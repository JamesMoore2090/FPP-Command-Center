import { useCallback, useState } from "react";
import { useSearchParams } from "react-router-dom";
import Commands from "../components/show/Commands";
import Playback from "../components/show/Playback";
import Schedule from "../components/show/Schedule";
import TestMode from "../components/show/TestMode";
import type { Snapshot } from "../types";

const TABS = [
  { id: "playback", label: "Playback" },
  { id: "schedule", label: "Schedule" },
  { id: "commands", label: "Commands" },
  { id: "test", label: "Test mode" },
];

export default function Show({ snap }: { snap: Snapshot | null }) {
  const [params, setParams] = useSearchParams();
  const [notice, setNotice] = useState<{ ok: boolean; text: string } | null>(null);
  const notify = useCallback((ok: boolean, text: string) => setNotice({ ok, text }), []);
  const tab = TABS.some((t) => t.id === params.get("tab")) ? params.get("tab")! : "playback";

  if (!snap) return <p className="muted">Connecting…</p>;
  if (!snap.banner.player_id) return <p className="muted">No FPP player found in the fleet.</p>;

  return (
    <>
      <div className="page-head"><h1>Show</h1></div>
      {notice && (
        <div className={`alert ${notice.ok ? "info" : "critical"}`} role="status" onClick={() => setNotice(null)}>
          <span className="sev">{notice.ok ? "done" : "error"}</span>{notice.text}
        </div>
      )}
      <nav className="tabs" role="tablist">
        {TABS.map((t) => (
          <button key={t.id} role="tab" aria-selected={tab === t.id} className={tab === t.id ? "active" : ""}
            onClick={() => setParams(t.id === "playback" ? {} : { tab: t.id }, { replace: true })}>{t.label}</button>
        ))}
      </nav>
      {tab === "playback" && <Playback snap={snap} notify={notify} />}
      {tab === "schedule" && <Schedule notify={notify} />}
      {tab === "commands" && <Commands snap={snap} notify={notify} />}
      {tab === "test" && <TestMode snap={snap} notify={notify} />}
    </>
  );
}
