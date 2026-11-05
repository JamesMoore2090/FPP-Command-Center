import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "../api";
import ConfirmDialog from "../components/ConfirmDialog";
import JobPanel from "../components/JobPanel";
import { bytes, stripExt } from "../format";
import type { ContentOverview, ContentRemote, JobSummary, Snapshot } from "../types";

type Push = { remotes: ContentRemote[]; files: string[] | null; overwrite: boolean; title: string };

export default function Content({ snap }: { snap: Snapshot | null }) {
  const [data, setData] = useState<ContentOverview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [jobId, setJobId] = useState<string | null>(null);
  const [push, setPush] = useState<Push | null>(null);
  const [del, setDel] = useState<ContentRemote | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<{ ok: boolean; text: string } | null>(null);
  const [filter, setFilter] = useState("");
  const [onlyGaps, setOnlyGaps] = useState(false);

  const load = useCallback(() => {
    setLoading(true);
    api<ContentOverview>("/api/content").then((d) => { setData(d); setError(null); })
      .catch((e) => setError((e as Error).message)).finally(() => setLoading(false));
  }, []);
  useEffect(() => {
    load();
    api<JobSummary[]>("/api/jobs").then((js) => {
      const running = js.find((j) => j.kind === "content_push" && j.status === "running");
      if (running) setJobId(running.id);
    }).catch(() => {});
  }, [load]);

  const playing = snap?.banner.status === "playing";
  const users = data?.remotes.filter((r) => r.uses_sequences) ?? [];
  const others = data?.remotes.filter((r) => !r.uses_sequences) ?? [];
  const sizes = useMemo(() => Object.fromEntries((data?.sequences ?? []).map((s) => [s.name, s.size ?? 0])), [data]);
  const missingTotal = users.reduce((n, r) => n + r.missing.length, 0);

  const planBytes = (p: Push) => p.remotes.reduce((n, r) => n + (p.files ?? r.missing)
    .filter((f) => p.overwrite || !(f in r.files)).reduce((m, f) => m + (sizes[f] ?? 0), 0), 0);
  const planCount = (p: Push) => p.remotes.reduce((n, r) => n + (p.files ?? r.missing).filter((f) => p.overwrite || !(f in r.files)).length, 0);

  const startPush = async (p: Push) => {
    setBusy(true);
    try {
      const r = await api<{ job: JobSummary | null; message?: string }>("/api/content/push", {
        method: "POST",
        body: JSON.stringify({ host_ids: p.remotes.map((x) => x.id), files: p.files, overwrite: p.overwrite, confirm: true, force: playing }),
      });
      if (r.job) setJobId(r.job.id);
      else setNotice({ ok: true, text: r.message ?? "Nothing to copy" });
    } catch (e) {
      setNotice({ ok: false, text: (e as Error).message });
    } finally {
      setBusy(false); setPush(null);
    }
  };

  const deleteExtras = async (r: ContentRemote) => {
    setBusy(true);
    try {
      const res = await api<{ file: string; ok: boolean; message: string | null }[]>("/api/content/delete", {
        method: "POST", body: JSON.stringify({ host_id: r.id, files: r.extra, confirm: true }) });
      const bad = res.filter((x) => !x.ok);
      setNotice({ ok: !bad.length, text: bad.length ? `Could not delete ${bad.map((b) => b.file).join(", ")} on ${r.name}` : `Deleted ${res.length} files from ${r.name}` });
      load();
    } catch (e) {
      setNotice({ ok: false, text: (e as Error).message });
    } finally {
      setBusy(false); setDel(null);
    }
  };

  const shown = (data?.sequences ?? []).filter((s) => s.name.toLowerCase().includes(filter.toLowerCase()))
    .filter((s) => !onlyGaps || users.some((r) => !(s.name in r.files)));

  return (
    <>
      <div className="page-head">
        <h1>Content</h1>
        <div className="actions">
          <button onClick={load} disabled={loading}>{loading ? "Checking…" : "Re-check"}</button>
          <button className="primary" disabled={!missingTotal || busy || !!jobId}
            onClick={() => setPush({ remotes: users.filter((r) => r.missing.length), files: null, overwrite: false, title: "Copy every missing sequence" })}>
            Copy missing{missingTotal ? ` (${missingTotal})` : ""}
          </button>
        </div>
      </div>
      {notice && (
        <div className={`alert ${notice.ok ? "info" : "critical"}`} role="status" onClick={() => setNotice(null)}>
          <span className="sev">{notice.ok ? "done" : "error"}</span>{notice.text}
        </div>
      )}
      {jobId && <JobPanel jobId={jobId} onDone={() => load()} onClose={() => setJobId(null)} />}
      {error && <p className="bad-text">{error}</p>}
      {!data && !error && <p className="muted">Comparing files across the fleet…</p>}

      {data && (
        <>
          <section className="panel">
            <div className="panel-head">
              <h2>Missing files</h2>
              <span className="muted small">
                Checked against every playlist on {data.player.name}
                {data.scheduled_playlists.length > 0 && <> · scheduled: {data.scheduled_playlists.join(", ")}</>}
              </span>
            </div>
            {data.problems.length === 0
              ? <p className="ok-text">Every file the playlists use is where it needs to be.</p>
              : (
                <div className="alerts">
                  {data.problems.map((p, i) => (
                    <div key={i} className={`alert ${p.severity}`}>
                      <span className="sev">{p.severity === "critical" ? "scheduled" : "warning"}</span>
                      <span>{p.message}</span>
                    </div>
                  ))}
                </div>
              )}
          </section>

          <section className="panel">
            <div className="panel-head">
              <h2>Remotes</h2>
              <span className="muted small">Sizes aren't compared: xLights' FPP Connect sends each remote a trimmed copy.</span>
            </div>
            <div className="table-scroll">
              <table className="plain">
                <thead><tr><th>Controller</th><th className="r">Sequences</th><th>Missing</th><th>Not on player</th><th></th></tr></thead>
                <tbody>
                  {data.remotes.map((r) => (
                    <tr key={r.id} className={r.uses_sequences ? "" : "muted"}>
                      <td>{r.name}</td>
                      <td className="r num">{r.error ? "—" : r.count}</td>
                      <td className="wrap">
                        {!r.online ? <span className="badge bad">offline</span>
                          : r.error ? <span className="bad-text small">{r.error}</span>
                          : !r.uses_sequences ? <span className="small">No sequences: fed over the network</span>
                          : r.missing.length ? <span className="warn-text">{r.missing.map(stripExt).join(", ")}</span>
                          : <span className="badge sync-in_sync">complete</span>}
                      </td>
                      <td className="wrap small">{r.extra.length ? <span title={r.extra.join("\n")}>{r.extra.length} file{r.extra.length === 1 ? "" : "s"}</span> : ""}</td>
                      <td>
                        <div className="actions">
                          {r.missing.length > 0 && (
                            <button className="small-btn" disabled={busy || !!jobId}
                              onClick={() => setPush({ remotes: [r], files: null, overwrite: false, title: `Copy missing sequences to ${r.name}` })}>
                              Copy missing</button>
                          )}
                          {r.extra.length > 0 && <button className="small-btn danger" disabled={busy} onClick={() => setDel(r)}>Delete extras…</button>}
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>

          <section className="panel">
            <div className="toolbar">
              <h2 className="grow">Sequences on {data.player.name} <span className="muted small">({data.sequences.length})</span></h2>
              <label className="check"><input type="checkbox" checked={onlyGaps} onChange={(e) => setOnlyGaps(e.target.checked)} /> Only gaps</label>
              <label>Filter<input value={filter} onChange={(e) => setFilter(e.target.value)} placeholder="name" /></label>
            </div>
            <div className="table-scroll">
              <table className="plain matrix">
                <thead>
                  <tr><th>Sequence</th><th className="r">Size</th><th>Used by</th>
                    {users.map((r) => <th key={r.id} className="c">{r.name}</th>)}<th></th></tr>
                </thead>
                <tbody>
                  {shown.map((s) => {
                    const lacking = users.filter((r) => !(s.name in r.files));
                    return (
                      <tr key={s.name}>
                        <td>{stripExt(s.name)}</td>
                        <td className="r num small">{bytes(s.size)}</td>
                        <td className="small wrap">{s.playlists.join(", ") || <span className="muted">no playlist</span>}</td>
                        {users.map((r) => (
                          <td key={r.id} className="c">
                            {s.name in r.files ? <span className="have" title={`${r.name}: ${bytes(r.files[s.name])}`}>✓</span>
                              : <span className="lack" title={`${r.name} doesn't have it`}>missing</span>}
                          </td>
                        ))}
                        <td>
                          {lacking.length > 0 && (
                            <div className="actions">
                              <button className="small-btn" disabled={busy || !!jobId}
                                onClick={() => setPush({ remotes: lacking, files: [s.name], overwrite: false,
                                  title: `Copy ${stripExt(s.name)} to ${lacking.map((r) => r.name).join(", ")}` })}>Copy</button>
                            </div>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            {others.length > 0 && <p className="hint muted">Not shown: {others.map((r) => r.name).join(", ")} (no sequences; they get channel data over the network).</p>}
          </section>

          <details className="panel">
            <summary><h2 className="inline">Audio and video on {data.player.name} <span className="muted small">({data.media.length})</span></h2></summary>
            <div className="table-scroll">
              <table className="plain">
                <thead><tr><th>File</th><th className="r">Size</th><th>Used by</th></tr></thead>
                <tbody>{data.media.map((m) => (
                  <tr key={m.name}><td>{m.name}</td><td className="r num small">{bytes(m.size)}</td>
                    <td className="small wrap">{m.playlists.join(", ") || <span className="muted">no playlist</span>}</td></tr>
                ))}</tbody>
              </table>
            </div>
          </details>
        </>
      )}

      <ConfirmDialog open={!!push} title={push?.title ?? ""} busy={busy} confirmLabel="Copy"
        danger={playing || push?.overwrite}
        message={push && <>
          <p>{planCount(push)} file{planCount(push) === 1 ? "" : "s"}, {bytes(planBytes(push))} in total, copied from {data?.player.name} to {push.remotes.map((r) => r.name).join(", ")}.</p>
          {push.overwrite && <p className="warn-text">This replaces the remotes' own copies (usually trimmed per controller by xLights) with the player's full file. That works, but it's bigger.</p>}
          {playing && <p className="warn-text">The show is playing. Copying large files can make remotes stutter.</p>}
          <p className="muted small">Each file is checked byte for byte before it's moved into place, so a failed copy never leaves a broken sequence.</p>
        </>}
        onCancel={() => setPush(null)} onConfirm={() => push && startPush(push)} />

      <ConfirmDialog open={!!del} title={`Delete ${del?.extra.length} sequences from ${del?.name}?`} busy={busy} danger confirmLabel="Delete"
        message={del && <>
          <p>These are on {del.name} but not on the player:</p>
          <ul className="plain-list small">{del.extra.map((f) => <li key={f}>{f}</li>)}</ul>
          <p className="muted small">Nothing on the player is touched. They aren't backed up (backups are configuration only).</p>
        </>}
        onCancel={() => setDel(null)} onConfirm={() => del && deleteExtras(del)} />
    </>
  );
}
