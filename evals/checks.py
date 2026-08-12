"""Structural checks on extraction output, requiring no human labelling.

These do not ask whether a summary is *good* — that needs judgement. They ask
whether the output has properties it must have to be usable at all: moments that
span the video, boundaries that exist, a language the user can search in, scores
that actually discriminate.

Cheap enough to run after every extraction, which is the point. A labelled set
answers deeper questions but costs an afternoon to build and goes stale; these
catch the failures that make a labelled set pointless to run.
"""

import re
from dataclasses import dataclass, field

CJK = re.compile(r"[一-鿿]")


@dataclass
class Finding:
    check: str
    passed: bool
    detail: str
    value: float | None = None


@dataclass
class VideoReport:
    video_id: int
    author: str | None
    duration_sec: float
    moment_count: int
    findings: list[Finding] = field(default_factory=list)

    @property
    def failures(self) -> list[Finding]:
        return [f for f in self.findings if not f.passed]


def cjk_ratio(text: str) -> float:
    """Share of CJK characters, ignoring spaces and punctuation."""
    letters = [c for c in text if c.isalnum()]
    if not letters:
        return 0.0
    return sum(1 for c in letters if CJK.match(c)) / len(letters)


def check_coverage(moments: list[dict], duration: float, floor: float = 0.5) -> Finding:
    """How much of the video the moments actually account for.

    A run that covers a tenth of its source has produced a table of contents.
    The floor is deliberately generous — filler, sign-offs and channel promotion
    are legitimately not worth a moment.
    """
    covered = sum(m["end_sec"] - m["start_sec"] for m in moments)
    ratio = covered / duration if duration else 0.0
    return Finding(
        check="coverage",
        passed=ratio >= floor,
        detail=f"{ratio:.1%} of {duration:.0f}s covered by {len(moments)} moments",
        value=ratio,
    )


def check_boundaries_are_real(moments: list[dict], segments: list[dict]) -> Finding:
    """Every boundary must be a timestamp something was actually said at.

    Snapping already enforces this, so a failure here means snapping did not run
    or ran against the wrong transcript — worth catching, because the symptom
    otherwise is a deep link that lands mid-sentence and nobody reports it.
    """
    starts = {round(float(s["start"]), 2) for s in segments if s.get("start") is not None}
    ends = {round(float(s["end"]), 2) for s in segments if s.get("end") is not None}
    if not starts:
        return Finding("boundaries_real", True, "no transcript segments to check against")

    bad = [
        m
        for m in moments
        if round(m["start_sec"], 2) not in starts or round(m["end_sec"], 2) not in ends
    ]
    return Finding(
        check="boundaries_real",
        passed=not bad,
        detail=f"{len(moments) - len(bad)}/{len(moments)} boundaries match a real segment",
        value=1 - len(bad) / len(moments) if moments else 1.0,
    )


def check_language_matches(moments: list[dict], transcript_text: str) -> Finding:
    """Moments must be written in the language of the video.

    Not cosmetic. Full-text retrieval matches tokens, so a Chinese video
    summarised in English cannot be found by a Chinese query — half of hybrid
    search silently stops contributing for that video, and the ranking still
    looks reasonable because the vector half carries it.
    """
    source_cjk = cjk_ratio(transcript_text)
    mismatched = []
    for moment in moments:
        written = cjk_ratio(f"{moment['title']} {moment['summary']}")
        # A 0.3 gap is wide enough to ignore technical terms in an otherwise
        # Chinese summary, and narrow enough to catch a wholly English one.
        if abs(written - source_cjk) > 0.3:
            mismatched.append(moment)

    return Finding(
        check="language_matches",
        passed=not mismatched,
        detail=(
            f"{len(mismatched)}/{len(moments)} moments differ from the source language "
            f"(transcript is {source_cjk:.0%} CJK)"
        ),
        value=1 - len(mismatched) / len(moments) if moments else 1.0,
    )


def check_relevance_discriminates(moments: list[dict]) -> Finding:
    """The relevance score has to separate things, or it is decoration.

    Only a degenerate distribution fails: one value repeated across every
    moment means nothing was ranked, and query-time filtering on that score
    would be filtering on noise.

    A *narrow* spread is not a defect and used to be flagged as one. When a run
    correctly drops the filler, what remains is legitimately all worth keeping
    and the scores cluster high — the first version of this check failed a video
    for being right. A check that fires when the model is correct is worse than
    no check, because it teaches you to ignore the output.
    """
    scores = [m["relevance"] for m in moments if m.get("relevance") is not None]
    if len(scores) < 2:
        return Finding("relevance_spread", True, "too few scored moments to judge")

    distinct = len(set(scores))
    spread = max(scores) - min(scores)
    note = "" if spread >= 0.2 else "  (narrow, but discriminating)"
    return Finding(
        check="relevance_spread",
        passed=distinct > 1,
        detail=f"{distinct} distinct values over {min(scores):.2f}-{max(scores):.2f}{note}",
        value=spread,
    )


def check_searchable(moments: list[dict]) -> Finding:
    """A moment with no embedding and no search text cannot be found at all."""
    unreachable = [
        m for m in moments if not m.get("has_embedding") or not m.get("has_search_text")
    ]
    return Finding(
        check="searchable",
        passed=not unreachable,
        detail=f"{len(moments) - len(unreachable)}/{len(moments)} moments are retrievable",
        value=1 - len(unreachable) / len(moments) if moments else 1.0,
    )


def check_no_heavy_overlap(moments: list[dict], tolerance: float = 0.5) -> Finding:
    """Moments should partition the video, not restate each other.

    Overlap means the same seconds are described twice, which inflates coverage
    while adding nothing — worth catching because coverage is the headline
    number and this is how it gets gamed by accident.
    """
    ordered = sorted(moments, key=lambda m: m["start_sec"])
    overlaps = [
        (a, b)
        for a, b in zip(ordered, ordered[1:], strict=False)
        if b["start_sec"] < a["end_sec"] - tolerance
    ]
    return Finding(
        check="no_heavy_overlap",
        passed=not overlaps,
        detail=f"{len(overlaps)} overlapping pair(s)",
        value=1 - len(overlaps) / max(len(ordered) - 1, 1),
    )


def run_all(
    *, video_id: int, author: str | None, duration: float,
    moments: list[dict], segments: list[dict], transcript_text: str,
) -> VideoReport:
    report = VideoReport(
        video_id=video_id, author=author, duration_sec=duration, moment_count=len(moments)
    )
    if not moments:
        report.findings.append(Finding("has_moments", False, "no moments extracted"))
        return report

    report.findings = [
        check_coverage(moments, duration),
        check_boundaries_are_real(moments, segments),
        check_language_matches(moments, transcript_text),
        check_relevance_discriminates(moments),
        check_searchable(moments),
        check_no_heavy_overlap(moments),
    ]
    return report
