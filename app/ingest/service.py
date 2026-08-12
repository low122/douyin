"""Business logic for turning a shared link into a stored video."""

import logging
from dataclasses import dataclass

import httpx
from arq import create_pool
from arq.connections import RedisSettings
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.models import FailureKind, IngestJob, JobStatus, LlmCall, Moment, Transcript, Video
from app.ingest.douyin import (
    ShapeChanged,
    TemporarilyBlocked,
    VideoUnavailable,
    fetch_metadata,
    resolve_short_url,
)
from app.extract.prompt import build_messages
from app.extract.schema import SCHEMA_VERSION
from app.extract.snap import snap_moments
from app.ingest.parse import UnparseableShare, parse_share_input
from app.search.text import build_search_text
from app.media.prepare import MediaError, prepared_media
from app.providers import openai_provider, pricing
from app.providers.base import MissingCredential, resolve_task
from app.providers.openai_provider import AudioTooLarge, ProviderAuthError
from app.transcribe.corrections import correct_transcription
from app.transcribe.vocabulary import transcription_prompt

log = logging.getLogger(__name__)


def classify(exc: Exception) -> str:
    """Decide whether retrying this failure could ever help.

    Retrying a deleted video three times only burns time; not retrying a
    timeout is needlessly fragile. See docs/design-decisions.md.
    """
    if isinstance(exc, MissingCredential | ProviderAuthError):
        # Affects every video until a human fixes the key or tops up the
        # account. Retrying buries the real cause under a wall of failures.
        return FailureKind.OPERATOR
    if isinstance(exc, UnparseableShare | VideoUnavailable | ShapeChanged | AudioTooLarge):
        return FailureKind.PERMANENT
    if isinstance(
        exc, ConnectionError | httpx.HTTPError | TimeoutError | MediaError | TemporarilyBlocked
    ):
        return FailureKind.TRANSIENT
    return FailureKind.TRANSIENT


async def create_job(session: AsyncSession, raw_input: str) -> IngestJob:
    """Record the request and return immediately.

    Parsing happens here because it is a regular expression — cheap, offline,
    and able to reject nonsense before it reaches a queue. Resolving the link
    needs a network round trip, so that waits for the worker; a third-party
    request has no business on the response path.
    """
    parsed = parse_share_input(raw_input)  # raises UnparseableShare -> 400
    job = IngestJob(
        raw_input=raw_input,
        short_url=parsed.short_url,
        status=JobStatus.QUEUED,
    )
    session.add(job)
    await session.flush()
    return job


async def submit_share(session: AsyncSession, raw_input: str) -> IngestJob:
    """Record a shared link and queue the work for it.

    Every entry point goes through here — the JSON API, the paste box, and
    whatever comes next. Two ways in that each build their own Redis pool and
    enqueue their own job name is precisely how one of them quietly stops
    matching the other, and the symptom would be a link that is accepted and
    then never processed.

    Committing before enqueuing is deliberate and in that order: the worker
    looks the job up by id, so the row has to be durable before anything can be
    told about it. The opposite order races, and loses.
    """
    job = await create_job(session, raw_input)  # raises UnparseableShare
    await session.commit()

    settings = get_settings()
    pool = await create_pool(RedisSettings.from_dsn(settings.redis_url))
    try:
        await pool.enqueue_job("process_ingest", job.id)
    finally:
        await pool.aclose()
    return job


async def _upsert_video(session: AsyncSession, meta) -> tuple[Video, bool]:
    """Insert the video, or find the one that already exists.

    Uniqueness is left to the database rather than checked first. Two workers
    can both look, both find nothing, and both insert — only the constraint
    settles it. The savepoint keeps the outer transaction usable after the
    conflict, which a plain rollback would not.

    Returns (video, is_duplicate).
    """
    video = Video(
        platform="douyin",
        aweme_id=meta.aweme_id,
        source_url=meta.source_url,
        author_name=meta.author_name,
        caption=meta.caption,
        duration_sec=meta.duration_sec,
        published_at=meta.published_at,
        raw_meta=meta.raw,
    )
    try:
        async with session.begin_nested():
            session.add(video)
        return video, False
    except IntegrityError:
        existing = await session.scalar(
            select(Video).where(Video.platform == "douyin", Video.aweme_id == meta.aweme_id)
        )
        if existing is None:  # pragma: no cover - would mean a different constraint failed
            raise
        return existing, True


