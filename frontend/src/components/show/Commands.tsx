import { useEffect, useMemo, useState } from "react";
import { api } from "../../api";
import type { CommandArg, FppCommand, Snapshot } from "../../types";
import ConfirmDialog from "../ConfirmDialog";

/** Values for an argument with a contentListUrl (e.g. api/playlists/playable), fetched through the proxy. */
function useArgOptions(hostId: number | null, arg: CommandArg) {
  const [opts, setOpts] = useState<string[] | null>(arg.contents ?? null);
  useEffect(() => {
    if (!hostId || !arg.contentListUrl || arg.contents?.length) return;
    const path = arg.contentListUrl.replace(/^\/?api\//, "");
    fetch(`/api/hosts/${hostId}/fpp/${path}`).then((r) => (r.ok ? r.json() : null)).then((d) => {
      if (Array.isArray(d)) setOpts(d.map((x) => (typeof x === "string" ? x : x?.name ?? JSON.stringify(x))));
      else if (d && typeof d === "object") setOpts(Object.keys(d));
    }).catch(() => {});
  }, [hostId, arg.contentListUrl, arg.contents]);
  return opts;
}

function ArgInput({ hostId, arg, value, onChange }: { hostId: number | null; arg: CommandArg; value: string; onChange: (v: string) => void }) {
  const opts = useArgOptions(hostId, arg);
  const label = `${arg.description || arg.name}${arg.optional ? " (optional)" : ""}`;
  const required = !arg.optional && !arg.allowBlanks;
  // A required list argument has no blank option, so the select shows its first choice: store that choice too.
  useEffect(() => {
    if (required && !value && opts?.length && arg.type !== "bool" && arg.type !== "int" && arg.type !== "float") onChange(opts[0]);
  }, [required, value, opts, arg.type, onChange]);
  if (arg.type === "bool") {
    return <label className="check"><input type="checkbox" checked={value === "true"} onChange={(e) => onChange(e.target.checked ? "true" : "false")} /> {label}</label>;
  }
  if (arg.type === "int" || arg.type === "float") {
    return <label>{label}<input type="number" value={value} min={arg.min} max={arg.max} step={arg.type === "float" ? "any" : 1} onChange={(e) => onChange(e.target.value)} /></label>;
  }
  if (opts && opts.length) {
    return (
      <label>{label}
        <select value={value} onChange={(e) => onChange(e.target.value)}>
          {(arg.optional || arg.allowBlanks) && <option value="">—</option>}
          {!opts.includes(value) && value && <option value={value}>{value}</option>}
          {opts.map((o) => <option key={o} value={o}>{o}</option>)}
        </select>
      </label>
    );
  }
  return <label>{label}<input value={value} onChange={(e) => onChange(e.target.value)} /></label>;
}

export default function Commands({ snap, notify }: { snap: Snapshot; notify: (ok: boolean, text: string) => void }) {
  const fppHosts = snap.hosts.filter((h) => h.kind === "fpp");
  const [hostId, setHostId] = useState<number | null>(snap.banner.player_id);
  const [commands, setCommands] = useState<FppCommand[]>([]);
  const [presets, setPresets] = useState<{ name: string }[]>([]);
  const [name, setName] = useState("");
  const [values, setValues] = useState<string[]>([]);
  const [multisync, setMultisync] = useState(false);
  const [filter, setFilter] = useState("");
  const [confirm, setConfirm] = useState(false);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<string | null>(null);

  useEffect(() => {
    if (!hostId) return;
    api<{ commands: FppCommand[]; presets: { name: string }[] }>(`/api/show/commands?host_id=${hostId}`)
      .then((r) => { setCommands(r.commands); setPresets(r.presets ?? []); })
      .catch((e) => notify(false, e.message));
  }, [hostId, notify]);

  const cmd = commands.find((c) => c.name === name);
  useEffect(() => { setValues((cmd?.args ?? []).map((a) => a.default ?? (a.type === "bool" ? "false" : ""))); setResult(null); }, [cmd]);

  const grouped = useMemo(() => {
    const g = new Map<string, FppCommand[]>();
    for (const c of commands) {
      if (filter && !c.name.toLowerCase().includes(filter.toLowerCase())) continue;
      const k = c.category || "Other";
      g.set(k, [...(g.get(k) ?? []), c]);
    }
    return [...g.entries()].sort(([a], [b]) => a.localeCompare(b));
  }, [commands, filter]);

  const host = fppHosts.find((h) => h.id === hostId);
  const missing = (cmd?.args ?? []).some((a, i) => !a.optional && !a.allowBlanks && a.type !== "bool" && !values[i]);

  const run = async (command: string, args: string[]) => {
    setBusy(true);
    try {
      const r = await api<{ message: string }>("/api/show/command", { method: "POST", body: JSON.stringify({ command, args, host_id: hostId, multisync }) });
      setResult(r.message || "OK");
      notify(true, `${command}: done`);
    } catch (e) {
      setResult(null);
      notify(false, `${command}: ${(e as Error).message}`);
    } finally {
      setBusy(false);
      setConfirm(false);
    }
  };

  return (
    <div className="detail-grid">
      <section className="panel">
        <h2>Run a command</h2>
        <div className="form-grid">
          <label>On
            <select value={hostId ?? ""} onChange={(e) => { setHostId(Number(e.target.value)); setName(""); }}>
              {fppHosts.map((h) => <option key={h.id} value={h.id}>{h.name}{h.id === snap.banner.player_id ? " (player)" : ""}{h.status.online ? "" : " (offline)"}</option>)}
            </select>
          </label>
          <label>Command
            <select value={name} onChange={(e) => setName(e.target.value)}>
              <option value="">Choose…</option>
              {grouped.map(([cat, cs]) => <optgroup key={cat} label={cat}>{cs.map((c) => <option key={c.name} value={c.name}>{c.name}</option>)}</optgroup>)}
            </select>
          </label>
          {commands.length > 20 && <label>Find<input value={filter} placeholder="filter commands" onChange={(e) => setFilter(e.target.value)} /></label>}
        </div>
        {cmd && (
          <>
            {cmd.description && <p className="muted small">{cmd.description}</p>}
            <div className="form-grid">
              {(cmd.args ?? []).map((a, i) => (
                <ArgInput key={`${cmd.name}-${a.name}`} hostId={hostId} arg={a} value={values[i] ?? ""}
                  onChange={(v) => setValues((vs) => vs.map((x, j) => (j === i ? v : x)))} />
              ))}
            </div>
            <label className="check"><input type="checkbox" checked={multisync} onChange={(e) => setMultisync(e.target.checked)} />
              Also run on the other controllers in the MultiSync group</label>
            <div className="dialog-actions">
              <button className="primary" disabled={busy || missing || !host?.status.online} onClick={() => setConfirm(true)}>Run</button>
            </div>
          </>
        )}
        {result && <pre className="log small">{result}</pre>}
      </section>

      <section className="panel">
        <h2>Command presets <span className="muted small">on {host?.name}</span></h2>
        {presets.length === 0 ? <p className="muted">No command presets are set up in FPP on this controller.</p> : (
          <div className="quick">{presets.map((p) => (
            <button key={p.name} className="chip" disabled={busy} onClick={() => run("Trigger Command Preset", [p.name])}>{p.name}</button>
          ))}</div>
        )}
      </section>

      <ConfirmDialog open={confirm} title={`Run "${name}" on ${host?.name}?`} busy={busy} confirmLabel="Run"
        message={<>
          {(cmd?.args ?? []).length > 0 && <p className="mono small">{(cmd?.args ?? []).map((a, i) => `${a.name}=${values[i] || "—"}`).join(", ")}</p>}
          {multisync && <p>It also runs on every controller in the MultiSync group.</p>}
          <p>Commands act on the show right away.</p>
        </>}
        onCancel={() => setConfirm(false)} onConfirm={() => run(name, values)} />
    </div>
  );
}
