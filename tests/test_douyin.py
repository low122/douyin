import json
from pathlib import Path

import pytest

from app.ingest.douyin import (
    ShapeChanged,
    TemporarilyBlocked,
    VideoUnavailable,
    parse_share_page,
)

FIXTURE = Path(__file__).parent / "fixtures" / "share_page_7670209615078206948.html"
AWEME_ID = "7670209615078206948"


def page_with(items: list[dict]) -> str:
    """Wrap items in the page shape. Built with json.dumps rather than written
    by hand — hand-escaping four levels of nesting is its own source of bugs."""
    payload = {"loaderData": {"video_(id)/page": {"videoInfoRes": {"item_list": items}}}}
    return f"<script>window._ROUTER_DATA = {json.dumps(payload)}</script>"


def test_parses_the_real_page_shape():
    """Pinned to a real share page, reduced to the fields the pipeline reads.

    ADR-0006 accepts a hand-written parser against an undocumented shape; this
    fixture is what makes that acceptable. If Douyin moves the JSON, this fails
    here rather than silently in production.
    """
    meta = parse_share_page(FIXTURE.read_text(encoding="utf-8"), AWEME_ID)

    assert meta.aweme_id == AWEME_ID
    assert meta.source_url == f"https://www.douyin.com/video/{AWEME_ID}"
    assert meta.author_name == "AI大模型日记"
    assert meta.duration_sec == 312  # 312333 ms, rounded
    assert "幻觉" in meta.caption
    assert meta.media_url.startswith("https://")


def test_no_captions_or_chapters_are_provided():
    """Both fields exist and are null on every video measured. This test exists
    so that if Douyin ever starts populating them, we find out — free captions
    would remove most of the need to read the frames at all."""
    meta = parse_share_page(FIXTURE.read_text(encoding="utf-8"), AWEME_ID)
    assert meta.raw.get("video_text") is None
    assert meta.raw.get("chapter_list") is None


def test_missing_router_data_is_a_shape_error():
    """A page without the payload means the parser needs updating, which is a
    different problem from a network failure and must not be retried."""
    with pytest.raises(ShapeChanged):
        parse_share_page("<html><body>nothing here</body></html>", AWEME_ID)


def test_unexpected_json_shape_is_a_shape_error():
    html = '<script>window._ROUTER_DATA = {"loaderData":{"other":{}}}</script>'
    with pytest.raises(ShapeChanged):
        parse_share_page(html, AWEME_ID)


def test_withheld_payload_is_throttling_not_a_shape_change():
    """Observed under anti-bot throttling: the page shell arrives intact and
    only the video data is missing, alongside serverToken/abParams and mentions
    of captcha.

    These two failures look nearly identical and must not be conflated — one is
    permanent and one clears on its own, so treating throttling as a schema
    change writes off a video that would have worked a minute later.
    """
    payload = {
        "loaderData": {
            "video_(id)/page": {
                "itemId": AWEME_ID,
                "serverToken": "...",
                "abParams": {},
                "isSpider": False,
            }
        },
        "errors": None,
    }
    html = f"<script>window._ROUTER_DATA = {json.dumps(payload)}</script>"
    with pytest.raises(TemporarilyBlocked):
        parse_share_page(html, AWEME_ID)


def test_malformed_videoinfores_is_still_a_shape_error():
    """Present but wrong is a parser problem; absent is throttling."""
    payload = {"loaderData": {"video_(id)/page": {"videoInfoRes": {"unexpected": 1}}}}
    html = f"<script>window._ROUTER_DATA = {json.dumps(payload)}</script>"
    with pytest.raises(ShapeChanged):
        parse_share_page(html, AWEME_ID)


def test_empty_item_list_means_unavailable():
    """The page renders but carries no video: deleted, private or blocked.
    Permanent, so the job should fail rather than retry three times."""
    with pytest.raises(VideoUnavailable):
        parse_share_page(page_with([]), AWEME_ID)


def test_absent_optional_fields_do_not_crash():
    """Every field except the id is optional in practice."""
    meta = parse_share_page(page_with([{"aweme_id": AWEME_ID}]), AWEME_ID)
    assert meta.aweme_id == AWEME_ID
    assert meta.caption is None
    assert meta.duration_sec is None
    assert meta.media_url is None
    assert meta.published_at is None
