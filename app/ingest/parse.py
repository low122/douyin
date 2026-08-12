"""Pull a usable reference out of whatever the share sheet produced.

What Douyin copies is not a URL. It is a blob: a numeric prefix, several
obfuscation tokens, the caption, the link, and a trailing instruction. The
tokens appear in a different order every time, so nothing positional survives
contact with the second sample — the URL is the only stable anchor.
See docs/douyin-platform-notes.md for the measured examples.
"""

import re
from dataclasses import dataclass

# The share-sheet form. The code is opaque: do not pin its length.
SHORT_URL = re.compile(r"https?://v\.douyin\.com/[A-Za-z0-9]+/?")

# A canonical link, in case someone pastes one straight from a browser.
CANONICAL_URL = re.compile(r"https?://(?:www\.)?douyin\.com/video/(\d+)")

# The resolved share page, should anyone paste that instead.
SHARE_PAGE_URL = re.compile(r"https?://(?:www\.)?iesdouyin\.com/share/video/(\d+)")

# A bare id, for the API's own convenience and for tests.
BARE_ID = re.compile(r"^\s*(\d{15,24})\s*$")


class UnparseableShare(ValueError):
    """No Douyin reference could be found in the input."""


@dataclass(frozen=True)
class ParsedShare:
    """Either a short link that still needs resolving, or an id we already have.

    A short link costs a network round trip to resolve; an id does not. Keeping
    them apart lets the worker skip work it doesn't need.
    """

    short_url: str | None = None
    aweme_id: str | None = None


def parse_share_input(raw: str) -> ParsedShare:
    """Extract a reference from share text, a URL, or a bare id.

    Raises UnparseableShare when there is nothing to work with. That is a
    permanent failure — no amount of retrying will add a link to the text.
    """
    if not raw or not raw.strip():
        raise UnparseableShare("input is empty")

    if m := CANONICAL_URL.search(raw):
        return ParsedShare(aweme_id=m.group(1))

    if m := SHARE_PAGE_URL.search(raw):
        return ParsedShare(aweme_id=m.group(1))

    if m := SHORT_URL.search(raw):
        # Normalise to a trailing slash; Douyin serves both, and being
        # consistent keeps logs and tests readable.
        url = m.group(0)
        return ParsedShare(short_url=url if url.endswith("/") else url + "/")

    if m := BARE_ID.match(raw):
        return ParsedShare(aweme_id=m.group(1))

    raise UnparseableShare("no Douyin link found in the shared text")


def canonical_url(aweme_id: str) -> str:
    """The only URL form worth storing.

    Rebuilt from the id rather than kept from the redirect: the resolved URL
    carries did, iid and u_code, which together identify the device that
    produced the share. Storing it would put a fingerprint in the database.
    """
    return f"https://www.douyin.com/video/{aweme_id}"
