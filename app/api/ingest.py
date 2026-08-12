from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import IngestJob, JobStatus
from app.db.session import get_session
from app.ingest.parse import UnparseableShare
from app.ingest.service import submit_share

router = APIRouter(tags=["ingest"])


class IngestRequest(BaseModel):
    # The whole share blob, pasted verbatim. Tidying it up is this service's
    # job, not the caller's — a shortcut on a phone should not have to parse.
    text: str = Field(min_length=1, max_length=4000)


class IngestResponse(BaseModel):
    job_id: int
    status: str


class JobSummary(BaseModel):
    id: int
    status: str
    failure_kind: str | None
    last_error: str | None
    video_id: int | None


@router.post("/ingest", status_code=status.HTTP_202_ACCEPTED, response_model=IngestResponse)
async def ingest(payload: IngestRequest, session: AsyncSession = Depends(get_session)):
    """Accept a shared link and return before any of the work happens.

    202 rather than 200: the link has been recorded, not processed. Processing
    takes minutes and cannot sit on an HTTP request.
    """
    try:
        job = await submit_share(session, payload.text)
    except UnparseableShare as exc:
        # Rejected here rather than queued: no link means no amount of retrying
        # will help, so failing now gives the caller an immediate answer.
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    return IngestResponse(job_id=job.id, status=job.status)


@router.get("/jobs/{job_id}", response_model=JobSummary)
async def get_job(job_id: int, session: AsyncSession = Depends(get_session)):
    job = await session.get(IngestJob, job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such job")
    return JobSummary(
        id=job.id,
        status=job.status,
        failure_kind=job.failure_kind,
        last_error=job.last_error,
        video_id=job.video_id,
    )


@router.get("/jobs")
async def recent_jobs(limit: int = 20, session: AsyncSession = Depends(get_session)):
    """What the shortcut's follow-up check reads.

    Includes a count of failures so the caller can surface "3 done, 1 failed"
    without walking the list — silent failure is the thing being defended
    against (ADR-0001 applies to the pipeline as much as to extraction).
    """
    rows = (
        await session.scalars(
            select(IngestJob).order_by(IngestJob.created_at.desc()).limit(min(limit, 100))
        )
    ).all()
    failed = await session.scalar(
        select(func.count())
        .select_from(IngestJob)
        .where(IngestJob.status == JobStatus.FAILED)
    )
    return {
        "failed_total": failed or 0,
        "jobs": [
            {
                "id": j.id,
                "status": j.status,
                "failure_kind": j.failure_kind,
                "created_at": j.created_at,
            }
            for j in rows
        ],
    }