@dataclass(frozen=True)
class StageResult:
    """Outcome of a stage, returned rather than raised.

    This is deliberate. If the stage raised, the exception would travel out
    through the transaction boundary and roll back the very row that recorded
    *why* it failed — leaving a job that failed silently, with no reason
    attached. Returning lets the transaction commit first; the caller then
    decides whether to raise in order to trigger a retry.
    """

    status: str
    failure_kind: str | None = None
    error: str | None = None

    # Fetch found the video already present; transcribe found a transcript
    # already present. Informational, never a stop signal — a retry of a job
    # that got halfway will legitimately find its own earlier work.
    already_present: bool = False

    @property
    def should_retry(self) -> bool:
        return self.failure_kind == FailureKind.TRANSIENT


async def run_fetch_stage(
    session: AsyncSession, client: httpx.AsyncClient, job_id: int
) -> StageResult:
    """Resolve the link, read the metadata, and attach a video to the job."""
    job = await session.get(IngestJob, job_id)
    if job is None:
        raise LookupError(f"ingest job {job_id} is gone")

    job.status = JobStatus.FETCHING
    job.attempt += 1
    await session.flush()

    try:
        parsed = parse_share_input(job.raw_input)
        aweme_id = parsed.aweme_id
        if aweme_id is None:
            aweme_id = await resolve_short_url(client, parsed.short_url)

        meta = await fetch_metadata(client, aweme_id)
        video, is_duplicate = await _upsert_video(session, meta)

        job.video_id = video.id
        # Not DONE — a fetched video still has to be transcribed — and not
        # terminal when the row already existed. A job retried after failing
        # mid-pipeline will find the video it created last time, which is not a
        # reason to stop. Whether any work remains is decided per stage.
        job.status = JobStatus.FETCHING
        job.failure_kind = None
        job.last_error = None
        await session.flush()
        log.info("job %s fetched aweme_id=%s (existing=%s)", job_id, aweme_id, is_duplicate)
        return StageResult(status=JobStatus.FETCHING, already_present=is_duplicate)

    except Exception as exc:
        return await _record_failure(session, job, job_id, exc)


async def _record_failure(session, job, job_id, exc) -> StageResult:
    kind = classify(exc)
    # Truncated: an error string is for a human reading the admin view, not
    # a place to accumulate megabytes of provider HTML.
    message = f"{type(exc).__name__}: {exc}"[:2000]
    job.status = JobStatus.FAILED
    job.failure_kind = kind
    job.last_error = message
    await session.flush()
    log.warning("job %s failed (%s): %s", job_id, kind, exc)
    return StageResult(status=JobStatus.FAILED, failure_kind=kind, error=message)


async def _media_url(client: httpx.AsyncClient, video: Video) -> str:
    """The playable URL, refreshed if the stored one has gone stale.

    The fetch stage recorded one seconds earlier, so it is normally current.
    A job retried hours later is the case worth handling — re-reading the share
    page costs one small request, and only when the stored URL actually fails.
    """
    stored = ((video.raw_meta or {}).get("video") or {}).get("play_addr") or {}
    urls = stored.get("url_list") or []
    if urls:
        return urls[0]

    meta = await fetch_metadata(client, video.aweme_id)
    if not meta.media_url:
        raise MediaError(f"no playable URL for {video.aweme_id}")
    return meta.media_url


