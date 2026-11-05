import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { ago } from "../format";
import type { Job, JobStep } from "../types";

const STEP_BADGE: Record<string, string> = { ok: "sync-in_sync", failed: "bad", running: "sync-drift", skipped: "", pending: "" };
const JOB_TEXT: Record<string, string> = { running: "Running", ok: "Finished", failed: "Failed", partial: "Finished with failures", cancelled: "Cancelled" };

/** Live view of a background job: one row per controller with status, progress and its log. */
export default function JobPanel({ jobId, onDone, onClose }: { jobId: string; onDone?: (job: Job) => void; onClose?: () => void }) {
  const [job, setJob] = useState<Job | null>(null);
  const [error, setError] = useState<string | null>(null);
  const done = useRef(false);

  useEffect(() => {
    done.current = false;
    let timer: number | undefined;
    const load = () => api<Job>(`/api/jobs/${jobId}`).then((j) => {
      setJob(j);
      if (j.status === "running") timer = window.setTimeout(load, 1500);
      else if (!done.current) { done.current = true; onDone?.(j); }
    }).catch((e) => setError((e as Error).message));
    load();
    return () => window.clearTimeout(timer);
  }, [jobId]); // eslint-disable-line react-hooks/exhaustive-deps

  if (error) return <section className="panel"><p className="bad-text">{error}</p></section>;
  if (!job) return <section className="panel"><p className="muted">Loading job…</p></section>;
  const running = job.status === "running";

  return (
    <section className={`panel job job-${job.status}`}>
      <div className="panel-head">
        <h2>{job.title}</h2>
        <div className="actions">
          <span className={`badge ${job.status === "ok" ? "sync-in_sync" : running ? "sync-drift" : "bad"}`}>{JOB_TEXT[job.status]}</span>
          {running && (
            <button className="small-btn" disabled={job.cancel_requested}
              title="Controllers that are already updating finish; the rest are skipped"
              onClick={() => api(`/api/jobs/${job.id}/cancel`, { method: "POST" }).catch((e) => setError((e as Error).message))}>
              {job.cancel_requested ? "Stopping after current…" : "Stop after current"}
            </button>
          )}
          {!running && onClose && <button className="small-btn" onClick={onClose}>Close</button>}
        </div>
      </div>
      <p className="muted small">Started {ago(job.created_at)}. {running && "You can leave this page; the job keeps running on the server."}</p>
      <div className="job-steps">{job.steps.map((s) => <StepRow key={s.id} step={s} />)}</div>
    </section>
  );
}

function StepRow({ step }: { step: JobStep }) {
  const [open, setOpen] = useState(false);
  const logRef = useRef<HTMLPreElement>(null);
  useEffect(() => { if (open && logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight; }, [open, step.log.length]);
  const pct = step.progress != null ? Math.round(step.progress * 100) : null;
  return (
    <div className={`job-step st-${step.status}`}>
      <div className="job-step-head">
        <span className={`badge ${STEP_BADGE[step.status]}`}>{step.status}</span>
        <b>{step.label}</b>
        <span className="muted small grow">{step.message}</span>
        {step.log.length > 0 && <button className="link small" onClick={() => setOpen((o) => !o)}>{open ? "Hide log" : "Log"}</button>}
      </div>
      {step.status === "running" && (
        <div className="progress">
          <div className={`bar ${pct == null ? "indeterminate" : ""}`}><span style={{ width: pct == null ? "30%" : `${pct}%` }} /></div>
          {pct != null && <span className="small num">{pct}%</span>}
        </div>
      )}
      {open && <pre className="log job-log" ref={logRef}>{step.log.join("\n")}</pre>}
    </div>
  );
}
