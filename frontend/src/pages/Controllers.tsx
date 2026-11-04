import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import ConfirmDialog from "../components/ConfirmDialog";
import { ago } from "../format";
import type { DiscoveryResult, HostView, Snapshot } from "../types";

const KINDS = ["", "fpp", "falcon"];

export default function Controllers({ snap }: { snap: Snapshot | null }) {
  const [hosts, setHosts] = useState<HostView[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [adding, setAdding] = useState({ address: "", name: "", kind: "", notes: "" });
  const [busy, setBusy] = useState(false);
  const [editing, setEditing] = useState<number | null>(null);
  const [draft, setDraft] = useState({ name: "", address: "", notes: "" });
  const [toDelete, setToDelete] = useState<HostView | null>(null);
  const [disc, setDisc] = useState<DiscoveryResult | null>(null);
  const [discovering, setDiscovering] = useState(false);

  const load = useCallback(() => api<HostView[]>("/api/hosts").then(setHosts).catch((e) => setError(e.message)), []);
  useEffect(() => { load(); }, [load, snap?.ts]);
  useEffect(() => { api<DiscoveryResult | null>("/api/discovery/last").then(setDisc).catch(() => {}); }, []);

  const run = async (fn: () => Promise<unknown>) => {
    setError(null);
    setBusy(true);
    try { await fn(); await load(); } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  };

  const add = (e: React.FormEvent) => {
    e.preventDefault();
    run(async () => {
      await api("/api/hosts", { method: "POST", body: JSON.stringify({
        address: adding.address, name: adding.name || null, kind: adding.kind || null, notes: adding.notes || null,
      }) });
      setAdding({ address: "", name: "", kind: "", notes: "" });
    });
  };

  const save = (id: number) => run(async () => {
    await api(`/api/hosts/${id}`, { method: "PATCH", body: JSON.stringify({ ...draft, notes: draft.notes || null }) });
    setEditing(null);
  });

  const discover = async () => {
    setError(null);
    setDiscovering(true);
    try { setDisc(await api<DiscoveryResult>("/api/discovery/run", { method: "POST" })); await load(); }
    catch (e) { setError((e as Error).message); }
    finally { setDiscovering(false); }
  };

  return (
    <>
      <div className="page-head">
        <h1>Controllers</h1>
        <button className="primary" onClick={discover} disabled={discovering}>
          {discovering ? "Discovering… (≈30s)" : "Run discovery"}
        </button>
      </div>
      {error && <div className="alert critical"><span className="sev">error</span>{error}</div>}

      <div className="table-wrap">
        <table className="hosts">
          <thead>
            <tr><th></th><th>Name</th><th>Address</th><th>Type</th><th>Role</th><th>Model</th><th>Source</th><th>Last seen</th><th>Notes</th><th></th></tr>
          </thead>
          <tbody>
            {hosts.map((h) => editing === h.id ? (
              <tr key={h.id} className="editing">
                <td />
                <td><input value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} aria-label="Name" /></td>
                <td><input value={draft.address} onChange={(e) => setDraft({ ...draft, address: e.target.value })} aria-label="Address" /></td>
                <td>{h.kind}</td><td>{h.role ?? "—"}</td><td className="model">{h.model}</td><td>{h.source}</td><td />
                <td><input value={draft.notes} onChange={(e) => setDraft({ ...draft, notes: e.target.value })} aria-label="Notes" /></td>
                <td><div className="actions">
                  <button className="primary" disabled={busy} onClick={() => save(h.id)}>Save</button>
                  <button onClick={() => setEditing(null)}>Cancel</button>
                </div></td>
              </tr>
            ) : (
              <tr key={h.id} className={h.enabled ? "" : "disabled"}>
                <td><span className={`dot ${!h.enabled ? "off" : h.status.online ? "ok" : "bad"}`} /></td>
                <td><Link to={`/hosts/${h.id}`}>{h.name}</Link>{h.hostname && h.hostname !== h.name && <small className="muted"> {h.hostname}</small>}</td>
                <td><a href={`http://${h.address}/`} target="_blank" rel="noreferrer">{h.address}</a></td>
                <td>{h.kind}</td>
                <td>{h.role ?? "—"}</td>
                <td className="model">{h.model ?? "—"}</td>
                <td>{h.source}</td>
                <td>{h.status.online ? "now" : ago(h.last_seen)}</td>
                <td className="notes">{h.notes}</td>
                <td><div className="actions">
                  <button onClick={() => { setEditing(h.id); setDraft({ name: h.name, address: h.address, notes: h.notes ?? "" }); }}>Edit</button>
                  <button disabled={busy} onClick={() => run(() => api(`/api/hosts/${h.id}`, { method: "PATCH", body: JSON.stringify({ enabled: !h.enabled }) }))}>
                    {h.enabled ? "Disable" : "Enable"}
                  </button>
                  <button className="danger" onClick={() => setToDelete(h)}>Remove</button>
                </div></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <form className="add-form" onSubmit={add}>
        <h2>Add controller</h2>
        <label>Address<input required placeholder="10.100.100.55" value={adding.address} onChange={(e) => setAdding({ ...adding, address: e.target.value })} /></label>
        <label>Name<input placeholder="auto-detect" value={adding.name} onChange={(e) => setAdding({ ...adding, name: e.target.value })} /></label>
        <label>Type
          <select value={adding.kind} onChange={(e) => setAdding({ ...adding, kind: e.target.value })}>
            {KINDS.map((k) => <option key={k} value={k}>{k || "auto-detect"}</option>)}
          </select>
        </label>
        <label>Notes<input value={adding.notes} onChange={(e) => setAdding({ ...adding, notes: e.target.value })} /></label>
        <button className="primary" disabled={busy || !adding.address}>Add</button>
        <p className="muted hint">Type is detected automatically when the controller is reachable. Pick one to add an offline controller.</p>
      </form>

      {disc && (
        <section className="discovery">
          <h2>Last discovery <small className="muted">{new Date(disc.finished_at).toLocaleString()} · {disc.added} added · {disc.moved} moved</small></h2>
          {disc.errors.length > 0 && <p className="warn-text">{disc.errors.join("; ")}</p>}
          <ul>
            {disc.found.map((f) => (
              <li key={f.address}>
                <span className={`badge act-${f.action}`}>{f.action}</span> {f.hostname ?? "?"} <span className="muted">{f.address} · {f.kind} · {f.mode ?? ""} · via {f.via}</span>
              </li>
            ))}
          </ul>
        </section>
      )}

      <ConfirmDialog
        open={!!toDelete}
        title={`Remove ${toDelete?.name}?`}
        message={<>
          <p>It stops being monitored and its history is deleted.</p>
          {toDelete?.source === "discovered" && <p className="warn-text">Discovery will add it back next time it runs. To hide it permanently, use <b>Disable</b> instead.</p>}
        </>}
        confirmLabel="Remove"
        danger
        busy={busy}
        onCancel={() => setToDelete(null)}
        onConfirm={() => run(async () => { await api(`/api/hosts/${toDelete!.id}`, { method: "DELETE" }); setToDelete(null); })}
      />
    </>
  );
}
