"""Apply the current correction rules to data that is already stored.

Adding a rule to `app/transcribe/corrections.py` only helps videos ingested
after it. Everything already in the database keeps the spelling it was saved
with, which for a search index means the term stays unfindable — so a rule
without a backfill is half a fix.

Run it dry first. It prints what would change and touches nothing:

    docker compose exec api python scripts/backfill_corrections.py
    docker compose exec api python scripts/backfill_corrections.py --apply

What `--apply` does, in one transaction per step:

1. Rewrites `transcript.full_text`, `.segments` and `.words`.
2. Rewrites `moment.title`, `.summary` and `.keywords`.
3. Clears `embedding` and `search_text` on every moment it changed.
4. Re-runs the ordinary embed stage, which regenerates both from the corrected
   text — the same code path an ingest uses, so this cannot drift from it.

Only step 4 costs anything, and only for the moments that actually changed:
embeddings are the cheapest model in the pipeline and nothing is re-transcribed.
Repairing the *stored* text does not need whisper — the vocabulary prompt is
what improves future transcripts, and it cannot retroactively improve old ones.

Safe to run twice: text that is already correct produces no substitutions, and a
moment with nothing to change keeps its embedding.
"""

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.db.models import IngestJob, Moment, Transcript  # noqa: E402
from app.db.session import dispose_engine, init_engine, session_scope  # noqa: E402
from app.extract.schema import SCHEMA_VERSION  # noqa: E402
from app.ingest.service import run_embed_stage  # noqa: E402
from app.transcribe.corrections import CORRECTIONS, correct_text, correct_transcription  # noqa: E402
from app.providers.base import TranscriptionResult  # noqa: E402


async def _fix_transcripts(session: AsyncSession, apply: bool) -> tuple[int, set[int]]:
    total = 0
    changed: set[int] = set()
    for transcript in (await session.scalars(select(Transcript))).all():
        # Routed through the same function the pipeline uses so the two cannot
        # disagree about which fields carry text.
        fixed, n = correct_transcription(
            TranscriptionResult(
                text=transcript.full_text,
                segments=transcript.segments or [],
                words=transcript.words,
            )
        )
        if not n:
            continue
        total += n
        changed.add(transcript.video_id)
        print(f"  transcript video={transcript.video_id}: {n} substitutions")
        if apply:
            # Reassigned rather than mutated: SQLAlchemy does not see an in-place
            # edit of a JSONB list, and the write would be dropped in silence.
            transcript.full_text = fixed.text
            transcript.segments = fixed.segments
            transcript.words = fixed.words
    return total, changed


async def _fix_moments(
    session: AsyncSession, apply: bool, transcripts_changed: set[int]
) -> tuple[int, set[int]]:
    """Repair moment text, and clear the embedding of anything now out of date.

    Out of date covers more than the moments whose own fields changed. A
    moment's indexed text is its title, summary, keywords *and the speech inside
    its time range* — so correcting a transcript silently invalidates the index
    of every moment in that video, including ones whose own columns are
    untouched. Missing that was the first version of this script: it fixed three
    visible strings and left three more sitting in the index, which is exactly
    the half-repaired state the whole exercise is about.
    """
    settings = get_settings()
    moments = (
        await session.scalars(
            select(Moment).where(
                Moment.schema_version == SCHEMA_VERSION,
                Moment.model == settings.extract_model,
            )
        )
    ).all()

    total = 0
    touched: set[int] = set()
    for moment in moments:
        title, a = correct_text(moment.title or "")
        summary, b = correct_text(moment.summary or "")
        keywords, c = [], 0
        for keyword in moment.keywords or []:
            fixed, n = correct_text(keyword)
            keywords.append(fixed)
            c += n

        n = a + b + c
        stale = n > 0 or moment.video_id in transcripts_changed
        if not stale:
            continue

        total += n
        touched.add(moment.video_id)
        why = f"{n} substitutions" if n else "transcript changed"
        print(f"  moment {moment.id} (video={moment.video_id}): {why} — {moment.title!r}")
        if apply:
            moment.title = title
            moment.summary = summary
            moment.keywords = keywords
            # Cleared so the embed stage picks the moment up: it selects on a
            # null embedding, which is also what makes this resumable.
            moment.embedding = None
            moment.search_text = None
    return total, touched


async def main(apply: bool) -> int:
    print(f"Rules in effect: {CORRECTIONS}\n")

    # The app lifespan normally does this; a script has no lifespan.
    init_engine(get_settings().database_url)
    try:
        return await _run(apply)
    finally:
        await dispose_engine()


async def _run(apply: bool) -> int:
    async with session_scope() as session:
        print("Transcripts:")
        t_total, t_changed = await _fix_transcripts(session, apply)
        print(f"  {t_total} substitutions\n")

        print("Moments (text repaired, and index invalidated where stale):")
        m_total, touched = await _fix_moments(session, apply, t_changed)
        print(f"  {m_total} substitutions across {len(touched)} video(s)\n")

        if not apply:
            print("Dry run. Re-run with --apply to write and re-embed.")
            return 0

        await session.commit()

        if not touched:
            print("Nothing to re-embed.")
            return 0

        print("Re-embedding:")
        for video_id in sorted(touched):
            job = await session.scalar(
                select(IngestJob)
                .where(IngestJob.video_id == video_id)
                .order_by(IngestJob.created_at.desc())
                .limit(1)
            )
            if job is None:
                # A moment can outlive its job. Say so rather than skipping in
                # silence, because the moment is left unsearchable either way.
                print(f"  video {video_id}: no job found — embedding NOT rebuilt")
                continue
            result = await run_embed_stage(session, job.id)
            await session.commit()
            print(f"  video {video_id}: {result.status}")

    print("\nDone.")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply", action="store_true", help="write the changes (default: dry run)"
    )
    raise SystemExit(asyncio.run(main(parser.parse_args().apply)))
