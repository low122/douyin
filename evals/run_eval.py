#!/usr/bin/env python
"""Score the pipeline's output.

    python evals/run_eval.py

Runs the label-free structural checks over every extracted video and, when
evals/dataset.yaml has entries, the retrieval set as well. Exits non-zero if
anything fails, so it can gate a change rather than merely describe one.
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.db.models import Moment, Transcript, Video  # noqa: E402
from app.db.session import dispose_engine, init_engine, session_scope  # noqa: E402
from app.extract.schema import SCHEMA_VERSION  # noqa: E402
from app.providers import openai_provider  # noqa: E402
from app.providers.base import resolve_task  # noqa: E402
from app.search.hybrid import hybrid_search  # noqa: E402
from evals.checks import run_all  # noqa: E402
from evals.retrieval import RetrievalScore, load_cases, score_case  # noqa: E402

TICK = {True: "PASS", False: "FAIL"}


async def collect_reports(session, model: str):
    videos = (await session.scalars(select(Video).order_by(Video.id))).all()
    reports = []

    for video in videos:
        rows = (
            await session.scalars(
                select(Moment)
                .where(
                    Moment.video_id == video.id,
                    Moment.schema_version == SCHEMA_VERSION,
                    Moment.model == model,
                )
                .order_by(Moment.start_sec)
            )
        ).all()
        if not rows:
            continue

        transcript = await session.scalar(
            select(Transcript).where(Transcript.video_id == video.id)
        )

        moments = [
            {
                "start_sec": float(m.start_sec),
                "end_sec": float(m.end_sec),
                "title": m.title,
                "summary": m.summary,
                "relevance": float(m.relevance) if m.relevance is not None else None,
                "has_embedding": m.embedding is not None,
                "has_search_text": bool(m.search_text),
            }
            for m in rows
        ]

        reports.append(
            run_all(
                video_id=video.id,
                author=video.author_name,
                duration=float(transcript.duration_sec or video.duration_sec or 0),
                moments=moments,
                segments=(transcript.segments if transcript else []) or [],
                transcript_text=(transcript.full_text if transcript else "") or "",
            )
        )

    return reports


def print_reports(reports, model: str) -> int:
    if not reports:
        print(f"no extracted videos at schema v{SCHEMA_VERSION} / {model}")
        return 1

    failures = 0
    print(f"\nStructural checks — schema v{SCHEMA_VERSION}, model {model}")
    print("=" * 72)

    for report in reports:
        print(f"\nvideo {report.video_id}  {report.author or '?'}  "
              f"{report.duration_sec:.0f}s  {report.moment_count} moments")
        for finding in report.findings:
            print(f"   {TICK[finding.passed]:4}  {finding.check:20} {finding.detail}")
        failures += len(report.failures)

    print("\n" + "=" * 72)
    # Aggregates across videos: one bad video is noise, the same failure on
    # every video is a systematic problem with the prompt or the model.
    by_check: dict[str, list[bool]] = {}
    for report in reports:
        for finding in report.findings:
            by_check.setdefault(finding.check, []).append(finding.passed)

    for check, results in by_check.items():
        passed = sum(results)
        marker = "" if passed == len(results) else "   <-- every video" if passed == 0 else ""
        print(f"  {check:20} {passed}/{len(results)} videos{marker}")

    print(f"\n{failures} failing check(s) across {len(reports)} video(s)")
    return 1 if failures else 0


async def run_retrieval(session) -> int:
    """Score the labelled query set, if there is one."""
    cases = load_cases()
    if not cases:
        print("\nNo retrieval cases in evals/dataset.yaml — structural checks only.")
        return 0

    config = resolve_task("embed")
    results = []
    for case in cases:
        embedded = await openai_provider.embed_texts(config, [case.query])
        hits = await hybrid_search(
            session,
            query=case.query,
            query_embedding=embedded.vectors[0],
            schema_version=SCHEMA_VERSION,
            limit=5,
        )
        results.append(score_case(case, hits))

    score = RetrievalScore(results)

    print(f"\nRetrieval — {len(cases)} labelled queries")
    print("=" * 72)
    for result in results:
        mark = f"rank {result.rank}" if result.found else "MISS"
        print(f"\n  {mark:8}  {result.case.query}")
        print(f"            expected video {result.case.video_id} @ {result.case.at_sec:.0f}s")
        if not result.found:
            # What it returned instead is the useful part of a miss; the miss
            # alone says nothing about why.
            print(f"            top hit: video {result.top_video_id} — {result.top_title}")

    print("\n" + "=" * 72)
    print(f"  Recall@1   {score.recall_at(1):.0%}")
    print(f"  Recall@3   {score.recall_at(3):.0%}")
    print(f"  Recall@5   {score.recall_at(5):.0%}")
    print(f"  MRR        {score.mrr:.3f}")

    # Only a total miss fails the run. Ranking second on a six-query seed set is
    # noise, and a gate that fires on noise stops being read.
    missed = sum(1 for r in results if not r.found)
    print(f"\n  {missed} query(ies) returned nothing correct in the top 5")
    return 1 if missed else 0


async def main() -> int:
    settings = get_settings()
    init_engine(settings.database_url)
    try:
        async with session_scope() as session:
            reports = await collect_reports(session, settings.extract_model)
            structural = print_reports(reports, settings.extract_model)
            retrieval = await run_retrieval(session)
        return structural or retrieval
    finally:
        await dispose_engine()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
