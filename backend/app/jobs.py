"""Long-running fleet jobs (content pushes, FPP updates, OS upgrades) that outlive the HTTP request.

A job is a list of steps, normally one per controller, run one after another (or a few at a time). Each step
has its own status, progress and log so the UI can show what is happening on every box. Jobs live in memory
(the last MAX_JOBS); a service restart forgets them, but whatever they did on the controllers stays done.
"""
import asyncio
import logging
import secrets
from collections import deque
from datetime import datetime, timezone
from typing import Awaitable, Callable

from pydantic import BaseModel, Field

log = logging.getLogger("fppcc.jobs")
MAX_JOBS = 50
MAX_LOG_LINES = 400


class StepFailed(Exception):
    """Raised by a step to fail with a clean message (no traceback in the log)."""


class Step(BaseModel):
    id: int
    label: str
    host_id: int | None = None
    host: str | None = None
    status: str = "pending"          # pending / running / ok / failed / skipped
    message: str | None = None
    progress: float | None = None    # 0..1 when known
    started_at: datetime | None = None
    finished_at: datetime | None = None
    log: list[str] = Field(default_factory=list)


class Job(BaseModel):
    id: str
    kind: str                        # content_push / fpp_update / fpp_upgrade / os_upgrade
    title: str
    status: str = "running"          # running / ok / failed / partial / cancelled
    created_at: datetime
    finished_at: datetime | None = None
    cancel_requested: bool = False
    steps: list[Step] = Field(default_factory=list)

    def summary(self) -> dict:
        d = self.model_dump(exclude={"steps"})
        d["counts"] = {s: sum(1 for x in self.steps if x.status == s)
                       for s in ("pending", "running", "ok", "failed", "skipped")}
        d["total"] = len(self.steps)
        return d


class StepContext:
    """Handed to a step function: log lines, report progress, check for cancellation."""

    def __init__(self, job: Job, step: Step):
        self.job, self.step = job, step
        self._partial = ""

    def log(self, line: str):
        for part in str(line).rstrip("\n").split("\n"):
            self.step.log.append(part[:500])
        if len(self.step.log) > MAX_LOG_LINES:
            del self.step.log[: len(self.step.log) - MAX_LOG_LINES]

    def feed(self, text: str):
        """Streamed output: buffer partial lines."""
        text = self._partial + text
        lines = text.split("\n")
        self._partial = lines.pop()
        for line in lines:
            self.log(line.rstrip("\r"))

    def flush(self):
        if self._partial:
            self.log(self._partial)
            self._partial = ""

    def progress(self, fraction: float | None, message: str | None = None):
        self.step.progress = None if fraction is None else max(0.0, min(1.0, fraction))
        if message is not None:
            self.step.message = message

    @property
    def cancelled(self) -> bool:
        return self.job.cancel_requested


StepFn = Callable[[StepContext], Awaitable[str | None]]


class JobManager:
    def __init__(self):
        self.jobs: deque[Job] = deque(maxlen=MAX_JOBS)
        self._tasks: dict[str, asyncio.Task] = {}
        self.exclusive: dict[str, str] = {}      # lock name -> running job id

    def get(self, job_id: str) -> Job | None:
        return next((j for j in self.jobs if j.id == job_id), None)

    def running(self, lock: str) -> Job | None:
        jid = self.exclusive.get(lock)
        j = self.get(jid) if jid else None
        return j if j and j.status == "running" else None

    def start(self, kind: str, title: str, steps: list[tuple[dict, StepFn]], *, parallel: int = 1,
              stop_on_failure: bool = False, lock: str | None = None) -> Job:
        """steps: (Step fields, async fn(ctx) -> message). Returns immediately; the job runs in the background."""
        job = Job(id=secrets.token_hex(6), kind=kind, title=title, created_at=datetime.now(timezone.utc),
                  steps=[Step(id=i, **fields) for i, (fields, _) in enumerate(steps)])
        self.jobs.appendleft(job)
        if lock:
            self.exclusive[lock] = job.id
        fns = [fn for _, fn in steps]
        self._tasks[job.id] = asyncio.create_task(self._run(job, fns, parallel, stop_on_failure, lock),
                                                  name=f"job-{job.id}")
        log.info("job %s started: %s (%d steps)", job.id, title, len(fns))
        return job

    def cancel(self, job: Job):
        """Stops after the steps already running; a step that is mid-update is never interrupted."""
        job.cancel_requested = True

    async def _run(self, job: Job, fns: list[StepFn], parallel: int, stop_on_failure: bool, lock: str | None):
        sem = asyncio.Semaphore(max(1, parallel))
        halted = False

        async def one(step: Step, fn: StepFn):
            nonlocal halted
            async with sem:
                if job.cancel_requested or halted:
                    step.status = "skipped"
                    step.message = "cancelled" if job.cancel_requested else "skipped after an earlier failure"
                    return
                step.status, step.started_at = "running", datetime.now(timezone.utc)
                ctx = StepContext(job, step)
                try:
                    msg = await fn(ctx)
                    ctx.flush()
                    step.status, step.message, step.progress = "ok", msg or step.message or "done", 1.0
                except StepFailed as e:
                    ctx.flush()
                    step.status, step.message = "failed", str(e)
                except Exception as e:      # noqa: BLE001 - a bug in one step must not kill the job
                    ctx.flush()
                    log.exception("job %s step %s crashed", job.id, step.label)
                    step.status, step.message = "failed", f"{type(e).__name__}: {e}"
                finally:
                    step.finished_at = datetime.now(timezone.utc)
                if step.status == "failed" and stop_on_failure:
                    halted = True

        try:
            await asyncio.gather(*(one(s, f) for s, f in zip(job.steps, fns)))
        finally:
            statuses = {s.status for s in job.steps}
            if "failed" in statuses:
                job.status = "partial" if "ok" in statuses else "failed"
            elif job.cancel_requested and "skipped" in statuses:
                job.status = "cancelled"
            else:
                job.status = "ok"
            job.finished_at = datetime.now(timezone.utc)
            if lock and self.exclusive.get(lock) == job.id:
                del self.exclusive[lock]
            self._tasks.pop(job.id, None)
            log.info("job %s finished: %s", job.id, job.status)

    async def wait(self, job: Job):
        t = self._tasks.get(job.id)
        if t:
            await t

    async def stop(self):
        for t in list(self._tasks.values()):
            t.cancel()
