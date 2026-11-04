import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api";
import { bytes } from "../format";
import type { Backup, HostView } from "../types";
import ConfirmDialog from "./ConfirmDialog";
import RestoreDialog from "./RestoreDialog";

const TRIGGER: Record<string, string> = { scheduled: "Nightly", manual: "Manual", "pre-restore": "Before restore", "pre-change": "Before change" };

/** Backup history (all controllers, or one) with compare, download, pin, delete and restore. */
export default function BackupList({ hostId, hosts, refreshKey = 0 }: { hostId?: number; hosts: HostView[]; refreshKey?: number }) {
  const [rows, setRows] = useState<Backup[]>([]);
  const [filterHost, setFilterHost] = useState<number | "">(hostId ?? "");
  const [showFailed, setShowFailed] = useState(true);
  const [selected, setSelected] = useState<number[]>([]);
  const [toDelete, setToDelete] = useState<Backup | null>(null);
  const [toRestore, setToRestore] = useState<Backup | null>(null);
  const [error, setError] = useState<string | null>(null);
  const navigate = useNavigate();

  const load = useCallback(() => {
    const q = filterHost !== "" ? `?host_id=${filterHost}&limit=500` : "?limit=500";
    api<Backup[]>(`/api/backups${q}`).then(setRows).catch((e) => setError(e.message));
  }, [filterHost]);
  useEffect(() => { load(); }, [load, refreshKey]);

  const toggle = (id: number) =>
    setSelected((s) => (s.includes(id) ? s.filter((x) => x !== id) : [...s.slice(-1), id]));

  const pin = async (b: Backup) => {
    try { await api(`/api/backups/${b.id}`, { method: "PATCH", body: JSON.stringify({ pinned: !b.pinned }) }); load(); }
    catch (e) { setError((e as Error).message); }
  };

  const shown = rows.filter((b) => showFailed || b.status === "ok");
  const selectedRows = rows.filter((b) => selected.includes(b.id));
  const canCompare = selectedRows.length === 2 && selectedRows[0].kind === selectedRows[1].kind && selectedRows.every((b) => b.status === "ok");

  return (
    <section className="panel">
      <div className="toolbar">
        {hostId === undefined && (
          <label>Controller
            <select value={filterHost} onChange={(e) => { setFilterHost(e.target.value ? Number(e.target.value) : ""); setSelected([]); }}>
              <option value="">All controllers</option>
              {hosts.filter((h) => h.kind === "fpp" || h.kind === "falcon").map((h) => <option key={h.id} value={h.id}>{h.name}</option>)}
            </select>
          </label>
        )}
        <label className="check"><input type="checkbox" checked={showFailed} onChange={(e) => setShowFailed(e.target.checked)} /> Show failed / skipped</label>
        <span className="grow" />
        <span className="muted small">{selected.length ? `${selected.length} selected` : "Tick two backups to compare"}</span>
        <button className="primary" disabled={!canCompare}
          onClick={() => { const [a, b] = [...selectedRows].sort((x, y) => x.created_at.localeCompare(y.created_at)); navigate(`/backups/compare?a=${a.id}&b=${b.id}`); }}>
          Compare
        </button>
      </div>
      {error && <p className="bad-text">{error}</p>}
      {shown.length === 0 ? <p className="muted">No backups yet.</p> : (
        <div className="table-scroll">
          <table className="plain backups">
            <thead>
              <tr><th></th><th>When</th>{hostId === undefined && <th>Controller</th>}<th>Type</th><th>Result</th>
                <th>Version</th><th className="r">Size</th><th></th></tr>
            </thead>
            <tbody>
              {shown.map((b) => (
                <tr key={b.id} className={b.status === "ok" ? "" : "muted"}>
                  <td>{b.status === "ok" && <input type="checkbox" aria-label={`Select backup ${b.id}`} checked={selected.includes(b.id)} onChange={() => toggle(b.id)} />}</td>
                  <td title={b.created_at}>{new Date(b.created_at).toLocaleString([], { dateStyle: "medium", timeStyle: "short" })}
                    {b.pinned && <span className="pin" title="Pinned: kept forever"> ★</span>}</td>
                  {hostId === undefined && <td>{b.host_name}</td>}
                  <td>{TRIGGER[b.trigger] ?? b.trigger}</td>
                  <td>
                    {b.status === "ok"
                      ? (b.changed ? <span className="badge act-moved" title="Configuration differs from the previous backup">changed</span>
                        : <span className="badge">no changes</span>)
                      : <span className={`badge ${b.status === "failed" ? "bad" : ""}`} title={b.error ?? ""}>{b.status}</span>}
                    {b.error && <span className="small bad-text"> {b.error}</span>}
                    {b.note && <div className="small muted">{b.note}</div>}
                  </td>
                  <td className="small">{b.version ?? ""}</td>
                  <td className="r num small">{b.size ? bytes(b.size) : ""}</td>
                  <td>
                    {b.status === "ok" && (
                      <div className="actions">
                        <a className="button small-btn" href={`/api/backups/${b.id}/download`}>Download</a>
                        {b.kind === "fpp" && <button className="small-btn" onClick={() => setToRestore(b)}>Restore…</button>}
                        <button className="small-btn" onClick={() => pin(b)} title={b.pinned ? "Let retention remove it" : "Keep forever"}>{b.pinned ? "Unpin" : "Pin"}</button>
                        <button className="small-btn danger" onClick={() => setToDelete(b)}>Delete</button>
                      </div>
                    )}
                    {b.status !== "ok" && <div className="actions"><button className="small-btn" onClick={() => setToDelete(b)}>Dismiss</button></div>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <ConfirmDialog
        open={!!toDelete}
        title={toDelete?.status === "ok" ? "Delete this backup?" : "Dismiss this record?"}
        message={toDelete?.status === "ok"
          ? <p>{toDelete?.host_name}, {toDelete && new Date(toDelete.created_at).toLocaleString()}. The file is deleted from the VM.</p>
          : <p>Removes the record of this {toDelete?.status} backup attempt.</p>}
        confirmLabel="Delete"
        danger
        onCancel={() => setToDelete(null)}
        onConfirm={async () => {
          try { await api(`/api/backups/${toDelete!.id}`, { method: "DELETE" }); } catch (e) { setError((e as Error).message); }
          setToDelete(null); setSelected((s) => s.filter((x) => x !== toDelete!.id)); load();
        }}
      />
      {toRestore && <RestoreDialog backup={toRestore} hosts={hosts} onClose={() => { setToRestore(null); load(); }} />}
    </section>
  );
}
