"""The shape extraction must produce.

Bumped whenever the meaning of a field changes or a field is added, because
moments are stored with the version that produced them: re-running extraction
adds rows beside the old ones instead of overwriting, so a prompt change that
makes things worse stays visible.
"""

from pydantic import BaseModel, Field, field_validator

# v1 produced moments that tracked transcript lines rather than ideas: ten
# fragments averaging under two seconds, covering 7.6% of a five-minute video,
# with summaries describing that a topic was discussed instead of what was said.
# v2 adds frame_notes ahead of moments so the frames are read before anything is
# written, and states the size and coverage expectations numerically.
# v3 pins the output language to the transcript rather than "the video": a deck
# of English slides over Chinese narration was being summarised in English,
# which the structural checks caught and which removes that video from Chinese
# full-text retrieval entirely.
# v4 drops the 15-to-60-second rule and asks for completeness and coverage
# instead. On a rapid-fire Q&A the fixed range was unsatisfiable, and the model
# resolved the conflict by omitting content — 44.8% coverage with two whole
# answers missing. A stated length is easy to obey by leaving things out.
# v5 removes frame_notes: frames are gone (ADR-0009), so a field asking the model
# to transcribe images it was never sent is an instruction it can only obey by
# inventing.
SCHEMA_VERSION = 5

EVIDENCE_STRENGTH = ("strong", "anecdotal", "unsupported")


class ExtractedMoment(BaseModel):
    """One self-contained piece of information, anchored in time."""

    start_sec: float = Field(
        ge=0,
        description=(
            "Start time in seconds, copied from the first transcript segment of "
            "this moment. Do not compute or estimate it."
        ),
    )
    end_sec: float = Field(
        ge=0,
        description=(
            "End time in seconds, copied from the last transcript segment of "
            "this moment. Do not compute or estimate it."
        ),
    )
    title: str = Field(
        max_length=120,
        description="One line naming what this moment is about, in the source language.",
    )
    summary: str = Field(
        description=(
            "Two or three sentences on what is actually said or shown here. "
            "State the substance, not that the speaker discusses a topic."
        )
    )
    keywords: list[str] = Field(
        default_factory=list,
        description=(
            "Proper nouns and technical terms appearing in this moment — tools, "
            "products, people, named concepts. Used for exact matching, so keep "
            "the surface form rather than translating or expanding it."
        ),
    )
    evidence_strength: str = Field(
        description=(
            "'strong' when a claim is backed by data, a mechanism, or a worked "
            "example; 'anecdotal' when it rests on a single story or the "
            "speaker's authority; 'unsupported' when it is asserted with nothing "
            "behind it."
        )
    )
    relevance: float = Field(
        ge=0,
        le=1,
        description=(
            "How much durable value this moment carries, 0 to 1. Filler, "
            "greetings, and calls to subscribe are near 0. Nothing is discarded "
            "on the basis of this score — it only ranks."
        ),
    )

    @field_validator("evidence_strength")
    @classmethod
    def _known_strength(cls, value: str) -> str:
        cleaned = value.strip().lower()
        if cleaned not in EVIDENCE_STRENGTH:
            raise ValueError(f"must be one of {EVIDENCE_STRENGTH}")
        return cleaned

    @field_validator("end_sec")
    @classmethod
    def _ordered(cls, end: float, info) -> float:
        start = info.data.get("start_sec")
        if start is not None and end <= start:
            raise ValueError("end_sec must be after start_sec")
        return end


class ExtractionResult(BaseModel):
    moments: list[ExtractedMoment] = Field(
        description=(
            "The video's substance, in order, with no gaps. A moment is one "
            "idea with its support; its length follows the content rather than "
            "any target. Every part of the video that says something must fall "
            "inside some moment."
        )
    )
