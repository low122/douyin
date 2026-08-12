from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

# text-embedding-3-small. The column width is fixed at migration time, so
# switching to a model with a different width needs a migration, not just an
# env change — the one place ADR-0004's "model choice is configuration" has a
# real edge.
EMBEDDING_DIM = 1536


class JobStatus:
    """Stages a job moves through. Stored as a plain string rather than a
    Postgres enum — altering an enum type in a migration is far more painful
    than adding a value to a check-free varchar column."""

    QUEUED = "queued"
    FETCHING = "fetching"
    TRANSCRIBING = "transcribing"
    EXTRACTING = "extracting"
    EMBEDDING = "embedding"
    DONE = "done"
    DUPLICATE = "duplicate"
    FAILED = "failed"


class FailureKind:
    """Why a job stopped, which decides whether retrying is worth anything.
    See docs/design-decisions.md — retrying a deleted video three times just
    burns time, and not retrying a network blip is needlessly fragile."""

    PERMANENT = "permanent"  # deleted, private, unparseable — do not retry
    TRANSIENT = "transient"  # timeout, 5xx, rate limit — retry with backoff
    OPERATOR = "operator"  # bad key, quota exhausted — stop and shout


class Video(Base):
    """A Douyin video that has been ingested.

    Holds derived text and metadata, never the media file itself (ADR-0003).
    """

    __tablename__ = "video"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    platform: Mapped[str] = mapped_column(
        String(16), server_default=text("'douyin'"), default="douyin", nullable=False
    )

    # The idempotency key. Uniqueness is enforced here, in the database, rather
    # than by checking before insert: two workers can both find nothing and both
    # insert. The second one gets an IntegrityError, which is the intended path.
    aweme_id: Mapped[str] = mapped_column(String(32), nullable=False)

    # Canonical and deliberately rebuilt from aweme_id. The URL the short link
    # actually resolves to carries did/iid/u_code — a device fingerprint that has
    # no business being stored (docs/douyin-platform-notes.md).
    source_url: Mapped[str] = mapped_column(Text, nullable=False)

    author_name: Mapped[str | None] = mapped_column(String(128))
    caption: Mapped[str | None] = mapped_column(Text)
    duration_sec: Mapped[int | None] = mapped_column(Integer)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # The full item from the share page. Kept so extraction can be re-run
    # against a better prompt without re-fetching, which is the same reason
    # transcripts live apart from extractions.
    raw_meta: Mapped[dict | None] = mapped_column(JSONB)

    # Indexed for the review view, which lists recent videos newest-first.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )

    jobs: Mapped[list["IngestJob"]] = relationship(back_populates="video")

    __table_args__ = (UniqueConstraint("platform", "aweme_id", name="uq_video_platform_aweme"),)


