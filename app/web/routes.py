"""Server-rendered pages. The surface a person actually uses."""

import secrets
from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from openai import APIConnectionError, APITimeoutError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import COOKIE_NAME
from app.config import get_settings
from app.db.models import IngestJob, JobStatus, Moment, Video
from app.db.session import get_session
from app.extract.schema import SCHEMA_VERSION
from app.ingest.parse import UnparseableShare
from app.ingest.service import submit_share
from app.providers import openai_provider
from app.providers.base import MissingCredential, resolve_task
from app.providers.openai_provider import ProviderAuthError
from app.search.hybrid import hybrid_search

router = APIRouter(include_in_schema=False)
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def _mmss(seconds: float) -> str:
    total = int(seconds)
    return f"{total // 60}:{total % 60:02d}"


templates.env.filters["mmss"] = _mmss


async def _failed_total(session: AsyncSession) -> int:
    return (
        await session.scalar(
            select(func.count()).select_from(IngestJob).where(IngestJob.status == JobStatus.FAILED)
        )
        or 0
    )


@router.get("/login", response_class=HTMLResponse)
async def login_form(request: Request):
    return templates.TemplateResponse(request, "login.html", {"error": None})


@router.post("/login")
async def login(request: Request, token: str = Form(...)):
    """Exchange the token for a cookie.

    Constant-time comparison here too — this endpoint is public by necessity,
    so it is the one an attacker would actually hammer.
    """
    settings = get_settings()
    if not secrets.compare_digest(token.encode(), settings.api_token.encode()):
        return templates.TemplateResponse(
            request, "login.html", {"error": "令牌不正确"}, status_code=401
        )

    # A cookie marked Secure is discarded by the browser over plain HTTP without
    # a word, so a correct token would bounce between here and the sign-in page
    # forever with nothing on screen to explain why. Refusing loudly is worth
    # more than a loop; the cookie stays Secure, because the alternative is
    # shipping the credential in the clear.
    #
    # The forwarded header is read here rather than left to uvicorn's
    # --proxy-headers, because that flag is a property of how the server was
    # launched, not of this code: nothing in the module would show that the check
    # silently inverts under a different entrypoint, and the failure is a lockout.
    #
    # Trusting a header the caller can forge is safe for this one decision. All a
    # forged "https" buys is skipping this warning and receiving a Secure cookie
    # the forger's own browser then discards — no access is granted, and the token
    # in it is the one they just typed. It is never used to authorise anything.
    forwarded = request.headers.get("x-forwarded-proto", "").split(",")[0].strip()
    if settings.is_production and (forwarded or request.url.scheme) != "https":
        return templates.TemplateResponse(
            request,
            "login.html",
            {
                "error": "生产模式的登录 cookie 带 Secure 标记，浏览器只在 HTTPS 下"
                "保存它 —— 现在是 HTTP，登录不会生效。请为服务配置 HTTPS；"
                "本地调试请把 ENVIRONMENT 设为 development。"
            },
            status_code=400,
        )

    response = RedirectResponse("/", status_code=303)
    response.set_cookie(
        COOKIE_NAME,
        token,
        httponly=True,  # unreadable from JavaScript
        samesite="lax",  # not sent on cross-site form posts
        secure=settings.is_production,  # HTTPS-only once deployed
        max_age=60 * 60 * 24 * 30,
    )
    return response


@router.get("/", response_class=HTMLResponse)
async def home(request: Request, q: str = "", session: AsyncSession = Depends(get_session)):
    context: dict = {"q": q, "hits": [], "failed_total": await _failed_total(session)}

    if not q.strip():
        context["video_count"] = await session.scalar(select(func.count()).select_from(Video)) or 0
        context["moment_count"] = (
            await session.scalar(
                select(func.count())
                .select_from(Moment)
                .where(Moment.schema_version == SCHEMA_VERSION)
            )
            or 0
        )
        return templates.TemplateResponse(request, "search.html", context)

    try:
        config = resolve_task("embed")
        embedded = await openai_provider.embed_texts(config, [q])
        context["hits"] = await hybrid_search(
            session,
            query=q,
            query_embedding=embedded.vectors[0],
            schema_version=SCHEMA_VERSION,
            limit=20,
        )
    # Every one of these fails the search without the query being wrong, and all
    # of them would otherwise render as "nothing matched" or a blank 500 — both
    # of which blame the reader for an outage. They are reported separately from
    # the results, not folded into `q`, because `q` is echoed back into the
    # search box and an error message does not belong in the reader's input.
    except MissingCredential:
        context["error"] = "还没有配置 embedding 的 API key，搜索用不了。"
    except ProviderAuthError as exc:
        context["error"] = f"embedding 服务拒绝了这次请求（额度、限流或 key 失效）：{exc}"
    except (APITimeoutError, APIConnectionError):
        context["error"] = "连不上 embedding 服务，可能是网络问题。稍后再试。"

    return templates.TemplateResponse(request, "search.html", context)


