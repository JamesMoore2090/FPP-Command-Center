import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import type { Backup, HostView, RestoreResult } from "../types";

const AREA_LABELS: Record<string, string> = {
  channelInputs: "Channel inputs", channelOutputs: "Channel outputs", show_setup: "Playlists, schedule & scripts",
  settings: "System settings", plugins: "Plugins", network: "Network", misc_configs: "Other config files",
  virtualEEPROM: "Cape EEPROM", virtualdisplaymap: "Virtual display map", "model-overlays": "Pixel overlay models",
  channelmemorymaps: "Channel memory maps", outputProcessors: "Output processors", email: "Email",
};

/** Restore an FPP backup to its own controller or a replacement. Requires typing the target's name. */
export default function RestoreDialog({ backup, hosts, onClose }: { backup: Backup; hosts: HostView[]; onClose: () => void }) {
  const ref = useRef<HTMLDialogElement>(null);
  const targets = hosts.filter((h) => h.kind === "fpp");
  const [targetId, setTargetId] = useState<number | "">(backup.host_id && targets.some((h) => h.id === backup.host_id) ? backup.host_id : "");
  const [areas, setAreas] = useState<string[]>([]);
  const [area, setArea] = useState("all");
  const [keepNetwork, setKeepNetwork] = useState(true);
  const [keepMode, setKeepMode] = useState(true);
  const [forcePlatform, setForcePlatform] = useState(false);
  const [typed, setTyped] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<RestoreResult | null>(null);
  const [rebootMsg, setRebootMsg] = useState<string | null>(null);

  useEffect(() => { ref.current?.showModal(); }, []);
  useEffect(() => { api<{ areas: string[] }>(`/api/backups/${backup.id}/areas`).then((r) => setAreas(r.areas)).catch(() => {}); }, [backup.id]);

  const target = targets.find((h) => h.id === targetId);
  const otherBox = target && target.id !== backup.host_id;
  const ready = !!target && typed.trim() === target.name && !busy;

  const run = async () => {
    if (!target) return;
    setBusy(true);
    setError(null);
    try {
      setResult(await api<RestoreResult>(`/api/backups/${backup.id}/restore`, {
        method: "POST",
        body: JSON.stringify({ target_host_id: target.id, area, keep_network: keepNetwork, keep_mode: keepMode,
          allow_platform_mismatch: forcePlatform, confirm: true }),
      }));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const reboot = async () => {
    if (!target) return;
    setBusy(true);
    try {
      const r = await api<{ message: string }>(`/api/hosts/${target.id}/actions/reboot`, { method: "POST" });
      setRebootMsg(`Reboot ${r.message}`);
    } catch (e) {
      setRebootMsg(`Reboot failed: ${(e as Error).message}`);
    } finally {
      setBusy(false);
    }
  };

  return (
    <dialog ref={ref} className="dialog wide" onCancel={(e) => { e.preventDefault(); if (!busy) onClose(); }}>
      <h3>Restore backup</h3>
      <p className="muted small">
        {backup.host_name} · {new Date(backup.created_at).toLocaleString()} · FPP {backup.version ?? "?"} · {backup.platform}
      </p>

      {!result ? (
        <>
          <div className="form-grid">
            <label>Restore to
              <select value={targetId} onChange={(e) => { setTargetId(e.target.value ? Number(e.target.value) : ""); setTyped(""); }}>
                <option value="">Choose a controller…</option>
                {targets.map((h) => (
                  <option key={h.id} value={h.id}>{h.name} ({h.address}){h.id === backup.host_id ? " (original)" : ""}{h.status.online ? "" : " (offline)"}</option>
                ))}
              </select>
            </label>
            <label>What to restore
              <select value={area} onChange={(e) => setArea(e.target.value)}>
                <option value="all">Everything</option>
                {areas.map((a) => <option key={a} value={a}>{AREA_LABELS[a] ?? a}</option>)}
              </select>
            </label>
          </div>
          <label className="check"><input type="checkbox" checked={keepNetwork} onChange={(e) => setKeepNetwork(e.target.checked)} />
            Keep the target's current network settings (IP address, WiFi)</label>
          <label className="check"><input type="checkbox" checked={keepMode} onChange={(e) => setKeepMode(e.target.checked)} />
            Keep the target's current mode (player / remote)</label>
          {otherBox && (
            <div className="alert warning"><span className="sev">note</span>
              <span>Restoring {backup.host_name}'s configuration onto <b>{target!.name}</b>. For a replacement box, untick
                both options so it takes over the old controller's IP address and mode; it moves to that IP after the reboot.</span>
            </div>
          )}
          {error && (
            <div className="alert critical"><span className="sev">error</span><span>{error}
              {error.includes("allow_platform_mismatch") && (
                <label className="check"><input type="checkbox" checked={forcePlatform} onChange={(e) => setForcePlatform(e.target.checked)} /> Restore anyway</label>
              )}</span>
            </div>
          )}
          {target && (
            <label className="confirm-type"><span>This overwrites the configuration on <b>{target.name}</b>. A backup of its
              current configuration is taken first. Type <b>{target.name}</b> to confirm.</span>
              <input value={typed} onChange={(e) => setTyped(e.target.value)} autoComplete="off" spellCheck={false} />
            </label>
          )}
          <div className="dialog-actions">
            <button onClick={onClose} disabled={busy}>Cancel</button>
            <button className="danger" disabled={!ready} onClick={run}>{busy ? "Restoring…" : "Restore"}</button>
          </div>
        </>
      ) : (
        <>
          <div className={`alert ${result.restored ? "info" : "critical"}`}>
            <span className="sev">{result.restored ? "done" : "failed"}</span>
            {result.restored ? `Backup restored to ${target?.name}.` : "FPP did not restore the backup."}
          </div>
          {result.areas.length > 0 && (
            <ul className="plain-list">
              {result.areas.map((a, i) => <li key={i} className={a.ok ? "" : "bad-text"}>{a.area}: {a.ok ? "restored" : "failed"}</li>)}
            </ul>
          )}
          {result.errors.map((e, i) => <p key={i} className="bad-text small">{e}</p>)}
          <p className="muted small">The configuration from before the restore was saved as backup #{result.pre_restore_backup_id} (pinned).</p>
          {result.reboot_recommended && <p>FPP needs a reboot to finish applying the restore.</p>}
          {rebootMsg && <p className="small">{rebootMsg}</p>}
          <div className="dialog-actions">
            <button onClick={onClose} disabled={busy}>Close</button>
            {result.reboot_recommended && !rebootMsg && <button className="danger" onClick={reboot} disabled={busy}>Reboot {target?.name} now</button>}
          </div>
        </>
      )}
    </dialog>
  );
}
