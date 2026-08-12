"""Guards on the one irreversible action in the product.

No database runs here, so the cascade itself is not what is under test — that is
enforced by ON DELETE CASCADE on the foreign keys, which holds for every caller
including psql. What is under test is the route layer above it, where the
damaging mistakes are: deleting on a GET, or deleting without a commit.
"""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.db.session import get_session
from app.main import app

TOKEN = "t" * 40

VIDEO = SimpleNamespace(
    id=7,
    caption="测试视频",
    author_name="某人",
    duration_sec=312.0,
    source_url="https://www.douyin.com/video/1",
    created_at=datetime(2026, 8, 12, tzinfo=timezone.utc),
)


class _RecordingSession:
    """Records what the route asked for instead of doing it."""

    def __init__(self, video):
        self._video = video
        self.deleted: list = []
        self.commits = 0

    async def get(self, _model, _pk):
        return self._video

    async def delete(self, obj):
        self.deleted.append(obj)

    async def commit(self):
        self.commits += 1

    async def scalar(self, *_args, **_kwargs):
        return 0

    async def scalars(self, *_args, **_kwargs):
        return []

    async def execute(self, *_args, **_kwargs):
        return []


def _client(video=VIDEO):
    session = _RecordingSession(video)

    async def _override():
        yield session

    app.dependency_overrides[get_session] = _override
    return TestClient(app, cookies={"dkb_token": TOKEN}), session


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


def test_the_confirmation_page_does_not_delete():
    """The bug this exists for. A destructive GET is reachable by a link
    prefetch, a crawler, or a browser restoring tabs — none of which asked for
    anything, and all of which would have deleted a video."""
    client, session = _client()
    response = client.get(f"/library/{VIDEO.id}/delete")
    assert response.status_code == 200
    assert session.deleted == []
    assert session.commits == 0


def test_the_confirmation_page_states_what_goes_with_it():
    """A button labelled 删除 does not say that the transcript and every moment
    go too, and that is the part that cannot be undone cheaply."""
    client, _ = _client()
    body = client.get(f"/library/{VIDEO.id}/delete").text
    assert "无法恢复" in body
    assert "片段" in body


def test_post_deletes_and_commits():
    client, session = _client()
    response = client.post(f"/library/{VIDEO.id}/delete", follow_redirects=False)
    assert response.status_code == 303
    assert session.deleted == [VIDEO]
    assert session.commits == 1


def test_deleting_something_that_is_gone_is_not_an_error():
    """Two taps on the same confirm button, or a stale open tab. The second one
    has nothing to do, and a 500 would suggest it did something."""
    client, session = _client(video=None)
    response = client.post("/library/999/delete", follow_redirects=False)
    assert response.status_code == 303
    assert session.deleted == []


def test_confirming_a_missing_video_goes_back_rather_than_500ing():
    client, _ = _client(video=None)
    response = client.get("/library/999/delete", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/library"


def test_delete_requires_authentication():
    """Unauthenticated must not reach the route at all — the middleware answers
    first, and this is the request where that matters most."""
    anonymous = TestClient(app)
    response = anonymous.post("/library/7/delete", follow_redirects=False)
    assert response.status_code in (401, 303)
    assert response.headers.get("location") != "/library"
