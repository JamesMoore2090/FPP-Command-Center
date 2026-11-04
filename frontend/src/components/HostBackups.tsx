import { useEffect, useState } from "react";
import { api } from "../api";
import type { Backup, HostView } from "../types";
import BackupList from "./BackupList";

export default function HostBackups({ host }: { host: HostView }) {
  const [hosts, setHosts] = useState<HostView[]>([]);
  const [refresh, setRefresh] = useState(0);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);
  useEffect(() => { api<HostView[]>("/api/hosts").then(setHosts).catch(() => {}); }, []);

  const backupNow = async () => {
    setBusy(true);
    setMessage(null);
    try {
      const [b] = await api<Backup[]>("/api/backups/run", { method: "POST", body: JSON.stringify({ host_ids: [host.id] }) });
      setMessage(b.status === "ok"
        ? { ok: true, text: b.changed ? "Backed up. The configuration changed since the last backup." : "Backed up. No changes since the last backup." }
        : { ok: false, text: `Backup ${b.status}: ${b.error}` });
    } catch (e) {
      setMessage({ ok: false, text: (e as Error).message });
    } finally {
      setBusy(false);
      setRefresh((x) => x + 1);
    }
  };

  return (
    <>
      <div className="toolbar">
        <span className="muted small">
          {host.kind === "falcon"
            ? "Falcon backups save the controller's XML configuration and network settings for reference and comparison. Restoring is done by hand."
            : "Configuration only (settings, playlists, schedule, outputs, network, plugins). Sequences and media are not included."}
        </span>
        <span className="grow" />
        <button className="primary" onClick={backupNow} disabled={busy || !host.status.online}>{busy ? "Backing up…" : "Back up now"}</button>
      </div>
      {message && <div className={`alert ${message.ok ? "info" : "critical"}`} role="status" onClick={() => setMessage(null)}>
        <span className="sev">{message.ok ? "done" : "error"}</span>{message.text}</div>}
      <BackupList hostId={host.id} hosts={hosts} refreshKey={refresh} />
    </>
  );
}
