import { Link } from "react-router-dom";
import type { HostView, Sync } from "../types";
import { bytes, duration, stripExt } from "../format";

const SYNC_LABEL: Record<string, string> = {
  in_sync: "In sync", drift: "Drifting", out_of_sync: "Out of sync", idle: "Idle", unknown: "Sync unknown",
};

function Meter({ value, warn }: { value: number | null; warn: number }) {
  if (value == null) return <span className="muted">—</span>;
  const level = value >= warn ? "bad" : value >= warn * 0.8 ? "warn" : "ok";
  return (
    <span className="meter-wrap">
      <span className="meter"><span className={`fill ${level}`} style={{ width: `${Math.min(100, value)}%` }} /></span>
      <span className="num">{value.toFixed(0)}%</span>
    </span>
  );
}

function SyncBadge({ sync }: { sync: Sync }) {
  if (!sync) return null;
  return <span className={`badge sync-${sync}`}>{SYNC_LABEL[sync]}</span>;
}

export default function HostCard({ h, refVersion, tempWarn = 70 }: { h: HostView; refVersion: string | null; tempWarn?: number }) {
  const s = h.status;
  const mismatch = h.kind === "fpp" && refVersion && s.version && s.version !== refVersion;
  const playing = s.status === "playing";
  return (
    <article className={`card ${s.online ? "" : "offline"}`}>
      <header>
        <span className={`dot ${s.online ? "ok" : "bad"}`} title={s.online ? "Online" : s.error ?? "Offline"} />
        <div className="title">
          <h3><Link to={`/hosts/${h.id}`} className="card-link">{h.name}</Link></h3>
          <a className="addr" href={`http://${h.address}/`} target="_blank" rel="noreferrer">{h.address}</a>
        </div>
        <span className={`badge role-${h.role ?? "unknown"}`}>{h.role ?? h.kind}</span>
      </header>

      {!s.online ? (
        <p className="offline-msg">Offline{s.error ? ` — ${s.error}` : ""}</p>
      ) : (
        <>
          <dl className="stats">
            <dt>Version</dt>
            <dd className={mismatch ? "warn-text" : ""} title={mismatch ? `Fleet is on ${refVersion}` : undefined}>
              {h.kind === "falcon" ? "fw " : "FPP "}{s.version ?? "—"}{mismatch ? " ⚠" : ""}
            </dd>
            <dt>CPU</dt>
            <dd><Meter value={s.cpu_pct} warn={90} /></dd>
            <dt>Temp</dt>
            <dd>
              {s.temps.length ? s.temps.map((t) => (
                <span key={t.label} className={`temp ${t.value >= tempWarn ? "bad-text" : ""}`} title={t.label}>
                  {t.value.toFixed(0)}°C<small>{t.label}</small>
                </span>
              )) : <span className="muted">—</span>}
            </dd>
            {s.voltages.length > 0 && (<>
              <dt>Volts</dt>
              <dd>{s.voltages.map((v) => <span key={v.label} className="temp">{v.value}V<small>{v.label}</small></span>)}</dd>
            </>)}
            {h.kind === "fpp" && (<>
              <dt>Status</dt>
              <dd>
                <span className={`badge st-${s.status}`}>{s.status ?? "—"}</span>
                {playing && s.current_sequence && <span className="seq" title={s.current_sequence}>{stripExt(s.current_sequence)}</span>}
              </dd>
            </>)}
            <dt>Uptime</dt>
            <dd>{duration(s.uptime_s)}</dd>
            {h.disk_pct != null && (<>
              <dt>Disk</dt>
              <dd title={`${bytes(s.disk_free)} free of ${bytes(s.disk_total)}`}><Meter value={h.disk_pct} warn={90} /></dd>
            </>)}
          </dl>
          <footer>
            <span className="model">{s.model ?? h.model ?? ""}</span>
            <SyncBadge sync={h.sync} />
            {h.kind === "fpp" && s.fppd && s.fppd !== "running" && <span className="badge bad">fppd {s.fppd}</span>}
          </footer>
        </>
      )}
    </article>
  );
}
