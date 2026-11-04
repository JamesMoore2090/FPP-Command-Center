import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../../api";
import { stripExt } from "../../format";
import type { ScheduleData, ScheduleEntryRaw } from "../../types";
import ConfirmDialog from "../ConfirmDialog";

// FPP src/ScheduleEntry.h
const DAYS: [number, string][] = [
  [7, "Everyday"], [8, "Weekdays"], [9, "Weekends"], [0, "Sunday"], [1, "Monday"], [2, "Tuesday"], [3, "Wednesday"],
  [4, "Thursday"], [5, "Friday"], [6, "Saturday"], [10, "Mon/Wed/Fri"], [11, "Tue/Thu"], [12, "Sun–Thu"], [13, "Fri/Sat"],
  [14, "Odd days"], [15, "Even days"],
];
const REPEATS: [number, string][] = [[0, "Play once"], [1, "Repeat"], [500, "Every 5 min"], [1000, "Every 10 min"],
  [1500, "Every 15 min"], [2000, "Every 20 min"], [3000, "Every 30 min"], [6000, "Every 60 min"]];
const STOPS: [number, string][] = [[0, "Graceful"], [2, "Graceful after loop"], [1, "Hard stop"]];
const SUN_TIMES = ["SunRise", "SunSet", "Dawn", "Dusk"];

const dayLabel = (d: number) => DAYS.find(([v]) => v === d)?.[1] ?? (d >= 0x10000 ? "Custom days" : String(d));
const repeatLabel = (r: number) => REPEATS.find(([v]) => v === r)?.[1] ?? `Repeat (${r})`;
const fmtTime = (t: string, off?: number) => {
  if (SUN_TIMES.includes(t)) return `${t}${off ? ` ${off > 0 ? "+" : ""}${off} min` : ""}`;
  const m = /^(\d{1,2}):(\d{2})/.exec(t);
  if (!m) return t;
  const h = Number(m[1]);
  return `${((h + 11) % 12) + 1}:${m[2]} ${h < 12 ? "AM" : "PM"}`;
};
const normTime = (t: string) => (/^\d{1,2}:\d{2}$/.test(t.trim()) ? `${t.trim().padStart(5, "0")}:00` : t.trim());
const what = (e: ScheduleEntryRaw) => e.command ? `${e.command}${e.args?.length ? ` (${e.args.filter(Boolean).join(", ")})` : ""}` : stripExt(e.playlist ?? "") ?? "";

