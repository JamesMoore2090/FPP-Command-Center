import HostCard from "../components/HostCard";
import { clock, stripExt } from "../format";
import type { Snapshot } from "../types";

export default function Dashboard({ snap }: { snap: Snapshot | null }) {
  if (!snap) return <p className="muted">Connecting…</p>;
  const { banner: b, summary: sm, alerts } = snap;
  const playing = b.status === "playing";
  const total = (b.seconds_played ?? 0) + (b.seconds_remaining ?? 0);

  return (
    <>
      <section className={`banner ${playing ? "playing" : ""} ${b.player_id && !b.player_online ? "down" : ""}`}>
        <div className="banner-main">
          <div className="eyebrow">{b.player_name ?? "No player"} {b.player_id && !b.player_online && "· offline"}</div>
          {playing ? (
            <>
              <h2>{stripExt(b.sequence) || "Playing"}</h2>
              <div className="muted">{b.playlist ? `Playlist: ${b.playlist}` : ""}</div>
              {total > 0 && (
                <div className="progress">
                  <span className="bar"><span style={{ width: `${(100 * (b.seconds_played ?? 0)) / total}%` }} /></span>
                  <span className="num">{clock(b.seconds_played)} / {clock(total)}</span>
                </div>
              )}
            </>
          ) : (
            <h2 className="idle">{b.player_online ? (b.status ?? "Idle") : "Show offline"}</h2>
          )}
        </div>
        <div className="banner-stats">
          <div><b>{sm.online}<span className="of">/{sm.total}</span></b><small>online</small></div>
          <div className={sm.offline ? "bad-text" : ""}><b>{sm.offline}</b><small>offline</small></div>
          <div><b>{sm.playing}</b><small>playing</small></div>
          <div><b>{sm.version_majority ?? "—"}</b><small>FPP version</small></div>
        </div>
      </section>

      {alerts.length > 0 && (
        <section className="alerts">
          {alerts.map((a, i) => (
            <div key={i} className={`alert ${a.severity}`}><span className="sev">{a.severity}</span>{a.message}</div>
          ))}
        </section>
      )}

      {snap.hosts.length === 0 ? (
        <p className="muted">No controllers yet. Run discovery or add one on the Controllers page.</p>
      ) : (
        <section className="grid">
          {snap.hosts.map((h) => <HostCard key={h.id} h={h} refVersion={sm.version_majority} />)}
        </section>
      )}
      <p className="updated muted">Updated {new Date(snap.ts).toLocaleTimeString()}</p>
    </>
  );
}
