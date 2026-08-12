"""Scoring retrieval against the labelled set.

Correctness is defined by time, not by text: a hit counts when the moment it
returned actually spans the second where the answer is spoken. That keeps the
judgement independent of how the extraction chose to word things — which is the
whole point, since the extraction is what is being evaluated.
"""

from dataclasses import dataclass
from pathlib import Path

import yaml

DATASET = Path(__file__).parent / "dataset.yaml"


@dataclass
class Case:
    query: str
    video_id: int
    at_sec: float
    source: str


@dataclass
class CaseResult:
    case: Case
    rank: int | None  # 1-based position of the first correct hit, None if absent
    top_title: str | None
    top_video_id: int | None

    @property
    def found(self) -> bool:
        return self.rank is not None


@dataclass
class RetrievalScore:
    results: list[CaseResult]

    def recall_at(self, k: int) -> float:
        if not self.results:
            return 0.0
        return sum(1 for r in self.results if r.rank is not None and r.rank <= k) / len(
            self.results
        )

    @property
    def mrr(self) -> float:
        """Mean reciprocal rank. Rewards being first, not merely present —
        which matches how the results are read: the top hit is the one that gets
        tapped."""
        if not self.results:
            return 0.0
        return sum(1 / r.rank for r in self.results if r.rank) / len(self.results)


def load_cases() -> list[Case]:
    if not DATASET.exists():
        return []
    raw = yaml.safe_load(DATASET.read_text(encoding="utf-8")) or {}
    return [Case(**entry) for entry in raw.get("queries", [])]


def score_case(case: Case, hits) -> CaseResult:
    """A hit is correct when it is the right video and its span contains the
    labelled second. Tolerance is deliberately zero: the moment boundaries are
    snapped to real transcript segments, so 'nearly containing it' means the
    extraction cut the idea in the wrong place."""
    rank = None
    for position, hit in enumerate(hits, start=1):
        if hit.video_id == case.video_id and hit.start_sec <= case.at_sec <= hit.end_sec:
            rank = position
            break

    top = hits[0] if hits else None
    return CaseResult(
        case=case,
        rank=rank,
        top_title=top.title if top else None,
        top_video_id=top.video_id if top else None,
    )