export default function Schedule({ notify }: { notify: (ok: boolean, text: string) => void }) {
  const [data, setData] = useState<ScheduleData | null>(null);
  const [entries, setEntries] = useState<ScheduleEntryRaw[]>([]);
  const [warnings, setWarnings] = useState<(string | null)[]>([]);
  const [dirty, setDirty] = useState(false);
  const [playable, setPlayable] = useState<string[]>([]);
  const [editing, setEditing] = useState<{ index: number | null; entry: ScheduleEntryRaw } | null>(null);
  const [confirmSave, setConfirmSave] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    setError(null);
    api<ScheduleData>("/api/show/schedule").then((d) => {
      setData(d);
      setEntries(d.entries.map((e) => e.raw));
      setWarnings(d.entries.map((e) => e.warning));
      setDirty(false);
    }).catch((e) => setError(e.message));
  }, []);
  useEffect(() => { load(); }, [load]);
  useEffect(() => { api<{ playable: string[] }>("/api/show/playlists").then((l) => setPlayable(l.playable)).catch(() => {}); }, []);

  const change = (next: ScheduleEntryRaw[]) => { setEntries(next); setWarnings(next.map(() => null)); setDirty(true); };
  const move = (i: number, d: -1 | 1) => {
    const j = i + d;
    if (j < 0 || j >= entries.length) return;
    const next = [...entries];
    [next[i], next[j]] = [next[j], next[i]];
    change(next);
  };

  const save = async () => {
    setBusy(true);
    try {
      const r = await api<ScheduleData & { pre_change_backup_id: number }>("/api/show/schedule", {
        method: "PUT", body: JSON.stringify({ entries, confirm: true }),
      });
      setData(r); setEntries(r.entries.map((e) => e.raw)); setWarnings(r.entries.map((e) => e.warning)); setDirty(false);
      notify(true, `Schedule saved on ${r.host}. The previous schedule is in backup #${r.pre_change_backup_id}.`);
    } catch (e) {
      notify(false, `Saving the schedule failed: ${(e as Error).message}`);
    } finally {
      setBusy(false);
      setConfirmSave(false);
    }
  };

  if (error) return <p className="bad-text">{error}</p>;
  if (!data) return <p className="muted">Loading schedule…</p>;
  const now = Date.now() / 1000;
  const upcoming = data.upcoming.filter((u) => u.end >= now).slice(0, 8);

  return (
    <>
      <section className="panel">
        <h2>Coming up <span className="muted small">on {data.host}{!data.enabled && " · scheduler disabled"}</span></h2>
        {upcoming.length === 0 ? <p className="muted">Nothing scheduled in the next few weeks.</p> : (
          <ul className="upcoming">
            {upcoming.map((u, i) => (
              <li key={i}><span className="when">{u.start_str}{u.end !== u.start && <span className="muted"> – {u.end_str.split("@ ")[1] ?? u.end_str}</span>}</span>
                <span>{u.command === "Start Playlist" ? stripExt(u.args[0]) : `${u.command}${u.args.length ? `: ${u.args.filter(Boolean).join(", ")}` : ""}`}</span></li>
            ))}
          </ul>
        )}
      </section>

      <section className="panel">
        <div className="panel-head">
          <h2>Schedule entries</h2>
          <div className="actions">
            {dirty && <button onClick={load} disabled={busy}>Discard changes</button>}
            <button onClick={() => setEditing({ index: null, entry: newEntry(playable[0] ?? "") })}>Add entry</button>
            <button className="primary" disabled={!dirty || busy} onClick={() => setConfirmSave(true)}>Save schedule</button>
          </div>
        </div>
        <p className="muted small">Entries higher in the list win when times overlap. Changes take effect when you save.</p>
        <div className="table-scroll">
          <table className="plain schedule">
            <thead><tr><th>On</th><th>What</th><th>Days</th><th>Time</th><th>Dates</th><th>Repeat / stop</th><th></th></tr></thead>
            <tbody>
              {entries.map((e, i) => (
                <tr key={i} className={e.enabled ? "" : "muted"}>
                  <td><input type="checkbox" aria-label="Enabled" checked={!!e.enabled}
                    onChange={(ev) => change(entries.map((x, j) => (j === i ? { ...x, enabled: ev.target.checked ? 1 : 0 } : x)))} /></td>
                  <td className="what">
                    <span className="badge">{e.command ? "command" : "playlist"}</span> {what(e)}
                    {e.multisyncCommand && <span className="muted small"> · MultiSync{e.multisyncHosts ? ` to ${e.multisyncHosts}` : ""}</span>}
                    {warnings[i] && <div className="warn-text small">⚠ {warnings[i]}</div>}
                  </td>
                  <td>{dayLabel(e.day)}</td>
                  <td className="num">{fmtTime(e.startTime, e.startTimeOffset)}{!e.command && <> – {fmtTime(e.endTime, e.endTimeOffset)}</>}</td>
                  <td className="small">{e.startDate} → {e.endDate}</td>
                  <td className="small">{e.command ? "" : `${repeatLabel(e.repeat)} · ${STOPS.find(([v]) => v === e.stopType)?.[1] ?? e.stopType}`}</td>
                  <td><div className="actions">
                    <button className="small-btn" aria-label="Move up" disabled={i === 0} onClick={() => move(i, -1)}>↑</button>
                    <button className="small-btn" aria-label="Move down" disabled={i === entries.length - 1} onClick={() => move(i, 1)}>↓</button>
                    <button className="small-btn" onClick={() => setEditing({ index: i, entry: { ...e } })}>Edit</button>
                    <button className="small-btn danger" onClick={() => change(entries.filter((_, j) => j !== i))}>Remove</button>
                  </div></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      {editing && (
        <EntryDialog entry={editing.entry} playable={playable} isNew={editing.index === null}
          onCancel={() => setEditing(null)}
          onSave={(e) => {
            change(editing.index === null ? [...entries, e] : entries.map((x, j) => (j === editing.index ? e : x)));
            setEditing(null);
          }} />
      )}
      <ConfirmDialog open={confirmSave} title={`Save the schedule on ${data.host}?`} busy={busy}
        message={<p>This replaces the player's schedule. A backup of the player is taken first so you can go back.</p>}
        confirmLabel="Save schedule" onCancel={() => setConfirmSave(false)} onConfirm={save} />
    </>
  );
}

function newEntry(playlist: string): ScheduleEntryRaw {
  const today = new Date().toISOString().slice(0, 10);
  return { enabled: 1, sequence: 0, playlist, day: 7, startTime: "17:00:00", startTimeOffset: 0, endTime: "22:00:00",
    endTimeOffset: 0, repeat: 1, startDate: today, endDate: `${new Date().getFullYear() + 1}-01-06`, stopType: 0 };
}

function TimeField({ label, value, offset, onChange }: { label: string; value: string; offset?: number; onChange: (t: string, o: number) => void }) {
  const sun = SUN_TIMES.includes(value);
  return (
    <label>{label}
      <span className="time-field">
        <select value={sun ? value : "clock"} onChange={(ev) => onChange(ev.target.value === "clock" ? "17:00:00" : ev.target.value, offset ?? 0)}>
          <option value="clock">Clock</option>{SUN_TIMES.map((s) => <option key={s} value={s}>{s}</option>)}
        </select>
        {sun
          ? <input type="number" aria-label={`${label} offset (minutes)`} value={offset ?? 0} onChange={(ev) => onChange(value, Number(ev.target.value))} title="Offset in minutes" />
          : <input type="time" step={1} aria-label={label} value={value.length === 5 ? `${value}:00` : value} onChange={(ev) => onChange(ev.target.value, 0)} />}
      </span>
    </label>
  );
}

function EntryDialog({ entry, playable, isNew, onSave, onCancel }: {
  entry: ScheduleEntryRaw; playable: string[]; isNew: boolean; onSave: (e: ScheduleEntryRaw) => void; onCancel: () => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const [e, setE] = useState<ScheduleEntryRaw>(entry);
  useEffect(() => { ref.current?.showModal(); }, []);
  const set = (patch: Partial<ScheduleEntryRaw>) => setE((x) => ({ ...x, ...patch }));
  const isCommand = !!e.command;
  const timeOk = (t: string) => SUN_TIMES.includes(t) || /^\d{1,2}:\d{2}(:\d{2})?$/.test(t.trim());
  const valid = timeOk(e.startTime) && (isCommand || timeOk(e.endTime)) && e.startDate && e.endDate && (isCommand || e.playlist);

  return (
    <dialog ref={ref} className="dialog wide" onCancel={(ev) => { ev.preventDefault(); onCancel(); }}>
      <h3>{isNew ? "Add schedule entry" : "Edit schedule entry"}</h3>
      <div className="form-grid">
        {isCommand ? (
          <label>Command<input value={what(e)} disabled /></label>
        ) : (
          <label>Playlist or sequence
            <select value={e.playlist} onChange={(ev) => set({ playlist: ev.target.value, sequence: ev.target.value.endsWith(".fseq") ? 1 : 0 })}>
              {!playable.includes(e.playlist ?? "") && <option value={e.playlist}>{e.playlist} (not on player)</option>}
              {playable.map((p) => <option key={p} value={p}>{stripExt(p)}</option>)}
            </select>
          </label>
        )}
        <label>Days
          <select value={e.day} onChange={(ev) => set({ day: Number(ev.target.value) })}>
            {e.day >= 0x10000 && <option value={e.day}>Custom days (keep)</option>}
            {DAYS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
          </select>
        </label>
        <TimeField label={isCommand ? "Time" : "Start"} value={e.startTime} offset={e.startTimeOffset}
          onChange={(t, o) => set({ startTime: normTime(t), startTimeOffset: o, ...(isCommand ? { endTime: normTime(t), endTimeOffset: o } : {}) })} />
        {!isCommand && <TimeField label="End" value={e.endTime} offset={e.endTimeOffset} onChange={(t, o) => set({ endTime: normTime(t), endTimeOffset: o })} />}
        <label>From date<input value={e.startDate} placeholder="YYYY-MM-DD" onChange={(ev) => set({ startDate: ev.target.value.trim() })} /></label>
        <label>To date<input value={e.endDate} placeholder="YYYY-MM-DD" onChange={(ev) => set({ endDate: ev.target.value.trim() })} /></label>
        {!isCommand && (
          <>
            <label>Repeat
              <select value={e.repeat} onChange={(ev) => set({ repeat: Number(ev.target.value) })}>
                {!REPEATS.some(([v]) => v === e.repeat) && <option value={e.repeat}>{repeatLabel(e.repeat)}</option>}
                {REPEATS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
              </select>
            </label>
            <label>When the end time comes
              <select value={e.stopType} onChange={(ev) => set({ stopType: Number(ev.target.value) })}>
                {STOPS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
              </select>
            </label>
          </>
        )}
      </div>
      <label className="check"><input type="checkbox" checked={!!e.enabled} onChange={(ev) => set({ enabled: ev.target.checked ? 1 : 0 })} /> Enabled</label>
      {isCommand && <p className="muted small">The command and its arguments are kept as they are; edit those in FPP if needed.</p>}
      <p className="muted small">Dates can also be FPP holiday names. Times can be sunrise/sunset based (offset in minutes).</p>
      <div className="dialog-actions">
        <button onClick={onCancel}>Cancel</button>
        <button className="primary" disabled={!valid} onClick={() => onSave(e)}>{isNew ? "Add" : "Done"}</button>
      </div>
    </dialog>
  );
}
