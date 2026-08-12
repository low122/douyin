"""Reading a Douyin video's metadata, without cookies and without yt-dlp.

yt-dlp ships a Douyin extractor but it demands fresh session cookies that only a
real browser can obtain, which rules it out for unattended use (ADR-0006). The
share page has no such requirement: it embeds everything as JSON.
"""

import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime

import httpx

from app.ingest.parse import canonical_url

# A mobile user agent is what the share page expects; a desktop one is served a
# different, less useful shell.
MOBILE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
)

SHARE_PAGE = "https://www.iesdouyin.com/share/video/{aweme_id}/"

# The page assigns its payload to this variable inside a <script> tag.
ROUTER_DATA = re.compile(r"_ROUTER_DATA\s*=\s*(\{.*?\})\s*</script>", re.S)

# aweme_id sits in the path of whatever the short link redirects to.
RESOLVED_ID = re.compile(r"/share/video/(\d+)")


class VideoUnavailable(Exception):
    """The video is gone, private, or region-blocked. Permanent — do not retry."""


class ShapeChanged(Exception):
    """The share page parsed, but not into the structure we expect.

    Distinct from a network error on purpose: this means the page's JSON moved
    and the parser needs updating, which no retry will fix. It is the accepted
    cost of not depending on a maintained extractor (ADR-0006).
    """


class TemporarilyBlocked(Exception):
    """Douyin served the page shell without the video data.

    Anti-bot throttling, triggered by requesting too much too fast. It looks
    almost exactly like ShapeChanged and must not be confused with it: one is
    permanent and one clears on its own, so conflating them either retries a
    dead parser forever or writes off a video that would have worked in a
    minute.

    The tell is that the page structure is intact — `_ROUTER_DATA`, `loaderData`
    and the page object are all present — and only `videoInfoRes` is absent.
    """


@dataclass
class VideoMeta:
    aweme_id: str
    source_url: str
    caption: str | None = None
    author_name: str | None = None
    duration_sec: int | None = None
    published_at: datetime | None = None
    media_url: str | None = None
    raw: dict = field(default_factory=dict)


async def resolve_short_url(client: httpx.AsyncClient, short_url: str) -> str:
    """Follow a v.douyin.com link and return the aweme_id.

    Only the id is taken from the destination. The rest of its query string is
    the sharer's device fingerprint (docs/douyin-platform-notes.md).
    """
    try:
        response = await client.get(
            short_url, headers={"User-Agent": MOBILE_UA}, follow_redirects=True
        )
    except httpx.HTTPError as exc:  # network-level: worth retrying
        raise ConnectionError(f"could not resolve {short_url}: {exc}") from exc

    # Check the final URL and every hop, since the id may appear before the last.
    candidates = [str(response.url)] + [str(r.url) for r in response.history]
    for url in candidates:
        if m := RESOLVED_ID.search(url):
            return m.group(1)

    raise VideoUnavailable(f"{short_url} did not resolve to a video (status {response.status_code})")


async def fetch_metadata(client: httpx.AsyncClient, aweme_id: str) -> VideoMeta:
    """Read the share page and pull out the fields the pipeline needs."""
    url = SHARE_PAGE.format(aweme_id=aweme_id)
    try:
        response = await client.get(url, headers={"User-Agent": MOBILE_UA})
    except httpx.HTTPError as exc:
        raise ConnectionError(f"could not fetch share page for {aweme_id}: {exc}") from exc

    if response.status_code >= 500:
        raise ConnectionError(f"share page returned {response.status_code} for {aweme_id}")
    if response.status_code != 200:
        raise VideoUnavailable(f"share page returned {response.status_code} for {aweme_id}")

    return parse_share_page(response.text, aweme_id)


def parse_share_page(html: str, aweme_id: str) -> VideoMeta:
    """Pure parse, so it can be tested against a saved fixture."""
    m = ROUTER_DATA.search(html)
    if not m:
        raise ShapeChanged(f"no _ROUTER_DATA in share page for {aweme_id}")

    try:
        payload = json.loads(m.group(1))
        page = payload["loaderData"]["video_(id)/page"]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        # The shell itself is wrong: the page moved and the parser is stale.
        raise ShapeChanged(f"share page JSON is not the expected shape: {exc}") from exc

    if "videoInfoRes" not in page:
        # Structure intact, payload withheld. Throttling, not a schema change —
        # the response also gains serverToken/abParams and mentions captcha.
        raise TemporarilyBlocked(
            f"share page for {aweme_id} carried no video data; "
            "this is throttling rather than a parser problem"
        )

    try:
        items = page["videoInfoRes"]["item_list"]
    except (KeyError, TypeError) as exc:
        raise ShapeChanged(f"videoInfoRes is not the expected shape: {exc}") from exc

    if not items:
        # The page rendered but carries no video: deleted, private, or blocked.
        raise VideoUnavailable(f"share page has no item for {aweme_id}")

    item = items[0]
    video = item.get("video") or {}

    duration_ms = video.get("duration")
    urls = (video.get("play_addr") or {}).get("url_list") or []

    created = item.get("create_time")
    published_at = None
    if isinstance(created, int) and created > 0:
        published_at = datetime.fromtimestamp(created, tz=UTC)

    return VideoMeta(
        aweme_id=str(item.get("aweme_id") or aweme_id),
        source_url=canonical_url(aweme_id),
        caption=item.get("desc") or None,
        author_name=(item.get("author") or {}).get("nickname"),
        duration_sec=round(duration_ms / 1000) if isinstance(duration_ms, int | float) else None,
        published_at=published_at,
        media_url=urls[0] if urls else None,
        raw=item,
    )