@router.get("/add", response_class=HTMLResponse)
async def add_form(
    request: Request, queued: int | None = None, session: AsyncSession = Depends(get_session)
):
    return templates.TemplateResponse(
        request, "add.html", {"queued": queued, "failed_total": await _failed_total(session)}
    )


@router.post("/add")
async def add_submit(
    request: Request, text: str = Form(...), session: AsyncSession = Depends(get_session)
):
    """The paste box. Same work as POST /ingest, reached without a terminal.

    Redirects on success rather than rendering: a refresh after a rendered POST
    resubmits it, and here that means the same link queued twice.
    """
    try:
        job = await submit_share(session, text)
    except UnparseableShare as exc:
        return templates.TemplateResponse(
            request,
            "add.html",
            {
                "error": f"没找到抖音链接：{exc}",
                "failed_total": await _failed_total(session),
            },
            status_code=400,
        )
    return RedirectResponse(f"/add?queued={job.id}", status_code=303)


async def _library_rows(session: AsyncSession):
    """Videos with what is actually searchable, and what is only taking space.

    `live` counts the current schema; anything older was produced by a prompt or
    model this build no longer uses and is invisible to search, so a video can
    look full and return nothing.
    """
    live = func.count(Moment.id).filter(Moment.schema_version == SCHEMA_VERSION)
    dead = func.count(Moment.id).filter(Moment.schema_version < SCHEMA_VERSION)
    return (
        await session.execute(
            select(Video, live.label("live"), dead.label("dead"))
            .join(Moment, Moment.video_id == Video.id, isouter=True)
            .group_by(Video.id)
            .order_by(Video.created_at.desc())
        )
    ).all()


@router.get("/library", response_class=HTMLResponse)
async def library(request: Request, session: AsyncSession = Depends(get_session)):
    return templates.TemplateResponse(
        request,
        "library.html",
        {"rows": await _library_rows(session), "failed_total": await _failed_total(session)},
    )


@router.get("/library/{video_id}/delete", response_class=HTMLResponse)
async def delete_confirm(
    request: Request, video_id: int, session: AsyncSession = Depends(get_session)
):
    """Show what is about to be destroyed before destroying it.

    A confirmation page rather than a JavaScript `confirm()`: the guard on an
    irreversible action should not be something a blocked script can skip, and
    the counts are the part worth reading — "this also removes 41 moments and
    the transcript" is not obvious from a button labelled 删除.
    """
    video = await session.get(Video, video_id)
    if video is None:
        return RedirectResponse("/library", status_code=303)

    moments = await session.scalar(
        select(func.count()).select_from(Moment).where(Moment.video_id == video_id)
    )
    return templates.TemplateResponse(
        request,
        "delete.html",
        {
            "video": video,
            "moments": moments or 0,
            "failed_total": await _failed_total(session),
        },
    )


@router.post("/library/{video_id}/delete")
async def delete_video(video_id: int, session: AsyncSession = Depends(get_session)):
    """Delete the video and everything derived from it.

    Moments, transcript and call records go with it by ON DELETE CASCADE, set on
    the foreign keys rather than performed here — the database enforces it for
    every caller, including psql, which application-side deletion would not.
    Ingest jobs are ON DELETE SET NULL instead: the job is a record that this
    link was submitted, and that stays true after the video is gone.
    """
    video = await session.get(Video, video_id)
    if video is not None:
        await session.delete(video)
        await session.commit()
    return RedirectResponse("/library", status_code=303)


@router.get("/admin/jobs", response_class=HTMLResponse)
async def jobs(request: Request, session: AsyncSession = Depends(get_session)):
    # Joined to the video so a row can name what it produced. A bare video id is
    # not an answer to the only question this page is ever asked — "what
    # happened to the thing I just sent?" — and it is the answer to none of them
    # for `duplicate`, where the useful information is *which* video it repeats.
    # Douyin mints a fresh short link on every share, so the same video arriving
    # under a URL never seen before is the normal case, not a strange one.
    rows = (
        await session.execute(
            select(IngestJob, Video.caption, Video.author_name)
            .join(Video, Video.id == IngestJob.video_id, isouter=True)
            .order_by(IngestJob.created_at.desc())
            .limit(50)
        )
    ).all()
    return templates.TemplateResponse(
        request,
        "jobs.html",
        {"jobs": rows, "failed_total": await _failed_total(session)},
    )
