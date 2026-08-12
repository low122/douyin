from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.extract.schema import SCHEMA_VERSION
from app.providers import openai_provider
from app.providers.base import MissingCredential, resolve_task
from app.search.hybrid import hybrid_search

router = APIRouter(tags=["search"])


class Hit(BaseModel):
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
    deep_link: str
    score: float
    # Which retriever surfaced this, and at what rank. Exposed rather than
    # hidden: without it there is no way to tell a working hybrid from a vector
    # search with an inert full-text branch bolted on.
    full_text_rank: int | None
    vector_rank: int | None


class SearchResponse(BaseModel):
    query: str
    count: int
    hits: list[Hit]


@router.get("/search", response_model=SearchResponse)
async def search(
    q: str = Query(min_length=1, max_length=500),
    limit: int = Query(default=20, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
):
    """Return the moments that answer a question, newest ranking first.

    Results are moments, not videos: the point is landing on the seconds that
    answer the question rather than on a twelve-minute clip to scrub through
    (ADR-0002). Each hit carries a deep link to its own start time.
    """
    try:
        config = resolve_task("embed")
    except MissingCredential as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc

    embedded = await openai_provider.embed_texts(config, [q])
    if not embedded.vectors:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "could not embed the query")

    hits = await hybrid_search(
        session,
        query=q,
        query_embedding=embedded.vectors[0],
        schema_version=SCHEMA_VERSION,
        limit=limit,
    )

    return SearchResponse(
        query=q,
        count=len(hits),
        hits=[
            Hit(
                moment_id=h.moment_id,
                video_id=h.video_id,
                start_sec=h.start_sec,
                end_sec=h.end_sec,
                title=h.title,
                summary=h.summary,
                keywords=h.keywords,
                evidence_strength=h.evidence_strength,
                relevance=h.relevance,
                author_name=h.author_name,
                deep_link=h.deep_link,
                score=h.score,
                full_text_rank=h.full_text_rank,
                vector_rank=h.vector_rank,
            )
            for h in hits
        ],
    )
