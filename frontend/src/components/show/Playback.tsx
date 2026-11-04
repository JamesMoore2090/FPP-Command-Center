import { useEffect, useRef, useState } from "react";
import { api } from "../../api";
import { clock, stripExt } from "../../format";
import type { PlaylistDetail, Snapshot } from "../../types";
import ConfirmDialog from "../ConfirmDialog";

const Icon = ({ d, label }: { d: string; label: string }) => (
  <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true" focusable="false"><title>{label}</title><path d={d} fill="currentColor" /></svg>
);
const PREV = "M6 5h2v14H6zM20 5v14L9 12z";
const NEXT = "M16 5h2v14h-2zM4 5v14l11-7z";
const PAUSE = "M6 5h4v14H6zM14 5h4v14h-4z";
const PLAY = "M7 5v14l12-7z";

interface Lists { playlists: string[]; playable: string[]; sequences: string[] }

export default function Playback({ snap, notify }: { snap: Snapshot; notify: (ok: boolean, text: string) => void }) {
  const b = snap.banner;
  const player = snap.hosts.find((h) => h.id === b.player_id);
  const status = player?.status.status ?? b.status;
  const playing = status === "playing";
  const paused = status === "paused";
  const [lists, setLists] = useState<Lists | null>(null);
  const [choice, setChoice] = useState("");
  const [repeat, setRepeat] = useState(false);
  const [detail, setDetail] = useState<PlaylistDetail | null>(null);
  const [volume, setVolume] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [confirm, setConfirm] = useState<null | { title: string; message: string; label: string; danger?: boolean; run: () => Promise<void> }>(null);
  const volTimer = useRef<number>();

  useEffect(() => {
    api<Lists>("/api/show/playlists").then((l) => { setLists(l); setChoice((c) => c || l.playlists[0] || ""); }).catch((e) => notify(false, e.message));
    api<{ volume: number }>("/api/show/volume").then((v) => setVolume(v.volume)).catch(() => {});
  }, [notify]);
  useEffect(() => {
    setDetail(null);
    if (choice && lists?.playlists.includes(choice)) {
      api<PlaylistDetail>(`/api/show/playlists/${encodeURIComponent(choice)}`).then(setDetail).catch(() => {});
    }
  }, [choice, lists]);

  const act = async (label: string, path: string, body: object = {}) => {
    setBusy(true);
    try { await api(path, { method: "POST", body: JSON.stringify(body) }); notify(true, label); }
    catch (e) { notify(false, `${label} failed: ${(e as Error).message}`); }
    finally { setBusy(false); setConfirm(null); }
  };

  const start = () => {
    const run = () => act(`Started ${stripExt(choice)}`, "/api/show/start", { name: choice, repeat });
    if (playing || paused) {
      setConfirm({ title: `Replace what's playing?`, label: "Start", run,
        message: `${b.player_name} is playing ${stripExt(b.sequence) ?? b.playlist}. Starting ${stripExt(choice)} replaces it.` });
    } else run();
  };

  const onVolume = (v: number) => {
    setVolume(v);
    window.clearTimeout(volTimer.current);
    volTimer.current = window.setTimeout(() => {
      api("/api/show/volume", { method: "POST", body: JSON.stringify({ volume: v }) }).catch((e) => notify(false, `Volume: ${e.message}`));
    }, 300);
  };

  const total = (b.seconds_played ?? 0) + (b.seconds_remaining ?? 0);
  const playlistNames = lists?.playlists ?? [];
  const sequences = (lists?.playable ?? []).filter((p) => !playlistNames.includes(p));

  return (
    <div className="detail-grid">
      <section className="panel">
        <h2>Now playing <span className="muted small">on {b.player_name ?? "no player"}</span></h2>
        {!b.player_online ? <p className="bad-text">The player is offline.</p> : (
          <>
            <div className="now">
              <span className={`badge st-${status}`}>{status ?? "unknown"}</span>
              <b>{playing || paused ? stripExt(b.sequence) || b.playlist : "Nothing playing"}</b>
              {b.playlist && (playing || paused) && <span className="muted small">playlist {b.playlist}</span>}
            </div>
            {total > 0 && (playing || paused) && (
              <div className="progress">
                <span className="bar"><span style={{ width: `${(100 * (b.seconds_played ?? 0)) / total}%` }} /></span>
                <span className="num">{clock(b.seconds_played)} / {clock(total)}</span>
              </div>
            )}
            <div className="transport">
              <button disabled={busy || !(playing || paused)} onClick={() => act("Previous item", "/api/show/prev")} aria-label="Previous item" title="Previous item"><Icon d={PREV} label="Previous" /></button>
              {paused
                ? <button disabled={busy} onClick={() => act("Resumed", "/api/show/resume")}><Icon d={PLAY} label="" /> Resume</button>
                : <button disabled={busy || !playing} onClick={() => act("Paused", "/api/show/pause")}><Icon d={PAUSE} label="" /> Pause</button>}
              <button disabled={busy || !(playing || paused)} onClick={() => act("Next item", "/api/show/next")} aria-label="Next item" title="Next item"><Icon d={NEXT} label="Next" /></button>
              <span className="grow" />
              <button disabled={busy || !(playing || paused)} onClick={() => setConfirm({
                title: "Stop the show after this sequence?", label: "Stop gracefully",
                message: "The current sequence finishes, then playback stops.", run: () => act("Stopping after this sequence", "/api/show/stop", { mode: "graceful" }) })}>
                Stop gracefully</button>
              <button className="danger" disabled={busy || !(playing || paused)} onClick={() => setConfirm({
                title: "Stop the show now?", label: "Stop now", danger: true,
                message: "Lights and audio stop immediately.", run: () => act("Stopped", "/api/show/stop", { mode: "now" }) })}>
                Stop now</button>
            </div>
            <label className="volume">Volume
              <input type="range" min={0} max={100} value={volume ?? 0} disabled={volume == null} onChange={(e) => onVolume(Number(e.target.value))} />
              <span className="num">{volume ?? "—"}%</span>
            </label>
          </>
        )}
      </section>

      <section className="panel">
        <h2>Start</h2>
        <div className="toolbar">
          <label className="grow">Playlist or sequence
            <select value={choice} onChange={(e) => setChoice(e.target.value)}>
              <optgroup label="Playlists">{playlistNames.map((p) => <option key={p} value={p}>{p}</option>)}</optgroup>
              <optgroup label="Sequences">{sequences.map((p) => <option key={p} value={p}>{stripExt(p)}</option>)}</optgroup>
            </select>
          </label>
          <label className="check"><input type="checkbox" checked={repeat} onChange={(e) => setRepeat(e.target.checked)} /> Repeat</label>
          <button className="primary" disabled={busy || !choice || !b.player_online} onClick={start}>Start</button>
        </div>
        {detail && (
          <div className="playlist-preview">
            <p className="muted small">{detail.items.length} item{detail.items.length === 1 ? "" : "s"}
              {detail.total_duration ? ` · ${clock(detail.total_duration)}` : ""}{detail.random ? " · random order" : ""}</p>
            {detail.items.length === 0 ? <p className="warn-text">This playlist is empty.</p> : (
              <ol>{detail.items.map((it, i) => (
                <li key={i} className={it.enabled ? "" : "muted"}>{stripExt(it.name)}
                  {it.section !== "mainPlaylist" && <span className="muted small"> ({it.section === "leadIn" ? "lead in" : "lead out"})</span>}
                  {it.duration ? <span className="muted small num"> {clock(it.duration)}</span> : null}</li>
              ))}</ol>
            )}
          </div>
        )}
      </section>

      <ConfirmDialog open={!!confirm} title={confirm?.title ?? ""} message={<p>{confirm?.message}</p>}
        confirmLabel={confirm?.label} danger={confirm?.danger} busy={busy}
        onCancel={() => setConfirm(null)} onConfirm={() => confirm?.run()} />
    </div>
  );
}
