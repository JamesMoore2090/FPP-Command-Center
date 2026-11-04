import { useCallback, useEffect, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { api } from "../api";
import ApiConsole from "../components/ApiConsole";
import ConfirmDialog from "../components/ConfirmDialog";
import LogViewer from "../components/LogViewer";
import OutputsView from "../components/OutputsView";
import { bytes, duration, stripExt } from "../format";
import type { ActionSpec, HostDetail, HostView, Snapshot } from "../types";

const SYNC_LABEL: Record<string, string> = {
  in_sync: "In sync", drift: "Drifting", out_of_sync: "Out of sync", idle: "Idle", unknown: "Unknown",
};

export default function ControllerDetail({ snap }: { snap: Snapshot | null }) {
  const id = Number(useParams().id);
  const [params, setParams] = useSearchParams();
  const [fallback, setFallback] = useState<HostView | null>(null);
  const [detail, setDetail] = useState<HostDetail | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [pending, setPending] = useState<ActionSpec | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<{ kind: "ok" | "error"; text: string } | null>(null);

  // Live data comes from the WebSocket snapshot; disabled hosts aren't in it, so fetch those once.
  const live = snap?.hosts.find((h) => h.id === id) ?? null;
  const host = live ?? fallback;
  useEffect(() => {
    if (snap && !live) api<HostView>(`/api/hosts/${id}`).then(setFallback).catch(() => setFallback(null));
  }, [id, snap, live]);

  const loadDetail = useCallback(() => {
    setLoading(true);
    setDetailError(null);
    api<HostDetail>(`/api/hosts/${id}/detail`)
      .then(setDetail)
      .catch((e) => setDetailError(e.message))
      .finally(() => setLoading(false));
  }, [id]);
  useEffect(() => { setDetail(null); loadDetail(); }, [loadDetail]);

  const runAction = async (a: ActionSpec) => {
    setBusy(true);
    try {
      const r = await api<{ message: string }>(`/api/hosts/${id}/actions/${a.id}`, { method: "POST" });
      setNotice({ kind: "ok", text: `${a.label}: ${r.message}` });
    } catch (e) {
      setNotice({ kind: "error", text: `${a.label} failed: ${(e as Error).message}` });
    } finally {
      setBusy(false);
      setPending(null);
    }
  };

  if (!snap) return <p className="muted">Connecting…</p>;
  if (!host) return <p className="muted">Controller not found. <Link to="/controllers">Back to controllers</Link></p>;

  const s = host.status;
  const tabs = [
    { id: "overview", label: "Overview" },
    { id: "outputs", label: "Outputs" },
    ...(detail?.has_logs || host.kind === "fpp" ? [{ id: "logs", label: "Logs" }] : []),
    { id: "console", label: "API console" },
  ];
  const tab = tabs.some((t) => t.id === params.get("tab")) ? params.get("tab")! : "overview";

  return (
    <>
      <div className="detail-head">
        <div>
          <div className="crumbs"><Link to="/">Dashboard</Link> / {host.name}</div>
          <h1>
            <span className={`dot ${!host.enabled ? "off" : s.online ? "ok" : "bad"}`} /> {host.name}
            <span className={`badge role-${host.role ?? "unknown"}`}>{host.role ?? host.kind}</span>
          </h1>
          <div className="muted">
            <a href={`http://${host.address}/`} target="_blank" rel="noreferrer">{host.address} ↗</a>
            {" · "}{s.model ?? host.model ?? host.kind}
            {!host.enabled && " · disabled"}
          </div>
        </div>
        <div className="action-bar">
          {(detail?.actions ?? []).map((a) => (
            <button key={a.id} className={a.danger ? "danger" : ""} disabled={busy || !s.online && a.id !== "start_fppd"}
              onClick={() => (a.confirm ? setPending(a) : runAction(a))}>
              {a.label}
            </button>
          ))}
        </div>
      </div>

      {notice && (
        <div className={`alert ${notice.kind === "ok" ? "info" : "critical"}`} onClick={() => setNotice(null)} role="status">
          <span className="sev">{notice.kind === "ok" ? "done" : "error"}</span>{notice.text}
        </div>
      )}

      <nav className="tabs" role="tablist">
        {tabs.map((t) => (
          <button key={t.id} role="tab" aria-selected={tab === t.id} className={tab === t.id ? "active" : ""}
            onClick={() => setParams(t.id === "overview" ? {} : { tab: t.id }, { replace: true })}>
            {t.label}
          </button>
        ))}
      </nav>

      {tab === "overview" && (
        <div className="detail-grid">
          <section className="panel">
            <h2>Live status</h2>
            {!s.online ? (
              <p className="bad-text">Offline{s.error ? `: ${s.error}` : ""}</p>
            ) : (
              <dl className="kv">
                <dt>Version</dt><dd>{host.kind === "falcon" ? "Firmware " : "FPP "}{s.version ?? "—"}</dd>
                {host.kind === "fpp" && <><dt>Status</dt><dd><span className={`badge st-${s.status}`}>{s.status}</span>
                  {s.current_sequence && <> {stripExt(s.current_sequence)}</>}</dd></>}
                {s.current_playlist && <><dt>Playlist</dt><dd>{s.current_playlist}</dd></>}
                {host.sync && <><dt>Sync</dt><dd><span className={`badge sync-${host.sync}`}>{SYNC_LABEL[host.sync]}</span></dd></>}
                {s.fppd && <><dt>fppd</dt><dd className={s.fppd === "running" ? "" : "bad-text"}>{s.fppd}</dd></>}
                {s.cpu_pct != null && <><dt>CPU</dt><dd>{s.cpu_pct.toFixed(0)}%</dd></>}
                {s.mem_pct != null && <><dt>Memory</dt><dd>{s.mem_pct.toFixed(0)}%</dd></>}
                {s.temps.map((t) => <Kv key={t.label} k={`Temp ${t.label}`} v={`${t.value.toFixed(1)} °C`} />)}
                {s.voltages.map((v) => <Kv key={v.label} k={`Voltage ${v.label}`} v={`${v.value} V`} />)}
                {s.volume != null && <><dt>Volume</dt><dd>{s.volume}%</dd></>}
                <dt>Uptime</dt><dd>{duration(s.uptime_s)}</dd>
                {s.disk_total != null && <><dt>Disk</dt><dd>{bytes(s.disk_free)} free of {bytes(s.disk_total)} ({host.disk_pct}% used)</dd></>}
              </dl>
            )}
          </section>

          <section className="panel">
            <div className="panel-head">
              <h2>System</h2>
              <button onClick={loadDetail} disabled={loading}>{loading ? "Loading…" : "Refresh"}</button>
            </div>
            {detailError && <p className="bad-text">{detailError}</p>}
            {detail && (
              <dl className="kv">
                {Object.entries(detail.info).filter(([, v]) => v != null && v !== "").map(([k, v]) => <Kv key={k} k={k} v={String(v)} />)}
              </dl>
            )}
            {!detail && !detailError && <p className="muted">Loading…</p>}
          </section>

          {detail && detail.interfaces.length > 0 && (
            <section className="panel">
              <h2>Network</h2>
              <table className="plain">
                <thead><tr><th>Interface</th><th>State</th><th>Address</th><th>MAC</th></tr></thead>
                <tbody>{detail.interfaces.map((i) => (
                  <tr key={i.name}><td>{i.name}</td><td className={i.state === "UP" ? "" : "muted"}>{i.state}</td>
                    <td>{i.addresses ?? "—"}</td><td className="mono muted">{i.mac}</td></tr>
                ))}</tbody>
              </table>
            </section>
          )}

          {detail && (detail.warnings.length > 0 || detail.errors.length > 0) && (
            <section className="panel">
              <h2>Warnings</h2>
              <ul className="plain-list">
                {detail.warnings.map((w, i) => <li key={i} className="warn-text">{w}</li>)}
                {detail.errors.map((e, i) => <li key={`e${i}`} className="muted">Could not load {e}</li>)}
              </ul>
            </section>
          )}
        </div>
      )}

      {tab === "outputs" && (
        detail ? <OutputsView detail={detail} kind={host.kind} />
          : <p className={detailError ? "bad-text" : "muted"}>{detailError ?? "Loading…"}</p>
      )}
      {tab === "logs" && <LogViewer hostId={id} />}
      {tab === "console" && <ApiConsole host={host} />}

      <ConfirmDialog
        open={!!pending}
        title={`${pending?.label} on ${host.name}?`}
        message={<p>{pending?.confirm}</p>}
        confirmLabel={pending?.label}
        danger={pending?.danger}
        busy={busy}
        onCancel={() => setPending(null)}
        onConfirm={() => pending && runAction(pending)}
      />
    </>
  );
}

function Kv({ k, v }: { k: string; v: string }) {
  return <><dt>{k}</dt><dd>{v}</dd></>;
}
