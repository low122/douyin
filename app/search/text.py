"""Chinese segmentation, done in Python because Postgres cannot do it.

`to_tsvector` splits on whitespace and punctuation, which for Chinese means the
whole sentence becomes one token and full-text search stops working. The
alternatives were an extension such as zhparser — compiled into the image, and a
deployment failure waiting to happen for anyone self-hosting — or segmenting
before the text ever reaches the database. This is the second.

Both indexing and querying must go through here. Segmenting one side only
produces a silent miss: the tokens simply never match.
"""

import logging
import re

import jieba

log = logging.getLogger(__name__)

# Latin words and numbers survive segmentation intact, but jieba splits some of
# them oddly. Pulling them out first keeps terms like "gpt-4o" and "pgvector"
# whole, which is the entire reason full-text sits beside vector search.
LATIN_TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9._+-]*|\d+(?:\.\d+)?")


def warm_up() -> None:
    """Build jieba's dictionary now rather than during the first search.

    The first call takes on the order of a second; paying it at startup keeps it
    out of a user-facing request.
    """
    jieba.initialize()
    log.info("jieba dictionary loaded")


def segment(text: str | None) -> str:
    """Space-joined tokens, ready for `to_tsvector('simple', ...)`."""
    if not text:
        return ""

    tokens: list[str] = []
    for token in jieba.cut(text):
        token = token.strip()
        if not token:
            continue
        # Keep latin/numeric runs verbatim; lowercase so a query for "GPT-4o"
        # matches an indexed "gpt-4o".
        if LATIN_TOKEN.fullmatch(token):
            tokens.append(token.lower())
        else:
            tokens.append(token)

    return " ".join(tokens)


def build_search_text(*parts: str | None) -> str:
    """What gets indexed for one moment.

    Keywords are repeated rather than merged in once: a proper noun that the
    extraction singled out should weigh more than the same word appearing in
    passing inside a summary.
    """
    return segment(" ".join(p for p in parts if p))
