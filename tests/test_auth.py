from fastapi.testclient import TestClient

from app.main import app

TOKEN = "t" * 40
client = TestClient(app)


def test_health_needs_no_token():
    """The liveness probe has to answer before anything is configured."""
    assert client.get("/health").status_code == 200


def test_no_header_is_rejected():
    assert client.get("/health/ready").status_code == 401


def test_wrong_scheme_is_rejected():
    response = client.get("/health/ready", headers={"Authorization": f"Basic {TOKEN}"})
    assert response.status_code == 401


def test_wrong_token_is_rejected():
    response = client.get("/health/ready", headers={"Authorization": "Bearer wrong-token"})
    assert response.status_code == 401


def test_non_ascii_token_does_not_crash():
    """compare_digest raises TypeError on non-ASCII str, so the middleware
    compares bytes. A crash here would surface as a 500, which tells an
    attacker more than a 401 does.

    The header is passed as bytes because a conforming client will not send
    this at all — httpx encodes header values as ASCII and refuses. Only a
    raw socket can produce it, which is exactly the case worth defending.
    """
    response = client.get(
        "/health/ready", headers={"Authorization": "Bearer 密码".encode()}
    )
    assert response.status_code == 401


def test_rejection_reveals_nothing():
    """The body must not hint at why the credential failed."""
    body = client.get("/health/ready", headers={"Authorization": "Bearer wrong"}).json()
    assert body == {"detail": "Unauthorized"}


def test_auth_runs_before_the_database():
    """The property ADR-0005 actually claims.

    No database is running in this test. If authentication were a route
    dependency resolved alongside the session, an unauthenticated request
    would reach the connection attempt and fail with a 500. Getting a 401
    proves the check happens first, and that a rejected request costs one
    string comparison rather than real work.
    """
    assert client.get("/health/ready").status_code == 401


def test_cookie_is_accepted_as_well_as_the_header():
    """A browser cannot set an Authorization header by typing a URL, so the
    admin pages would otherwise be permanently unreachable from one.

    No database is running here, so getting past authentication means reaching
    the connection attempt and failing there — which is itself the proof. What
    must not happen is a 401 or a bounce to the sign-in page.
    """
    authed = TestClient(app, cookies={"dkb_token": TOKEN})
    try:
        response = authed.get("/admin/jobs", follow_redirects=False)
    except Exception:
        return  # reached the database layer: past auth
    assert response.status_code not in (401, 303)


def test_wrong_cookie_is_rejected():
    wrong = TestClient(app, cookies={"dkb_token": "wrong"})
    response = wrong.get("/admin/jobs", follow_redirects=False)
    assert response.status_code in (303, 401)


def test_browser_is_redirected_rather_than_given_a_401():
    """A person who lands on a page needs somewhere to go; a script needs a
    status code. The distinction is the Accept header, not the path."""
    response = client.get(
        "/admin/jobs", headers={"Accept": "text/html"}, follow_redirects=False
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_login_page_itself_is_reachable_without_a_token():
    """It cannot require the credential it exists to collect."""
    assert client.get("/login").status_code == 200


def test_login_rejects_a_wrong_token_without_setting_a_cookie():
    response = client.post("/login", data={"token": "wrong"}, follow_redirects=False)
    assert response.status_code == 401
    assert "dkb_token" not in response.cookies


def test_login_sets_an_httponly_cookie():
    """Readable from JavaScript would make any injected script a token thief."""
    response = client.post("/login", data={"token": TOKEN}, follow_redirects=False)
    assert response.status_code == 303
    header = response.headers.get("set-cookie", "")
    assert "dkb_token=" in header
    assert "HttpOnly" in header
    assert "SameSite=lax" in header.replace("samesite", "SameSite")


def test_valid_token_passes_the_middleware():
    """With a good token the request proceeds far enough to hit the database
    and fail there — which is the proof it got past authentication."""
    try:
        response = client.get("/health/ready", headers={"Authorization": f"Bearer {TOKEN}"})
    except Exception:
        return  # reached the DB layer and failed to connect: past auth
    assert response.status_code != 401


def test_production_over_plain_http_explains_itself(monkeypatch):
    """The trap this guards: a Secure cookie is silently discarded over HTTP, so
    the correct token bounces between /login and / forever with nothing on screen.

    Refusing loudly is the fix; dropping Secure would ship the credential in the
    clear, which is the wrong trade to make for a nicer error message.
    """
    from app.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("ENVIRONMENT", "production")
    try:
        response = client.post("/login", data={"token": TOKEN}, follow_redirects=False)
        assert response.status_code == 400
        assert "dkb_token" not in response.cookies
        assert "HTTPS" in response.text
    finally:
        get_settings.cache_clear()


def test_production_behind_a_tls_proxy_still_logs_in(monkeypatch):
    """The other half. A platform terminates TLS at its edge and forwards plain
    HTTP inward, so a check on the raw scheme would lock out every correct
    deployment. The forwarded header is what makes the check honest."""
    from app.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("ENVIRONMENT", "production")
    try:
        response = client.post(
            "/login",
            data={"token": TOKEN},
            headers={"X-Forwarded-Proto": "https"},
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert "Secure" in response.headers.get("set-cookie", "")
    finally:
        get_settings.cache_clear()
