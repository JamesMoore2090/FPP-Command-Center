import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api } from "../api";
import ConfirmDialog from "../components/ConfirmDialog";
import JobPanel from "../components/JobPanel";
import { ago, bytes } from "../format";
import type { HostResult, JobSummary, OsImage, SettingsCompare, Snapshot, VersionRow, VersionsData } from "../types";

const TABS = [
  { id: "updates", label: "Versions & updates" },
  { id: "settings", label: "Settings compare" },
  { id: "jobs", label: "Job history" },
];

export default function Fleet({ snap }: { snap: Snapshot | null }) {
  const [params, setParams] = useSearchParams();
  const tab = TABS.some((t) => t.id === params.get("tab")) ? params.get("tab")! : "updates";
  return (
    <>
      <div className="page-head"><h1>Fleet</h1></div>
      <nav className="tabs" role="tablist">
        {TABS.map((t) => (
          <button key={t.id} role="tab" aria-selected={tab === t.id} className={tab === t.id ? "active" : ""}
            onClick={() => setParams(t.id === "updates" ? {} : { tab: t.id }, { replace: true })}>{t.label}</button>
        ))}
      </nav>
      {tab === "updates" && <Updates snap={snap} />}
      {tab === "settings" && <Settings />}
      {tab === "jobs" && <Jobs />}
    </>
  );
}

// ---------------------------------------------------------------- versions & updates
type Op = { kind: "update" | "upgrade" | "os" | "restart_fppd" | "reboot"; hosts: VersionRow[]; version?: string; image?: OsImage };

function statusOf(r: VersionRow): { text: string; cls: string } {
  if (!r.online) return { text: "offline", cls: "bad" };
  if (!r.updatable) return { text: "update manually", cls: "" };
  if (r.error) return { text: r.error, cls: "bad" };
  if (r.activity?.in_progress) return { text: `updating: ${r.activity.stage || r.activity.op || "…"}`, cls: "sync-drift" };
  if (r.upgrade) return { text: r.upgrade.major ? `FPP ${r.upgrade.version} (needs OS image)` : `FPP ${r.upgrade.version} available`, cls: "sync-drift" };
  if (r.update_available) return { text: "update available", cls: "sync-drift" };
  if (r.os_update) return { text: "newer OS image", cls: "sync-drift" };
  return { text: "up to date", cls: "sync-in_sync" };
}

