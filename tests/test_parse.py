import pytest

from app.ingest.parse import (
    UnparseableShare,
    canonical_url,
    parse_share_input,
)

# Verbatim from the Douyin iOS share sheet, 2026-08-10. Captions truncated;
# the obfuscation tokens are untouched because their varying order is the whole
# reason this parser ignores position. See docs/douyin-platform-notes.md.
REAL_SHARES = [
    (
        "2.51 03/15 :3pm a@A.go jpQ:/ 具身智能5-10年都起不来—卡的不是技术，是人性。 "
        "#水球泡 #具身智能  https://v.douyin.com/8IC3BBBzMAY/ 复制此链接，打开Dou音搜索，直接观看视频！",
        "https://v.douyin.com/8IC3BBBzMAY/",
    ),
    (
        "9.41 h@b.aN 09/30 :0pm YZM:/ 生产级PDF文件识别RAG要过4道关，90%的人倒在第一关 "
        "#Ai大模型 #Agent  https://v.douyin.com/OXnlTiRkdXw/ 复制此链接，打开Dou音搜索，直接观看视频！",
        "https://v.douyin.com/OXnlTiRkdXw/",
    ),
    (
        "7.61 YzT:/ :5pm 11/17 G@I.IV 大模型为什么会产生幻觉？ 6层归因 "
        "#Ai #Agent  https://v.douyin.com/aBf60JvMaMU/ 复制此链接，打开Dou音搜索，直接观看视频！",
        "https://v.douyin.com/aBf60JvMaMU/",
    ),
]


@pytest.mark.parametrize(("raw", "expected"), REAL_SHARES)
def test_real_share_text(raw, expected):
    """All three samples put the noise tokens in a different order. Anything
    that keys off position passes on one and fails on the next."""
    parsed = parse_share_input(raw)
    assert parsed.short_url == expected
    assert parsed.aweme_id is None  # not known until the link is resolved


def test_canonical_url_is_recognised_without_a_round_trip():
    parsed = parse_share_input("https://www.douyin.com/video/7670209615078206948")
    assert parsed.aweme_id == "7670209615078206948"
    assert parsed.short_url is None  # nothing to resolve, so no network call


def test_resolved_share_page_url():
    parsed = parse_share_input("https://www.iesdouyin.com/share/video/7640824143700577586/?region=MY")
    assert parsed.aweme_id == "7640824143700577586"


def test_bare_id():
    assert parse_share_input(" 7664067682870206838 ").aweme_id == "7664067682870206838"


def test_short_url_without_trailing_slash_is_normalised():
    parsed = parse_share_input("look https://v.douyin.com/abc123XYZ end")
    assert parsed.short_url == "https://v.douyin.com/abc123XYZ/"


def test_short_code_length_is_not_assumed():
    """Codes were 11 characters in every sample. That is an observation, not a
    guarantee, so the pattern must not encode it."""
    for code in ("a", "abc123", "aBf60JvMaMU", "aBf60JvMaMUxyz789"):
        parsed = parse_share_input(f"x https://v.douyin.com/{code}/ y")
        assert parsed.short_url == f"https://v.douyin.com/{code}/"


@pytest.mark.parametrize("raw", ["", "   ", "没有链接的一段话", "https://youtube.com/watch?v=abc"])
def test_garbage_is_rejected(raw):
    """Unparseable input is permanent: retrying will not add a link to it."""
    with pytest.raises(UnparseableShare):
        parse_share_input(raw)


def test_canonical_url_drops_tracking_parameters():
    """The URL a short link resolves to carries did/iid/u_code, which identify
    the device that shared it. Only the id is ever kept."""
    url = canonical_url("7670209615078206948")
    assert url == "https://www.douyin.com/video/7670209615078206948"
    for token in ("u_code", "did=", "iid=", "share_sign", "region="):
        assert token not in url