async def run_media_stage(
    session: AsyncSession, client: httpx.AsyncClient, job_id: int
) -> StageResult:
    """Download once, then derive everything from it.

    Transcription, frame selection and extraction all need the same media, so
    they share one download and one temporary directory. Frames in particular
    are never stored (ADR-0003), which means extraction has to happen while they
    still exist rather than in a later stage.

    Skipping is decided on moments, not on the transcript: a video with a
    transcript but no moments still has work left.
    """
    job = await session.get(IngestJob, job_id)
    if job is None or job.video_id is None:
        raise LookupError(f"job {job_id} has no video to process")

    video = await session.get(Video, job.video_id)

    # Skipping is keyed on the version alone. It used to include the model, so
    # that two models could be extracted side by side and compared — which is how
    # gpt-4o was chosen (ADR-0008). That was only ever half-built: search filtered
    # on the version and not the model, so the two sets it carefully kept apart
    # came back interleaved, under near-identical titles. The comparison has been
    # made and written down, so a video now has one live set of moments and
    # `model` is a label on them rather than part of their identity.
    already_extracted = await session.scalar(
        select(func.count())
        .select_from(Moment)
        .where(
            Moment.video_id == video.id,
            Moment.schema_version == SCHEMA_VERSION,
        )
    )
    if already_extracted:
        log.info(
            "job %s: %d moments already at v%s, skipping",
            job_id, already_extracted, SCHEMA_VERSION,
        )
        job.status = JobStatus.DONE
        await session.flush()
        return StageResult(status=JobStatus.DONE, already_present=True)

    try:
        transcript = await session.scalar(
            select(Transcript).where(Transcript.video_id == video.id)
        )
        transcribe_config = resolve_task("transcribe")
        extract_config = resolve_task("extract")
        url = await _media_url(client, video)

        async with prepared_media(client, url) as media:
            if transcript is None:
                job.status = JobStatus.TRANSCRIBING
                await session.flush()
                spoken = await openai_provider.transcribe(
                    transcribe_config,
                    media.audio_path,
                    prompt=transcription_prompt(),
                )
                # Prevention above, repair here. The prompt makes a mishearing
                # less likely; it cannot make it impossible, and everything
                # downstream reads the transcript as fact — so the last chance to
                # fix a known bad spelling is before it is written.
                spoken, corrected = correct_transcription(spoken)
                transcript = Transcript(
                    video_id=video.id,
                    provider=transcribe_config.provider,
                    model=transcribe_config.model,
                    language=spoken.language,
                    duration_sec=spoken.duration_sec,
                    full_text=spoken.text,
                    segments=spoken.segments,
                    words=spoken.words,
                )
                session.add(transcript)
                session.add(
                    LlmCall(
                        video_id=video.id,
                        task="transcribe",
                        provider=transcribe_config.provider,
                        model=transcribe_config.model,
                        audio_seconds=round(spoken.duration_sec) if spoken.duration_sec else None,
                        # None when the model is absent from the verified price
                        # table. A null cost beside real usage is recoverable; a
                        # guessed number is not.
                        cost_usd=pricing.transcription_cost(
                            transcribe_config.provider,
                            transcribe_config.model,
                            spoken.duration_sec,
                        ),
                        latency_ms=spoken.latency_ms,
                    )
                )
                await session.flush()
                # The correction count is logged because a silent rewrite of the
                # transcript is exactly the kind of thing that should be visible:
                # a number climbing here means the vocabulary prompt is not
                # doing its job, and a number appearing for a term nobody
                # expected means an entry is firing on something it should not.
                log.info(
                    "job %s transcribed: %s chars, %s segments, %s corrections, %s ms",
                    job_id, len(spoken.text), len(spoken.segments), corrected,
                    spoken.latency_ms,
                )
            else:
                log.info("job %s: reusing existing transcript", job_id)

            job.status = JobStatus.EXTRACTING
            await session.flush()

            segments = transcript.segments or []
            messages = build_messages(
                caption=video.caption,
                author=video.author_name,
                segments=segments,
            )
            outcome = await openai_provider.extract_moments(extract_config, messages)
        # Media is gone by here, on this path and every other.

        moments, moved = snap_moments(outcome.result.moments, segments)
        if moved:
            # Surfaced rather than silently corrected: a model that routinely
            # invents timestamps is a prompt problem, and hiding it would make
            # the snapping look like a success.
            log.warning(
                "job %s: %d/%d moment boundaries were not real segment times",
                job_id, moved, len(moments),
            )

        # The retire path the old design never had. Anything this video carried
        # from an earlier schema or a different model goes now, after the new
        # extraction has come back and been snapped — so a call that fails or
        # returns nothing leaves the previous set untouched rather than clearing
        # it in advance and hoping.
        removed = await session.execute(
            delete(Moment).where(Moment.video_id == video.id)
        )
        if removed.rowcount:
            log.info("job %s: replaced %d superseded moments", job_id, removed.rowcount)

        for moment in moments:
            session.add(
                Moment(
                    video_id=video.id,
                    schema_version=SCHEMA_VERSION,
                    provider=extract_config.provider,
                    model=extract_config.model,
                    start_sec=moment.start_sec,
                    end_sec=moment.end_sec,
                    title=moment.title[:200],
                    summary=moment.summary,
                    keywords=moment.keywords,
                    evidence_strength=moment.evidence_strength,
                    relevance=moment.relevance,
                )
            )
        session.add(
            LlmCall(
                video_id=video.id,
                task="extract",
                provider=extract_config.provider,
                model=extract_config.model,
                input_tokens=outcome.input_tokens,
                output_tokens=outcome.output_tokens,
                cost_usd=pricing.token_cost(
                    extract_config.provider,
                    extract_config.model,
                    outcome.input_tokens,
                    outcome.output_tokens,
                ),
                latency_ms=outcome.latency_ms,
            )
        )

        job.status = JobStatus.DONE
        job.failure_kind = None
        job.last_error = None
        await session.flush()
        log.info(
            "job %s extracted %d moments, %s ms", job_id, len(moments), outcome.latency_ms,
        )
        return StageResult(status=JobStatus.DONE)

    except Exception as exc:
        return await _record_failure(session, job, job_id, exc)


