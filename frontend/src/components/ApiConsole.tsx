import { useEffect, useMemo, useState } from "react";
import { api } from "../api";
import ConfirmDialog from "./ConfirmDialog";
import type { Endpoint, HostView } from "../types";

type Mode = "fpp" | "http";
interface Req { mode: Mode; method: string; path: string; body: string }
interface Res { status: number; ms: number; size: number; type: string; upstream: string | null; body: string; truncated: boolean }

const METHODS = ["GET", "POST", "PUT", "DELETE"];
const MAX_SHOW = 500_000;
const HISTORY_KEY = "fppcc.console.history";
const QUICK: Record<Mode, string[]> = {
  fpp: ["fppd/status", "system/info", "system/status", "fppd/multiSyncSystems", "playlists", "schedule", "fppd/warnings", "plugin"],
  http: ["status.xml", "strings.xml", "settings.xml"],
};

function loadHistory(): Req[] {
  try { return JSON.parse(localStorage.getItem(HISTORY_KEY) || "[]"); } catch { return []; }
}
function saveHistory(h: Req[]) {
  try { localStorage.setItem(HISTORY_KEY, JSON.stringify(h)); } catch { /* storage unavailable */ }
}

/** Sends any request to a controller through the backend proxy. Anything other than GET needs confirmation. */
export default function ApiConsole({ host }: { host: HostView }) {
  const isFpp = host.kind === "fpp";
  const [req, setReq] = useState<Req>({ mode: isFpp ? "fpp" : "http", method: "GET", path: isFpp ? "fppd/status" : "status.xml", body: "" });
  const [res, setRes] = useState<Res | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [catalog, setCatalog] = useState<Endpoint[]>([]);
  const [history, setHistory] = useState<Req[]>(loadHistory);

  useEffect(() => {
    if (isFpp) api<{ endpoints: Endpoint[] }>("/api/fpp/endpoints").then((c) => setCatalog(c.endpoints)).catch(() => {});
  }, [isFpp]);

  const suggestions = useMemo(
    () => (req.mode === "fpp" ? catalog.filter((e) => e.method === req.method).map((e) => e.path) : QUICK.http),
    [catalog, req.mode, req.method],
  );
  // In FPP mode the /api/ prefix is implied, so "api/fppd/status" and "/fppd/status" both work.
  const cleanPath = req.path.trim().replace(/^\/+/, "");
  const url = `/api/hosts/${host.id}/${req.mode}/${req.mode === "fpp" ? cleanPath.replace(/^api\//, "") : cleanPath}`;
  const placeholders = req.mode === "fpp" ? req.path.match(/:[A-Za-z]+|\*\*/g) : null;

  const send = async () => {
    setConfirming(false);
    setSending(true);
    setError(null);
    const t0 = performance.now();
    try {
      let headers: Record<string, string> | undefined;
      let body: string | undefined;
      if (req.method !== "GET" && req.body.trim()) {
        body = req.body;
        let json = true;
        try { JSON.parse(body); } catch { json = false; }
        headers = { "Content-Type": json ? "application/json" : "text/plain" };
      }
      const r = await fetch(url, { method: req.method, headers, body });
      const raw = await r.text();
      const type = r.headers.get("content-type") ?? "";
      let pretty = raw;
      if (type.includes("json") || /^\s*[[{]/.test(raw)) {
        try { pretty = JSON.stringify(JSON.parse(raw), null, 2); } catch { /* not JSON after all */ }
      }
      setRes({
        status: r.status, ms: Math.round(performance.now() - t0), size: raw.length, type,
        upstream: r.headers.get("x-upstream-url"), body: pretty.slice(0, MAX_SHOW), truncated: pretty.length > MAX_SHOW,
      });
      const h = [req, ...history.filter((x) => !(x.mode === req.mode && x.method === req.method && x.path === req.path && x.body === req.body))].slice(0, 15);
      setHistory(h);
      saveHistory(h);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSending(false);
    }
  };

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    if (req.method === "GET") send(); else setConfirming(true);
  };

  return (
    <section className="panel console">
      <form className="console-form" onSubmit={submit}>
        {isFpp && (
          <select aria-label="Target" value={req.mode} onChange={(e) => setReq({ ...req, mode: e.target.value as Mode, path: "" })}>
            <option value="fpp">FPP API /api/</option>
            <option value="http">Raw HTTP /</option>
          </select>
        )}
        {!isFpp && <span className="prefix">http://{host.address}/</span>}
        <select aria-label="Method" value={req.method} onChange={(e) => setReq({ ...req, method: e.target.value })}>
          {METHODS.map((m) => <option key={m}>{m}</option>)}
        </select>
        <input aria-label="Path" className="grow mono" list="endpoint-list" value={req.path} placeholder={req.mode === "fpp" ? "fppd/status" : "status.xml"}
          onChange={(e) => setReq({ ...req, path: e.target.value })} />
        <datalist id="endpoint-list">{suggestions.map((p) => <option key={p} value={p} />)}</datalist>
        <button className="primary" disabled={sending || !req.path.trim() || !!placeholders}>{sending ? "Sending…" : "Send"}</button>
      </form>
      {placeholders && <p className="warn-text small">Replace {placeholders.join(", ")} in the path before sending.</p>}

      <div className="quick">
        {QUICK[req.mode].map((p) => (
          <button key={p} type="button" className="chip" onClick={() => setReq({ ...req, method: "GET", path: p, body: "" })}>{p}</button>
        ))}
      </div>

      {req.method !== "GET" && (
        <textarea className="mono body" rows={6} placeholder='Request body (JSON or text), e.g. {"command": "Stop Now"}'
          value={req.body} onChange={(e) => setReq({ ...req, body: e.target.value })} />
      )}

      {error && <p className="bad-text">{error}</p>}
      {res && (
        <div className="response">
          <div className="response-meta">
            <span className={`badge ${res.status < 300 ? "st-playing" : res.status < 500 ? "sync-drift" : "bad"}`}>{res.status}</span>
            <span>{res.ms} ms</span><span>{res.size.toLocaleString()} bytes</span><span className="muted">{res.type}</span>
            {res.upstream && <span className="muted mono small">{res.upstream}</span>}
            <button type="button" className="chip" onClick={() => navigator.clipboard?.writeText(res.body)}>Copy</button>
          </div>
          <pre className="log">{res.body}{res.truncated && "\n… (truncated)"}</pre>
        </div>
      )}

      {history.length > 0 && (
        <details className="history">
          <summary>History ({history.length})</summary>
          <ul>
            {history.map((h, i) => (
              <li key={i}><button type="button" className="link" onClick={() => setReq(isFpp ? h : { ...h, mode: "http" })}>
                <b>{h.method}</b> <span className="mono">{h.mode === "fpp" ? "/api/" : "/"}{h.path}</span>
              </button></li>
            ))}
          </ul>
        </details>
      )}

      <ConfirmDialog
        open={confirming}
        title={`Send ${req.method} to ${host.name}?`}
        message={<>
          <p className="mono small">{req.method} {req.mode === "fpp" ? "/api/" : "/"}{req.path}</p>
          <p>This can change settings or interrupt the show on this controller.</p>
        </>}
        confirmLabel={`Send ${req.method}`}
        danger
        onCancel={() => setConfirming(false)}
        onConfirm={send}
      />
    </section>
  );
}