class IngestJob(Base):
    """One attempt to bring a shared link into the system.

    Created before the video exists: at enqueue time all we have is the blob the
    share sheet produced. Resolving it to an aweme_id needs a network call, which
    is why that happens in the worker and not on the request path.
    """

    __tablename__ = "ingest_job"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    # Nullable on purpose — populated once the worker has resolved the link.
    # Indexed because Postgres does not index foreign keys for you.
    video_id: Mapped[int | None] = mapped_column(
        ForeignKey("video.id", ondelete="SET NULL"), index=True
    )

    # The share text exactly as received. Stored so a parser improvement can be
    # replayed over old input instead of asking the user to re-share everything.
    raw_input: Mapped[str] = mapped_column(Text, nullable=False)
    short_url: Mapped[str | None] = mapped_column(Text)

    # Indexed: "how many failed" and "what is still queued" are the two queries
    # the admin view and the shortcut's follow-up check both run.
    status: Mapped[str] = mapped_column(
        String(24),
        server_default=text(f"'{JobStatus.QUEUED}'"),
        default=JobStatus.QUEUED,
        nullable=False,
        index=True,
    )
    failure_kind: Mapped[str | None] = mapped_column(String(16))
    last_error: Mapped[str | None] = mapped_column(Text)
    attempt: Mapped[int] = mapped_column(
        Integer, server_default=text("0"), default=0, nullable=False
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    video: Mapped["Video | None"] = relationship(back_populates="jobs")


class Transcript(Base):
    """What was said, and when.

    Its job is timing and rough content, not finished prose. The model that
    returns timestamps is not the one that transcribes Chinese best, so
    homophone errors are expected here and get resolved during extraction,
    which can also see the frames and the caption (ADR-0007).
    """

    __tablename__ = "transcript"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    # One transcript per video. Re-transcribing replaces it rather than
    # accumulating rows; the extractions built on it are versioned instead.
    video_id: Mapped[int] = mapped_column(
        ForeignKey("video.id", ondelete="CASCADE"), nullable=False, unique=True
    )

    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str] = mapped_column(String(64), nullable=False)
    language: Mapped[str | None] = mapped_column(String(16))
    duration_sec: Mapped[float | None] = mapped_column(Numeric(10, 3))

    full_text: Mapped[str] = mapped_column(Text, nullable=False)

    # [{"start": 0.0, "end": 1.76, "text": "..."}] — the boundaries a moment
    # will be cut from.
    segments: Mapped[list | None] = mapped_column(JSONB)

    # Word-level timings, kept for the same reason transcripts are kept at all:
    # re-deriving them costs another paid transcription, while storing them
    # costs a column. Roughly 1700 entries for a five-minute video.
    words: Mapped[list | None] = mapped_column(JSONB)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Moment(Base):
    """A time-bounded span carrying one self-contained piece of information.

    The unit a search returns (ADR-0002). Its boundaries come from real
    transcript segments rather than from the model's own arithmetic — a summary
    pointing at a timestamp where nothing was said is worse than no timestamp.
    """

    __tablename__ = "moment"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    video_id: Mapped[int] = mapped_column(
        ForeignKey("video.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # Extraction is re-runnable against a better prompt without re-fetching or
    # re-transcribing, which is why transcripts and moments are separate tables.
    # The version says which prompt shape produced this row.
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str] = mapped_column(String(64), nullable=False)

    start_sec: Mapped[float] = mapped_column(Numeric(10, 3), nullable=False)
    end_sec: Mapped[float] = mapped_column(Numeric(10, 3), nullable=False)

    title: Mapped[str] = mapped_column(String(200), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)

    # Proper nouns worth matching exactly. Vector search alone answers a query
    # for "pgvector" with "databases, retrieval, embeddings"; these carry the
    # full-text half of hybrid retrieval.
    keywords: Mapped[list | None] = mapped_column(JSONB)

    # "strong" | "anecdotal" | "unsupported". Business and mindset content is
    # mostly assertion, and knowing which is which is what keeps the store from
    # becoming a pile of confident claims.
    evidence_strength: Mapped[str | None] = mapped_column(String(16))

    # Scored, never used to discard (ADR-0001). Filtering happens at query time,
    # where a mistake is visible and reversible.
    relevance: Mapped[float | None] = mapped_column(Numeric(4, 3))

    # Retrieval lives on the moment rather than in a separate chunk table. An
    # earlier design had one, from when it was still open what a search should
    # return; once that settled on the moment (ADR-0002) the extra table was
    # only a join and a way for the two to fall out of step.
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM))

    # jieba output, space-joined. Postgres cannot segment Chinese, and adding an
    # extension that can means compiling it into the image — a deployment
    # failure mode for every self-hoster. Segmenting in Python keeps the stock
    # pgvector image and puts the logic somewhere it can be tested.
    search_text: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        # One live set of moments per video. `schema_version` and `model` are
        # recorded on the row but are not part of its identity: they say what
        # produced this moment, not which of several parallel copies it is.
        #
        # They used to be in the key, so that two models could be extracted side
        # by side and compared — which is how gpt-4o was chosen, and the numbers
        # are in ADR-0008. That was only half-built: search filtered on the
        # version and not the model, so the sets the key carefully kept apart
        # came back interleaved under near-identical titles. Re-extraction now
        # replaces rather than accumulates, which removes the duplicate results
        # and the store that grew on every re-run with nothing to retire it.
        UniqueConstraint("video_id", "start_sec", name="uq_moment_video_start"),
    )


class LlmCall(Base):
    """One model invocation, with what it cost.

    A provider's billing page gives a monthly total; it cannot answer which task
    is expensive or what one video costs — and that number is the only evidence
    that routing tasks to different models was worth doing.
    """

    __tablename__ = "llm_call"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    video_id: Mapped[int | None] = mapped_column(
        ForeignKey("video.id", ondelete="CASCADE"), index=True
    )

    # Indexed for the query this table exists to answer: cost and latency grouped
    # by task, which is what shows whether per-task model routing paid off.
    task: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str] = mapped_column(String(64), nullable=False)

    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    audio_seconds: Mapped[int | None] = mapped_column(Integer)  # transcription bills by duration

    # Null rather than zero when the model is not in the local price table.
    # An absent number is recoverable; a fabricated one quietly corrupts every
    # cost figure computed from this table.
    cost_usd: Mapped[float | None] = mapped_column(Numeric(10, 6))

    latency_ms: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
