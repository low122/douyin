from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.ingest import router as ingest_router
from app.api.search import router as search_router
from app.web.routes import router as web_router
from app.auth import BearerTokenMiddleware
from app.config import get_settings
from app.db.session import dispose_engine, get_session, init_engine
from app.logging_config import configure_logging
from app.search.text import warm_up

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    # Build jieba's dictionary now; the first call takes about a second and
    # has no business landing inside a user's search.
    warm_up()
    init_engine(settings.database_url)
    yield
    await dispose_engine()


app = FastAPI(
    title="Douyin Knowledge Base",
    lifespan=lifespan,
    # In production the interactive docs would hand a scanner the full API
    # reference — every path, every parameter (ADR-0005).
    docs_url=None if settings.is_production else "/docs",
    openapi_url=None if settings.is_production else "/openapi.json",
    redoc_url=None,
)

app.add_middleware(BearerTokenMiddleware, token=settings.api_token)
app.include_router(ingest_router)
app.include_router(search_router)
app.include_router(web_router)


@app.get("/health")
async def health() -> dict[str, str]:
    """Liveness: is the process up? Public, because a container health check
    has to reach it before anything is configured. Deliberately says nothing
    about internal state."""
    return {"status": "ok"}


@app.get("/health/ready")
async def readiness(session: AsyncSession = Depends(get_session)) -> dict[str, str]:
    """Readiness: can the process actually serve traffic? Authenticated,
    because whether the database is reachable is not a stranger's business."""
    await session.execute(text("SELECT 1"))
    return {"status": "ready", "database": "ok"}
