import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../api";
import BackupList from "../components/BackupList";
import { ago } from "../format";
import type { Backup, BackupStatus, HostView, Snapshot } from "../types";

export default function Backups({ snap }: { snap: Snapshot | null }) {
  const [status, setStatus] = useState<BackupStatus | null>(null);
  const [hosts, setHosts] = useState<HostView[]>([]);
  const [running, setRunning] = useState<number | "all" | null>(null);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);
  const [refresh, setRefresh] = useState(0);
  const [cmpA, setCmpA] = useState<number | "">("");
  const [cmpB, setCmpB] = useState<number | "">("");
  const navigate = useNavigate();

  const load = useCallback(() => {
    api<BackupStatus>("/api/backups/status").then(setStatus).catch(() => {});
    api<HostView[]>("/api/hosts").then(setHosts).catch(() => {});
  }, []);
  useEffect(() => { load(); }, [load, refresh]);
  // live online state for the host list comes from the snapshot
  const online = (id: number) => snap?.hosts.find((h) => h.id === id)?.status.online;

  const run = async (hostIds: number[] | null) => {
    setRunning(hostIds ? hostIds[0] : "all");
    setMessage(null);
    try {
      const r = await api<Backup[]>("/api/backups/run", { method: "POST", body: JSON.stringify(hostIds ? { host_ids: hostIds } : {}) });
      const ok = r.filter((b) => b.status === "ok").length;
      const bad = r.filter((b) => b.status !== "ok");
      setMessage({ ok: bad.length === 0, text: `${ok} of ${r.length} backed up` + (bad.length ? `. Failed: ${bad.map((b) => `${b.host_name} (${b.error})`).join(", ")}` : "") });
    } catch (e) {
      setMessage({ ok: false, text: (e as Error).message });
    } finally {
      setRunning(null);
      setRefresh((x) => x + 1);
    }
  };

  const compareHosts = () => {
    const a = status?.hosts.find((h) => h.host_id === cmpA)?.last_ok_id;
    const b = status?.hosts.find((h) => h.host_id === cmpB)?.last_ok_id;
    if (a && b) navigate(`/backups/compare?a=${a}&b=${b}`);
  };
  const withBackups = (status?.hosts ?? []).filter((h) => h.last_ok_id);
  const kindOf = (id: number | "") => status?.hosts.find((h) => h.host_id === id)?.kind;

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Backups</h1>
          {status && (
            <p className="muted small">
              {status.enabled ? <>Nightly at {status.schedule.replace("daily at ", "")} ({status.timezone})
                {status.next_run && <> · next {new Date(status.next_run).toLocaleString([], { weekday: "short", hour: "numeric", minute: "2-digit" })}</>}</>
                : "Nightly backups are turned off"}
              {" · "}keeps {status.retention.daily} daily, {status.retention.weekly} weekly, {status.retention.monthly} monthly · configuration only
            </p>
          )}
        </div>
        <button className="primary" onClick={() => run(null)} disabled={running !== null}>
          {running === "all" ? "Backing up…" : "Back up all now"}
        </button>
      </div>
      {message && <div className={`alert ${message.ok ? "info" : "critical"}`} role="status" onClick={() => setMessage(null)}>
        <span className="sev">{message.ok ? "done" : "problem"}</span>{message.text}</div>}

      <section className="panel">
        <div className="table-scroll">
          <table className="plain">
            <thead><tr><th></th><th>Controller</th><th>Last good backup</th><th className="r">Kept</th><th>Last attempt</th><th></th></tr></thead>
            <tbody>
              {(status?.hosts ?? []).map((h) => {
                const dot = !h.enabled ? "off" : h.last_status === "ok" ? "ok" : h.last_status === "failed" ? "bad" : "";
                return (
                  <tr key={h.host_id} className={h.enabled ? "" : "muted"}>
                    <td><span className={`dot ${dot}`} /></td>
                    <td><Link to={`/hosts/${h.host_id}?tab=backups`}>{h.host_name}</Link> <span className="muted small">{h.kind}</span></td>
                    <td>{h.last_ok_at ? <span title={new Date(h.last_ok_at).toLocaleString()}>{ago(h.last_ok_at)}</span> : <span className="muted">never</span>}</td>
                    <td className="r num">{h.count}</td>
                    <td className="small">{h.last_status && h.last_status !== "ok"
                      ? <span className={h.last_status === "failed" ? "bad-text" : "muted"}>{h.last_status}: {h.last_error}</span>
                      : h.last_at ? <span className="muted">ok</span> : ""}</td>
                    <td><div className="actions">
                      <button className="small-btn" disabled={running !== null || h.running || online(h.host_id) === false}
                        onClick={() => run([h.host_id])}>{running === h.host_id || h.running ? "Backing up…" : "Back up now"}</button>
                    </div></td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </section>

      <section className="panel compare-hosts">
        <h2>Compare two controllers</h2>
        <p className="muted small">Compares the latest backups of each, e.g. two remotes that should be set up alike.</p>
        <div className="toolbar">
          <select aria-label="First controller" value={cmpA} onChange={(e) => setCmpA(e.target.value ? Number(e.target.value) : "")}>
            <option value="">First controller…</option>
            {withBackups.map((h) => <option key={h.host_id} value={h.host_id}>{h.host_name}</option>)}
          </select>
          <select aria-label="Second controller" value={cmpB} onChange={(e) => setCmpB(e.target.value ? Number(e.target.value) : "")}>
            <option value="">Second controller…</option>
            {withBackups.filter((h) => h.host_id !== cmpA && (!cmpA || h.kind === kindOf(cmpA))).map((h) =>
              <option key={h.host_id} value={h.host_id}>{h.host_name}</option>)}
          </select>
          <button className="primary" disabled={!cmpA || !cmpB} onClick={compareHosts}>Compare</button>
        </div>
      </section>

      <h2 className="section-title">History</h2>
      <BackupList hosts={hosts} refreshKey={refresh} />
    </>
  );
}
