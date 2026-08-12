"""Hybrid retrieval over moments.

Vector search alone answers a query for "pgvector" with everything about
databases, retrieval and embeddings, and misses the one moment that names the
thing. Full-text alone misses a moment that explains the idea in other words.
Each covers the other's blind spot, so both run and their results are fused.
"""

from dataclasses import dataclass

from sqlalchemy import text as sql
from sqlalchemy.ext.asyncio import AsyncSession

from app.search.text import segment

# Reciprocal Rank Fusion. Ranks are combined, not scores: a cosine distance and
# a ts_rank are not on comparable scales, and normalising them introduces
# constants that then need tuning per corpus. Ranks need none of that.
#
# k dampens the top of each list so a single first place cannot dominate a
# result that both retrievers liked. 60 is the value from the original paper and
# is not sensitive.
RRF_K = 60

# How deep each retriever goes before fusion. Wider than the result count so a
# moment ranked poorly by one side still has a chance through the other.
CANDIDATES = 50

# Cosine distance beyond which a vector match is not a match at all.
#
# RRF ranks; it cannot gate. Because it uses positions only, the top result
# always scores 1/(k+1) whether it is a perfect match or the least-bad of a bad
# set — and a vector index always returns its nearest neighbours however far
# away they are. Without a floor, "红烧肉怎么做" returned every moment in the
# store at exactly the score a correct answer gets, and "no results" could never
# happen.
#
# Measured on text-embedding-3-small over this corpus: relevant queries landed
# at 0.478–0.594, unrelated ones at 0.767–0.795. 0.70 sits in the gap. Six
# samples, so treat it as provisional — and re-measure if the embedding model
# changes, since the distance distribution is a property of the model.
MAX_VECTOR_DISTANCE = 0.70

_SEARCH_SQL = sql(
    """
WITH full_text AS (
    SELECT id,
           ROW_NUMBER() OVER (
               ORDER BY ts_rank(to_tsvector('simple', search_text),
                                plainto_tsquery('simple', :segmented)) DESC
           ) AS rank
    FROM moment
    WHERE schema_version = :schema_version
      AND search_text IS NOT NULL
      AND to_tsvector('simple', search_text) @@ plainto_tsquery('simple', :segmented)
    LIMIT :candidates
),
vector AS (
    SELECT id,
           ROW_NUMBER() OVER (ORDER BY embedding <=> CAST(:embedding AS vector)) AS rank
    FROM moment
    WHERE schema_version = :schema_version
      AND embedding IS NOT NULL
      -- The gate. Applied here rather than after fusion so that a query with
      -- nothing near it produces an empty candidate set, and therefore no
      -- results, instead of the nearest available irrelevance.
      AND embedding <=> CAST(:embedding AS vector) <= :max_distance
    ORDER BY embedding <=> CAST(:embedding AS vector)
    LIMIT :candidates
),
fused AS (
    SELECT COALESCE(f.id, v.id) AS id,
           COALESCE(1.0 / (:k + f.rank), 0) AS ft_score,
           COALESCE(1.0 / (:k + v.rank), 0) AS vec_score,
           f.rank AS ft_rank,
           v.rank AS vec_rank
    FROM full_text f
    FULL OUTER JOIN vector v ON f.id = v.id
)
SELECT m.id, m.video_id, m.start_sec, m.end_sec, m.title, m.summary,
       m.keywords, m.evidence_strength, m.relevance,
       v.author_name, v.caption, v.source_url, v.duration_sec AS video_duration,
       fused.ft_score + fused.vec_score AS score,
       fused.ft_rank, fused.vec_rank
FROM fused
JOIN moment m ON m.id = fused.id
JOIN video v ON v.id = m.video_id
ORDER BY score DESC, m.relevance DESC NULLS LAST
LIMIT :limit
"""
)


@dataclass
class SearchHit:
    moment_id: int
    video_id: int
    start_sec: float
    end_sec: float
    title: str
    summary: str
    keywords: list[str]
    evidence_strength: str | None
    relevance: float | None
    author_name: str | None
    source_url: str
    # Lets a result show where in the video it sits. "Is this the intro or
    # the conclusion" is something the reader wants before tapping, and the
    # timestamp alone does not answer it without knowing the total length.
    video_duration: float | None
    score: float
    # Which retriever found it, and where. Kept because "the vector side alone
    # returned this" is the difference between a good hybrid and a vector search
    # with extra steps — and it is not visible from the final ordering.
    full_text_rank: int | None
    vector_rank: int | None

    @property
    def deep_link(self) -> str:
        """Back to the exact second. The product's whole promise."""
        return f"{self.source_url}?t={int(self.start_sec)}"


async def hybrid_search(
    session: AsyncSession,
    query: str,
    query_embedding: list[float],
    schema_version: int,
    limit: int = 20,
    max_distance: float = MAX_VECTOR_DISTANCE,
) -> list[SearchHit]:
    """Both retrievers, fused by rank.

    The query is segmented with the same function used at index time. Doing it
    on one side only fails silently — the tokens simply never match, and the
    full-text half quietly contributes nothing.
    """
    rows = await session.execute(
        _SEARCH_SQL,
        {
            "segmented": segment(query),
            "embedding": "[" + ",".join(f"{v:.7f}" for v in query_embedding) + "]",
            "schema_version": schema_version,
            "candidates": CANDIDATES,
            "max_distance": max_distance,
            "k": RRF_K,
            "limit": limit,
        },
    )

    return [
        SearchHit(
            moment_id=r.id,
            video_id=r.video_id,
            start_sec=float(r.start_sec),
            end_sec=float(r.end_sec),
            title=r.title,
            summary=r.summary,
            keywords=r.keywords or [],
            evidence_strength=r.evidence_strength,
            relevance=float(r.relevance) if r.relevance is not None else None,
            author_name=r.author_name,
            source_url=r.source_url,
            video_duration=float(r.video_duration) if r.video_duration else None,
            score=float(r.score),
            full_text_rank=r.ft_rank,
            vector_rank=r.vec_rank,
        )
        for r in rows
    ]
