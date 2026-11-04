import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api";

interface LogFile { name: string; mtime: string; sizeBytes: number; sizeHuman: string }

/** FPP log files through the generic proxy: /api/hosts/{id}/fpp/files/logs and /file/logs/<name>?tail=N */
export default function LogViewer({ hostId }: { hostId: number }) {
  const base = `/api/hosts/${hostId}/fpp`;
  const [files, setFiles] = useState<LogFile[]>([]);
  const [name, setName] = useState("fppd.log");
  const [lines, setLines] = useState(200);
  const [text, setText] = useState("");
  const [filter, setFilter] = useState("");
  const [follow, setFollow] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const pre = useRef<HTMLPreElement>(null);

  useEffect(() => {
    api<{ files: LogFile[] }>(`${base}/files/logs`)
      .then((r) => setFiles([...(r.files ?? [])].sort((a, b) => a.name.localeCompare(b.name))))
      .catch((e) => setError(e.message));
  }, [base]);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const r = await fetch(`${base}/file/logs/${encodeURIComponent(name)}?tail=${lines}`);
      if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
      setText(await r.text());
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, [base, name, lines]);

  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    if (!follow) return;
    const t = window.setInterval(load, 5000);
    return () => window.clearInterval(t);
  }, [follow, load]);
  useEffect(() => { pre.current?.scrollTo({ top: pre.current.scrollHeight }); }, [text]);

  const shown = filter
    ? text.split("\n").filter((l) => l.toLowerCase().includes(filter.toLowerCase())).join("\n")
    : text;

  return (
    <section className="panel">
      <div className="toolbar">
        <label>File
          <select value={name} onChange={(e) => setName(e.target.value)}>
            {!files.some((f) => f.name === name) && <option value={name}>{name}</option>}
            {files.map((f) => <option key={f.name} value={f.name}>{f.name} ({f.sizeHuman})</option>)}
          </select>
        </label>
        <label>Lines
          <select value={lines} onChange={(e) => setLines(Number(e.target.value))}>
            {[100, 200, 500, 2000, 10000].map((n) => <option key={n} value={n}>{n.toLocaleString()}</option>)}
          </select>
        </label>
        <label className="grow">Filter<input value={filter} placeholder="text to match" onChange={(e) => setFilter(e.target.value)} /></label>
        <label className="check"><input type="checkbox" checked={follow} onChange={(e) => setFollow(e.target.checked)} /> Follow (5 s)</label>
        <button onClick={load} disabled={loading}>{loading ? "Loading…" : "Refresh"}</button>
        <a className="button" href={`${base}/file/logs/${encodeURIComponent(name)}`} download={name}>Download</a>
      </div>
      {error && <p className="bad-text">{error}</p>}
      <pre ref={pre} className="log">{shown || (loading ? "" : "(empty)")}</pre>
    </section>
  );
}