function Updates({ snap }: { snap: Snapshot | null }) {
  const [data, setData] = useState<VersionsData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [withOs, setWithOs] = useState(false);
  const [selected, setSelected] = useState<number[]>([]);
  const [op, setOp] = useState<Op | null>(null);
  const [typed, setTyped] = useState("");
  const [busy, setBusy] = useState(false);
  const [jobId, setJobId] = useState<string | null>(null);
  const [notice, setNotice] = useState<{ ok: boolean; text: string } | null>(null);

  const load = useCallback((os = withOs) => {
    setLoading(true);
    api<VersionsData>(`/api/fleet/versions${os ? "?os=true" : ""}`).then((d) => {
      setData(d); setError(null);
      if (d.running_job) setJobId((cur) => cur ?? d.running_job!.id);
    }).catch((e) => setError((e as Error).message)).finally(() => setLoading(false));
  }, [withOs]);
  useEffect(() => { load(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const rows = data?.hosts ?? [];
  const fpp = rows.filter((r) => r.updatable && r.online && !r.error);
  const sel = fpp.filter((r) => selected.includes(r.id));
  const playing = snap?.banner.status === "playing";
  const platforms = new Set(sel.map((r) => `${r.platform}|${r.os_version?.includes("64") ? 64 : 32}`));
  const upgradeTo = sel.length && sel.every((r) => r.upgrade && !r.upgrade.major && r.upgrade.version === sel[0].upgrade?.version) ? sel[0].upgrade!.version : null;
  const osImages = platforms.size === 1 ? (sel[0]?.os_images ?? []) : [];

  const toggle = (id: number) => setSelected((s) => (s.includes(id) ? s.filter((x) => x !== id) : [...s, id]));
  const needsTyping = op?.kind === "os" || (op && op.hosts.length > 1 && op.kind !== "restart_fppd");
  const confirmWord = op?.kind === "os" ? "UPGRADE" : "YES";

  const run = async () => {
    if (!op) return;
    setBusy(true);
    try {
      if (op.kind === "restart_fppd" || op.kind === "reboot") {
        const r = await api<HostResult[]>(`/api/fleet/actions/${op.kind}`, { method: "POST", body: JSON.stringify({ host_ids: op.hosts.map((h) => h.id), confirm: true }) });
        const bad = r.filter((x) => !x.ok);
        setNotice({ ok: !bad.length, text: bad.length ? `Failed on ${bad.map((b) => b.host).join(", ")}` : `${op.kind === "reboot" ? "Rebooting" : "Restarted fppd on"} ${r.length} controller${r.length === 1 ? "" : "s"}` });
      } else {
        const r = await api<{ job: JobSummary }>("/api/fleet/update", {
          method: "POST",
          body: JSON.stringify({ op: op.kind, host_ids: op.hosts.map((h) => h.id), version: op.version, os_image: op.image?.filename,
            confirm: true, force: playing, stop_on_failure: true }),
        });
        setJobId(r.job.id);
      }
    } catch (e) {
      setNotice({ ok: false, text: (e as Error).message });
    } finally {
      setBusy(false); setOp(null); setTyped("");
    }
  };

  return (
    <>
      {notice && (
        <div className={`alert ${notice.ok ? "info" : "critical"}`} role="status" onClick={() => setNotice(null)}>
          <span className="sev">{notice.ok ? "done" : "error"}</span>{notice.text}
        </div>
      )}
      {jobId && <JobPanel jobId={jobId} onDone={() => load()} onClose={() => setJobId(null)} />}
      {error && <p className="bad-text">{error}</p>}

      <section className="panel">
        <div className="toolbar">
          <div className="grow">
            {data && (
              <span className="small">
                FPP {data.summary.versions.join(", ") || "—"} · OS {data.summary.os_versions.map((v) => v.replace(/^v/, "")).join(", ") || "—"}
                {data.summary.versions.length > 1 && <span className="warn-text"> · versions differ</span>}
              </span>
            )}
          </div>
          <label className="check"><input type="checkbox" checked={withOs} onChange={(e) => { setWithOs(e.target.checked); load(e.target.checked); }} /> Check OS images</label>
          <button onClick={() => load()} disabled={loading}>{loading ? "Checking…" : "Refresh"}</button>
        </div>
        {!data ? <p className="muted">Asking every controller…</p> : (
          <div className="table-scroll">
            <table className="plain versions">
              <thead>
                <tr>
                  <th><input type="checkbox" aria-label="Select all" checked={fpp.length > 0 && sel.length === fpp.length}
                    onChange={(e) => setSelected(e.target.checked ? fpp.map((r) => r.id) : [])} /></th>
                  <th>Controller</th><th>Hardware</th><th>FPP</th><th>Commit</th><th>OS image</th><th>Status</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => {
                  const st = statusOf(r);
                  const can = r.updatable && r.online && !r.error;
                  return (
                    <tr key={r.id} className={can ? "" : "muted"}>
                      <td>{can && <input type="checkbox" aria-label={`Select ${r.name}`} checked={selected.includes(r.id)} onChange={() => toggle(r.id)} />}</td>
                      <td><Link to={`/hosts/${r.id}`}>{r.name}</Link> {r.is_player && <span className="badge role-player">player</span>}</td>
                      <td className="small">{[r.platform, r.variant !== r.platform ? r.variant : null].filter(Boolean).join(" · ")}</td>
                      <td className="num">{r.version ?? "—"}{r.branch && r.branch !== `v${r.version}` && <span className="muted small"> ({r.branch})</span>}</td>
                      <td className="mono small" title={r.remote_commit ? `latest: ${r.remote_commit}` : ""}>{r.local_commit ?? ""}</td>
                      <td className="small">{r.os_version ?? ""}{r.os_update && <div className="warn-text">→ {r.os_update.filename}</div>}
                        {r.os_error && <div className="bad-text">{r.os_error}</div>}</td>
                      <td className="wrap"><span className={`badge ${st.cls}`} title={st.text}>{st.text}</span>
                        {!!r.plugin_updates?.length && <div className="small warn-text">Plugin updates: {r.plugin_updates.join(", ")}</div>}
                        {r.end_of_life && <div className="small warn-text">This FPP version is end of life</div>}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
        <div className="action-bar fleet-actions">
          <span className="muted small grow">{sel.length ? `${sel.length} selected` : "Select controllers to act on"}</span>
          <button disabled={!sel.length || busy} onClick={() => setOp({ kind: "restart_fppd", hosts: sel })}>Restart fppd</button>
          <button className="danger" disabled={!sel.length || busy} onClick={() => setOp({ kind: "reboot", hosts: sel })}>Reboot</button>
          <button className="primary" disabled={!sel.length || busy || !!jobId} title="Pull the latest fixes for the installed version and rebuild"
            onClick={() => setOp({ kind: "update", hosts: sel })}>Update FPP{sel.filter((r) => r.update_available).length ? ` (${sel.filter((r) => r.update_available).length} behind)` : ""}</button>
          {upgradeTo && <button className="primary" disabled={busy || !!jobId} onClick={() => setOp({ kind: "upgrade", hosts: sel, version: upgradeTo })}>Upgrade to {upgradeTo}</button>}
          <button disabled={!sel.length || busy || !!jobId || platforms.size !== 1 || !osImages.length}
            title={!withOs ? "Tick “Check OS images” first" : platforms.size > 1 ? "Select controllers of one platform" : ""}
            onClick={() => { const img = sel[0].os_update ?? osImages[0]; setOp({ kind: "os", hosts: sel, image: img }); }}>OS upgrade…</button>
        </div>
        <p className="hint muted">Updates run one controller at a time, remotes first and the player last. Each controller's configuration is backed up first, and the run stops at the first failure.</p>
      </section>

      <ConfirmDialog open={!!op} busy={busy}
        title={op ? ({ update: "Update FPP", upgrade: `Upgrade to FPP ${op.version}`, os: "Upgrade the OS", restart_fppd: "Restart fppd", reboot: "Reboot" }[op.kind] + ` on ${op.hosts.length} controller${op.hosts.length === 1 ? "" : "s"}?`) : ""}
        danger={op?.kind !== "restart_fppd" && op?.kind !== "update" || playing}
        confirmDisabled={!!needsTyping && typed.trim().toUpperCase() !== confirmWord}
        confirmLabel={op?.kind === "os" ? "Upgrade OS" : op?.kind === "reboot" ? "Reboot" : op?.kind === "restart_fppd" ? "Restart" : "Start"}
        message={op && <div className="op-confirm">
          <p>{op.hosts.map((h) => h.name).join(", ")}</p>
          {op.kind === "update" && <p>Pulls the latest fixes for FPP {op.hosts[0].version} and rebuilds. fppd restarts on each controller (a few minutes each).
            {op.hosts.some((h) => !h.update_available) && <span className="muted"> Controllers that are already current just rebuild.</span>}</p>}
          {op.kind === "upgrade" && <p>Switches to FPP {op.version}, then reboots each controller.</p>}
          {op.kind === "os" && <>
            <p>Replaces each controller's operating system with a new image (about {bytes(op.image?.size)} download each), then it reboots. This can take 20–30 minutes per controller.</p>
            <label className="confirm-type">Image
              <select value={op.image?.filename} onChange={(e) => setOp({ ...op, image: osImages.find((i) => i.filename === e.target.value) })}>
                {osImages.map((i) => <option key={i.filename} value={i.filename}>{i.filename}</option>)}
              </select>
            </label>
          </>}
          {op.kind === "reboot" && <p>Each controller is offline for about a minute.</p>}
          {op.kind === "restart_fppd" && <p>Output stops for a few seconds on each controller.</p>}
          {playing && <p className="warn-text">The show is playing right now.</p>}
          {op.hosts.some((h) => h.is_player) && op.kind !== "restart_fppd" && <p className="warn-text">Includes the player, {op.hosts.find((h) => h.is_player)?.name}.</p>}
          {needsTyping && (
            <label className="confirm-type">Type {confirmWord} to confirm
              <input value={typed} onChange={(e) => setTyped(e.target.value)} autoComplete="off" />
            </label>
          )}
        </div>}
        onCancel={() => { setOp(null); setTyped(""); }}
        onConfirm={run} />
    </>
  );
}

// ---------------------------------------------------------------- settings compare
function Settings() {
  const [data, setData] = useState<SettingsCompare | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showExpected, setShowExpected] = useState(false);
  const [showUnset, setShowUnset] = useState(false);
  const [filter, setFilter] = useState("");
  const [backingUp, setBackingUp] = useState(false);

  const load = useCallback(() => {
    api<SettingsCompare>("/api/fleet/settings").then((d) => { setData(d); setError(null); }).catch((e) => setError((e as Error).message));
  }, []);
  useEffect(() => { load(); }, [load]);

  const rows = useMemo(() => (data?.rows ?? []).filter((r) => (showExpected || !r.expected)
    && (showUnset || Object.values(r.values).every((v) => v !== null))
    && r.key.toLowerCase().includes(filter.toLowerCase())), [data, showExpected, showUnset, filter]);
  const cols = data?.hosts ?? [];

  const backupNow = async () => {
    setBackingUp(true);
    try { await api("/api/backups/run", { method: "POST", body: JSON.stringify({ host_ids: cols.map((c) => c.id) }) }); load(); }
    catch (e) { setError((e as Error).message); }
    finally { setBackingUp(false); }
  };

  return (
    <section className="panel">
      <div className="toolbar">
        <p className="grow muted small">From each controller's latest backup. Only settings that differ are listed; “—” means not set (FPP's default).</p>
        <label className="check"><input type="checkbox" checked={showExpected} onChange={(e) => setShowExpected(e.target.checked)} /> Per-controller settings</label>
        <label className="check"><input type="checkbox" checked={showUnset} onChange={(e) => setShowUnset(e.target.checked)} /> Set on only some</label>
        <label>Filter<input value={filter} onChange={(e) => setFilter(e.target.value)} placeholder="setting" /></label>
        <button onClick={backupNow} disabled={backingUp || !cols.length}>{backingUp ? "Backing up…" : "Back up now and refresh"}</button>
      </div>
      {error && <p className="bad-text">{error}</p>}
      {!data ? <p className="muted">Loading…</p> : (
        <div className="table-scroll">
          <table className="plain matrix settings-matrix">
            <thead>
              <tr><th>Setting</th>{cols.map((c) => (
                <th key={c.id} className="c" title={c.taken_at ? `Backup from ${new Date(c.taken_at).toLocaleString()}` : "no backup"}>
                  {c.name}<div className="muted small">{c.taken_at ? ago(c.taken_at) : "no backup"}</div>
                </th>))}</tr>
            </thead>
            <tbody>
              {rows.map((r) => {
                const vals = cols.map((c) => r.values[String(c.id)]);
                const counts = new Map<string, number>();
                vals.forEach((v) => counts.set(String(v), (counts.get(String(v)) ?? 0) + 1));
                const common = [...counts.entries()].sort((a, b) => b[1] - a[1])[0]?.[0];
                return (
                  <tr key={r.key} className={r.expected ? "muted" : ""}>
                    <td className="mono small" title={r.key}>{r.area === "settings" ? r.name : r.key}</td>
                    {cols.map((c, i) => {
                      const v = vals[i];
                      const odd = counts.size > 1 && v != null && String(v) !== common && !r.expected;
                      return <td key={c.id} className={`c small ${odd ? "odd" : ""}`} title={v ?? "not set"}>
                        {!(String(c.id) in r.values) ? "" : v == null ? <span className="muted">—</span> : <span className="cell">{v}</span>}</td>;
                    })}
                  </tr>
                );
              })}
              {rows.length === 0 && <tr><td colSpan={cols.length + 1} className="muted">No differences with these filters.</td></tr>}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

// ---------------------------------------------------------------- jobs
function Jobs() {
  const [jobs, setJobs] = useState<JobSummary[] | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  useEffect(() => {
    const load = () => api<JobSummary[]>("/api/jobs").then(setJobs).catch(() => setJobs([]));
    load();
    const t = window.setInterval(load, 5000);
    return () => window.clearInterval(t);
  }, []);
  if (!jobs) return <p className="muted">Loading…</p>;
  return (
    <>
      {open && <JobPanel jobId={open} onClose={() => setOpen(null)} />}
      <section className="panel">
        {jobs.length === 0 ? <p className="muted">No jobs since the service started.</p> : (
          <div className="table-scroll">
            <table className="plain">
              <thead><tr><th>Started</th><th>Job</th><th>Result</th><th className="r">Steps</th><th></th></tr></thead>
              <tbody>{jobs.map((j) => (
                <tr key={j.id}>
                  <td>{new Date(j.created_at).toLocaleString([], { dateStyle: "medium", timeStyle: "short" })}</td>
                  <td>{j.title}</td>
                  <td><span className={`badge ${j.status === "ok" ? "sync-in_sync" : j.status === "running" ? "sync-drift" : "bad"}`}>{j.status}</span></td>
                  <td className="r num small">{j.counts.ok}/{j.total} ok{j.counts.failed ? `, ${j.counts.failed} failed` : ""}</td>
                  <td><div className="actions"><button className="small-btn" onClick={() => setOpen(j.id)}>Details</button></div></td>
                </tr>
              ))}</tbody>
            </table>
          </div>
        )}
        <p className="hint muted">Jobs are kept in memory until the service restarts.</p>
      </section>
    </>
  );
}
