"""What the search page does when the embedding call fails.

Each of these is an outage, not a bad query, and the distinction is the whole
point: rendering "nothing matched" states a fact about the corpus, so the reader
goes off to rephrase a query that was never actually run.

No database is running here. The session is stubbed rather than mocked into a
real one because these tests are about the page's behaviour on a provider
failure, and the counters it renders are incidental to that.
"""

import httpx
import pytest
from fastapi.testclient import TestClient
from openai import APIConnectionError, APITimeoutError

from app.db.session import get_session
from app.main import app
from app.providers import openai_provider
from app.providers.base import MissingCredential
from app.providers.openai_provider import ProviderAuthError

TOKEN = "t" * 40
QUERY = "大模型幻觉"

_REQUEST = httpx.Request("POST", "https://api.openai.com/v1/embeddings")


class _EmptySession:
    async def scalar(self, *args, **kwargs):
        return 0

    async def scalars(self, *args, **kwargs):
        return []


async def _stub_session():
    yield _EmptySession()


@pytest.fixture
def client():
    app.dependency_overrides[get_session] = _stub_session
    yield TestClient(app, cookies={"dkb_token": TOKEN})
    app.dependency_overrides.clear()


def _search_failing_with(client, monkeypatch, failure):
    async def boom(*args, **kwargs):
        raise failure

    monkeypatch.setattr(openai_provider, "embed_texts", boom)
    return client.get("/", params={"q": QUERY})


@pytest.mark.parametrize(
    "failure",
    [
        MissingCredential("no key"),
        ProviderAuthError("openai/text-embedding-3-small: 429"),
        APITimeoutError(request=_REQUEST),
        APIConnectionError(message="connection refused", request=_REQUEST),
    ],
    ids=["missing-key", "rejected", "timeout", "unreachable"],
)
def test_a_provider_failure_is_a_page_not_a_500(client, monkeypatch, failure):
    """A stack trace is not a search result. Every one of these reaches the
    reader as a rendered page carrying an explanation."""
    response = _search_failing_with(client, monkeypatch, failure)
    assert response.status_code == 200
    assert "banner" in response.text


@pytest.mark.parametrize(
    "failure",
    [
        MissingCredential("no key"),
        ProviderAuthError("openai/text-embedding-3-small: 429"),
        APITimeoutError(request=_REQUEST),
    ],
    ids=["missing-key", "rejected", "timeout"],
)
def test_a_failure_never_claims_the_corpus_had_nothing(client, monkeypatch, failure):
    """The regression this file exists for. "没有找到相关片段" asserts that the
    search ran and came back empty; on these paths it never ran at all."""
    response = _search_failing_with(client, monkeypatch, failure)
    assert "没有找到相关片段" not in response.text


def test_the_error_does_not_land_in_the_search_box(client, monkeypatch):
    """The message used to be concatenated onto `q`, which is echoed back into
    the input — so the reader's next keystroke edited an error message."""
    response = _search_failing_with(client, monkeypatch, MissingCredential("no key"))
    assert f'value="{QUERY}"' in response.text
