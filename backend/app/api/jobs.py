"""Background jobs (content pushes, updates): list, follow, cancel."""
from fastapi import APIRouter, Depends, HTTPException, Request

from ..jobs import JobManager

router = APIRouter(prefix="/api/jobs")


def jobs(request: Request) -> JobManager:
    return request.app.state.jobs


@router.get("")
async def list_jobs(mgr: JobManager = Depends(jobs)):
    return [j.summary() for j in mgr.jobs]


@router.get("/{job_id}")
async def get_job(job_id: str, mgr: JobManager = Depends(jobs)):
    j = mgr.get(job_id)
    if j is None:
        raise HTTPException(404, "job not found (jobs are kept in memory until the service restarts)")
    return j


@router.post("/{job_id}/cancel")
async def cancel_job(job_id: str, mgr: JobManager = Depends(jobs)):
    """Skips steps that haven't started. A controller that is mid-update is left to finish."""
    j = mgr.get(job_id)
    if j is None:
        raise HTTPException(404, "job not found")
    if j.status != "running":
        raise HTTPException(409, f"job already {j.status}")
    mgr.cancel(j)
    return j.summary()
