import { useCallback, useEffect, useState } from "react";
import { api } from "../../api";
import type { CommandArg, HostResult, Snapshot } from "../../types";
import ConfirmDialog from "../ConfirmDialog";

export default function TestMode({ snap, notify }: { snap: Snapshot; notify: (ok: boolean, text: string) => void }) {
  const hosts = snap.hosts.filter((h) => h.kind === "fpp" && h.status.online);
  const [selected, setSelected] = useState<number[]>(() => hosts.map((h) => h.id));
  const [patterns, setPatterns] = useState<string[]>([]);
  const [pattern, setPattern] = useState("RGB Chase");
  const [spec, setSpec] = useState<CommandArg[]>([]);
  const [args, setArgs] = useState<string[]>([]);
  const [interval, setIntervalMs] = useState(1000);
  const [state, setState] = useState<Record<number, string>>({});
  const [busy, setBusy] = useState(false);
  const [confirm, setConfirm] = useState(false);
  const [results, setResults] = useState<HostResult[] | null>(null);

  const loadState = useCallback(() => {
    api<HostResult[]>("/api/show/test").then((r) => setState(Object.fromEntries(r.map((x) => [x.host_id, x.ok ? x.message : "?"])))).catch(() => {});
  }, []);
  useEffect(() => {
    loadState();
    const t = window.setInterval(loadState, 5000);
    return () => window.clearInterval(t);
  }, [loadState]);
  useEffect(() => { api<{ patterns: string[] }>("/api/show/test/patterns").then((p) => setPatterns(p.patterns)).catch(() => {}); }, []);
  useEffect(() => {
    api<{ args: CommandArg[] }>(`/api/show/test/patterns/${encodeURIComponent(pattern)}`).then((r) => {
      setSpec(r.args);
      // channel ranges differ per controller, so leave them blank (= each controller's own range)
      setArgs(r.args.map((a) => (a.name === "ChannelRange" || !a.contents?.length ? "" : a.contents[0])));
    }).catch(() => { setSpec([]); setArgs([]); });
  }, [pattern]);

  const run = async (path: string, body: object, label: string) => {
    setBusy(true);
    try {
      const r = await api<HostResult[]>(path, { method: "POST", body: JSON.stringify(body) });
      setResults(r);
      const bad = r.filter((x) => !x.ok);
      notify(bad.length === 0, bad.length ? `${label}: failed on ${bad.map((x) => x.host).join(", ")}` : `${label} on ${r.length} controller${r.length === 1 ? "" : "s"}`);
    } catch (e) {
      notify(false, `${label}: ${(e as Error).message}`);
    } finally {
      setBusy(false);
      setConfirm(false);
      loadState();
    }
  };

  const testing = hosts.filter((h) => state[h.id] === "on");
  const playing = snap.banner.status === "playing";

  return (
    <div className="detail-grid">
      <section className="panel">
        <h2>Test pattern</h2>
        <div className="form-grid">
          <label>Pattern
            <select value={pattern} onChange={(e) => setPattern(e.target.value)}>{patterns.map((p) => <option key={p}>{p}</option>)}</select>
          </label>
          <label>Step (ms)<input type="number" min={100} max={10000} step={100} value={interval} onChange={(e) => setIntervalMs(Number(e.target.value))} /></label>
          {spec.map((a, i) => (
            <label key={a.name}>{a.description || a.name}
              {a.contents?.length && a.name !== "ChannelRange"
                ? <select value={args[i]} onChange={(e) => setArgs((v) => v.map((x, j) => (j === i ? e.target.value : x)))}>{a.contents.map((c) => <option key={c}>{c}</option>)}</select>
                : <input value={args[i] ?? ""} placeholder={a.name === "ChannelRange" ? "each controller's own channels" : ""}
                    onChange={(e) => setArgs((v) => v.map((x, j) => (j === i ? e.target.value : x)))} />}
            </label>
          ))}
        </div>
        <h3 className="sub">Controllers</h3>
        <div className="host-checks">
          {hosts.map((h) => (
            <label key={h.id} className="check">
              <input type="checkbox" checked={selected.includes(h.id)} onChange={(e) => setSelected((s) => (e.target.checked ? [...s, h.id] : s.filter((x) => x !== h.id)))} />
              {h.name} {state[h.id] === "on" && <span className="badge sync-drift">testing</span>}
            </label>
          ))}
        </div>
        <div className="dialog-actions">
          <button onClick={() => setSelected(selected.length === hosts.length ? [] : hosts.map((h) => h.id))}>{selected.length === hosts.length ? "Select none" : "Select all"}</button>
          <button className="primary" disabled={busy || selected.length === 0} onClick={() => setConfirm(true)}>Start test</button>
          <button className="danger" disabled={busy || testing.length === 0} onClick={() => run("/api/show/test/stop", { host_ids: testing.map((h) => h.id) }, "Test stopped")}>
            Stop test{testing.length ? ` (${testing.length})` : ""}</button>
        </div>
        {results && results.some((r) => !r.ok) && (
          <ul className="plain-list small">{results.filter((r) => !r.ok).map((r) => <li key={r.host_id} className="bad-text">{r.host}: {r.message}</li>)}</ul>
        )}
      </section>

      <ConfirmDialog open={confirm} title={`Start ${pattern} on ${selected.length} controller${selected.length === 1 ? "" : "s"}?`} busy={busy}
        danger={playing} confirmLabel="Start test"
        message={<>
          <p>The test pattern overrides whatever those controllers are showing until you stop it.</p>
          {playing && <p className="warn-text">The show is playing right now.</p>}
        </>}
        onCancel={() => setConfirm(false)}
        onConfirm={() => run("/api/show/test/start", { pattern, interval_ms: interval, args, host_ids: selected }, `${pattern} started`)} />
    </div>
  );
}