def _spoken_within(segments: list[dict], start: float, end: float, limit: int = 1500) -> str:
    """The transcript text falling inside a moment's span.

    Capped: a very long moment should not push the summary's weight down to
    nothing in the embedding, and the tail of a span adds little once the topic
    is established.
    """
    parts = []
    used = 0
    for segment in segments:
        seg_start = segment.get("start")
        if seg_start is None or not (start <= float(seg_start) <= end):
            continue
        text = (segment.get("text") or "").strip()
        if not text:
            continue
        if used + len(text) > limit:
            break
        parts.append(text)
        used += len(text)
    return " ".join(parts)


async def run_embed_stage(session: AsyncSession, job_id: int) -> StageResult:
    """Make the video's moments findable.

    Needs no media, so it runs outside the download context. Only moments that
    are missing an embedding are sent, which makes a retry cheap and makes a
    partial failure resumable.
    """
    job = await session.get(IngestJob, job_id)
    if job is None or job.video_id is None:
        raise LookupError(f"job {job_id} has no video to embed")

    pending = (
        await session.scalars(
            select(Moment).where(
                Moment.video_id == job.video_id,
                Moment.schema_version == SCHEMA_VERSION,
                # No model filter: a video has one live set of moments, so
                # "missing an embedding" is the whole condition. Filtering on the
                # configured model here used to mean that changing EXTRACT_MODEL
                # left the previous set permanently unembedded and unsearchable.
                Moment.embedding.is_(None),
            )
        )
    ).all()

    if not pending:
        return StageResult(status=JobStatus.DONE, already_present=True)

    job.status = JobStatus.EMBEDDING
    await session.flush()

    try:
        config = resolve_task("embed")
        transcript = await session.scalar(
            select(Transcript).where(Transcript.video_id == job.video_id)
        )
        segments = (transcript.segments if transcript else []) or []

        # The spoken words go in alongside the summary, not instead of it.
        # Indexing the summary alone means searching a compression of the
        # content: a 53-second moment carrying 223 characters of speech was
        # being represented by a 60-character summary, and the phrases someone
        # would actually type — the speaker's own turns of phrase — were not in
        # it. Measured as three misses out of six on the retrieval set.
        texts = [
            " ".join(
                filter(
                    None,
                    [
                        m.title,
                        m.summary,
                        " ".join(m.keywords or []),
                        _spoken_within(segments, float(m.start_sec), float(m.end_sec)),
                    ],
                )
            )
            for m in pending
        ]
        outcome = await openai_provider.embed_texts(config, texts)

        for moment, vector, raw in zip(pending, outcome.vectors, texts, strict=True):
            moment.embedding = vector
            # Segmented with the same function the query will use. Doing it on
            # one side only is a silent miss, not an error.
            moment.search_text = build_search_text(raw)

        session.add(
            LlmCall(
                video_id=job.video_id,
                task="embed",
                provider=config.provider,
                model=config.model,
                input_tokens=outcome.input_tokens,
                cost_usd=pricing.token_cost(
                    config.provider, config.model, outcome.input_tokens, 0
                ),
                latency_ms=outcome.latency_ms,
            )
        )

        job.status = JobStatus.DONE
        job.failure_kind = None
        job.last_error = None
        await session.flush()
        log.info("job %s embedded %d moments, %s ms", job_id, len(pending), outcome.latency_ms)
        return StageResult(status=JobStatus.DONE)

    except Exception as exc:
        return await _record_failure(session, job, job_id, exc)


async def run_pipeline(
    session: AsyncSession, client: httpx.AsyncClient, job_id: int
) -> StageResult:
    """Run the stages in order, stopping at the first that does not succeed.

    Stages are individually skippable, so a retry picks up where the last
    attempt stopped instead of re-downloading and re-paying for work that
    already landed.
    """
    fetched = await run_fetch_stage(session, client, job_id)
    if fetched.status == JobStatus.FAILED:
        return fetched

    result = await run_media_stage(session, client, job_id)
    if result.status != JobStatus.DONE:
        return result

    embedded = await run_embed_stage(session, job_id)
    if embedded.status != JobStatus.DONE:
        return embedded

    # Nothing new was produced: the video was already here and so was its
    # transcript. Worth reporting distinctly so a re-share reads as "already
    # saved" rather than as fresh work.
    if fetched.already_present and result.already_present:
        job = await session.get(IngestJob, job_id)
        job.status = JobStatus.DUPLICATE
        await session.flush()
        return StageResult(status=JobStatus.DUPLICATE, already_present=True)

    return result
