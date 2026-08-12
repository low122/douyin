"""arq worker. Runs the stages that are too slow to sit on an HTTP request.

    arq app.worker.WorkerSettings
"""

import logging

import httpx
from arq.connections import RedisSettings

from app.config import get_settings
from app.db.session import dispose_engine, init_engine, session_scope
from app.logging_config import configure_logging
from app.ingest.douyin import MOBILE_UA
from app.ingest.service import run_pipeline

log = logging.getLogger(__name__)


class TransientStageError(Exception):
    """Raised only to make arq retry. The reason is already in the database."""


async def process_ingest(ctx: dict, job_id: int) -> str:
    """Run one ingest job.

    Whether this raises is what decides retry behaviour, so it is the one place
    the failure taxonomy is acted on: transient failures raise and get another
    attempt with backoff, permanent ones return quietly. Retrying a deleted
    video two more times accomplishes nothing except delay.
    """
    async with session_scope() as session:
        result = await run_pipeline(session, ctx["http"], job_id)
    # The transaction has committed by here, so the failure row survives the
    # raise below. Raising before the commit would roll away the explanation.

    if result.should_retry:
        raise TransientStageError(result.error or "transient failure")

    return result.status


async def startup(ctx: dict) -> None:
    configure_logging()
    settings = get_settings()
    init_engine(settings.database_url)
    # One client for the worker's lifetime: connection reuse matters when every
    # job makes several requests to the same two hosts.
    ctx["http"] = httpx.AsyncClient(timeout=30.0, headers={"User-Agent": MOBILE_UA})
    log.info("worker started")


async def shutdown(ctx: dict) -> None:
    await ctx["http"].aclose()
    await dispose_engine()
    log.info("worker stopped")


class WorkerSettings:
    functions = [process_ingest]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)

    # Three attempts total, which matches the retry budget the failure taxonomy
    # assumes. Only transient failures ever consume more than one.
    max_tries = 3
    # Generous: a long video's media handling is minutes of work in M2.
    job_timeout = 900
