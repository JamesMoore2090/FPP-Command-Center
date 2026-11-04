import { useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api } from "../api";
import type { Backup, DiffChange, DiffResult } from "../types";

const AREA_LABELS: Record<string, string> = {
  channelInputs: "Channel inputs", channelOutputs: "Channel outputs", show_setup: "Playlists, schedule & scripts",
  settings: "System settings", plugins: "Plugins", network: "Network", misc_configs: "Other config files",
  virtualEEPROM: "Cape EEPROM", virtualdisplaymap: "Virtual display map", "model-overlays": "Pixel overlay models",
  files: "Config files", platform: "Platform", firmware: "Firmware", model: "Model",
};

const label = (b: Backup) => `${b.host_name} · ${new Date(b.created_at).toLocaleString([], { dateStyle: "medium", timeStyle: "short" })}`;

export default function BackupCompare() {
  const [params] = useSearchParams();
  const a = params.get("a"), b = params.get("b");
  const [diff, setDiff] = useState<DiffResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState("");

  useEffect(() => {
    setDiff(null);
    setError(null);
    api<DiffResult>(`/api/backups/diff?a=${a}&b=${b}`).then(setDiff).catch((e) => setError(e.message));
  }, [a, b]);

  const groups = useMemo(() => {
    const out = new Map<string, DiffChange[]>();
    for (const c of diff?.changes ?? []) {
      if (filter && !c.path.toLowerCase().includes(filter.toLowerCase())) continue;
      out.set(c.area, [...(out.get(c.area) ?? []), c]);
    }
    return [...out.entries()];
  }, [diff, filter]);

  if (error) return <><Crumbs /><p className="bad-text">{error}</p></>;
  if (!diff) return <><Crumbs /><p className="muted">Comparing…</p></>;
  const sameHost = diff.a.host_id === diff.b.host_id;

  return (
    <>
      <Crumbs />
      <h1>{sameHost ? `${diff.a.host_name}: changes between backups` : `${diff.a.host_name} vs ${diff.b.host_name}`}</h1>
      <div className="compare-sides">
        <div><span className="side a">A</span> {label(diff.a)} <span className="muted small">{diff.a.version}</span></div>
        <div><span className="side b">B</span> {label(diff.b)} <span className="muted small">{diff.b.version}</span></div>
      </div>
      <div className="toolbar">
        <span>{diff.changes.length === 0 ? "No differences." : `${diff.changes.length} difference${diff.changes.length === 1 ? "" : "s"}`}
          <span className="muted"> · {diff.unchanged} settings identical · passwords and WiFi keys are hidden</span></span>
        <span className="grow" />
        {diff.changes.length > 10 && <input aria-label="Filter" placeholder="Filter by setting name" value={filter} onChange={(e) => setFilter(e.target.value)} />}
      </div>

      {groups.map(([area, changes]) => (
        <details key={area} className="panel diff-group" open={diff.changes.length <= 60 || changes.length <= 15}>
          <summary><h2>{AREA_LABELS[area] ?? area} <span className="muted small">{changes.length}</span></h2></summary>
          <div className="diff-rows">
            {changes.map((c) => <Change key={c.path} c={c} />)}
          </div>
        </details>
      ))}
    </>
  );
}

function Crumbs() {
  return <div className="crumbs"><Link to="/backups">Backups</Link> / Compare</div>;
}

function Change({ c }: { c: DiffChange }) {
  const path = c.path.split("/").slice(1).join(" / ") || c.path;
  return (
    <div className="diff-row">
      <div className="diff-path mono">{path} <span className={`badge diff-${c.type}`}>{c.type === "changed" ? (c.secret ? "hidden value changed" : "changed") : c.type === "added" ? "only in B" : "only in A"}</span></div>
      {c.lines ? (
        <pre className="diff-lines">{c.lines.map((l, i) => (
          <span key={i} className={l.startsWith("+") ? "add" : l.startsWith("-") ? "del" : l.startsWith("@@") ? "hunk" : ""}>{l}{"\n"}</span>
        ))}</pre>
      ) : (
        <div className="diff-values">
          {c.a !== undefined && <div className="del"><span className="side a">A</span><code>{c.a}</code></div>}
          {c.b !== undefined && <div className="add"><span className="side b">B</span><code>{c.b}</code></div>}
        </div>
      )}
    </div>
  );
}
