import secrets

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse

# Reachable without a token. Keep this list as short as it can be: a liveness
# probe has to work before anything else does, and the sign-in page cannot
# require the credential it exists to collect.
PUBLIC_PATHS = frozenset({"/health", "/login"})

# The same secret, delivered the only way a browser can deliver it. A shortcut
# on a phone sets an Authorization header; a person typing a URL cannot, so
# without this the admin pages would be permanently unreachable from a browser
# — which ADR-0005 did not account for.
COOKIE_NAME = "dkb_token"


class BearerTokenMiddleware(BaseHTTPMiddleware):
    """Reject unauthenticated requests before any handler runs.

    This is middleware rather than a per-route dependency so that a rejected
    request costs one string comparison — no link resolution, no model call,
    no database round trip (ADR-0005).
    """

    def __init__(self, app, token: str) -> None:
        super().__init__(app)
        self._token = token.encode()

    def _presented(self, request: Request) -> str:
        scheme, _, credential = request.headers.get("authorization", "").partition(" ")
        if scheme.lower() == "bearer" and credential:
            return credential
        return request.cookies.get(COOKIE_NAME, "")

    async def dispatch(self, request: Request, call_next):
        if request.url.path in PUBLIC_PATHS or request.url.path.startswith("/static/"):
            return await call_next(request)

        # compare_digest, not ==. A short-circuiting comparison returns sooner
        # the earlier it finds a mismatch, and that timing difference is enough
        # to recover the token one character at a time. This one always takes
        # the same amount of time.
        presented = self._presented(request)
        if not presented or not secrets.compare_digest(presented.encode(), self._token):
            # A browser gets sent somewhere it can do something about it; an API
            # client gets the status code it knows how to handle. Both are
            # deliberately uninformative about *why* the credential failed —
            # telling the caller that hands them a way to iterate.
            if "text/html" in request.headers.get("accept", ""):
                return RedirectResponse("/login", status_code=303)
            return JSONResponse({"detail": "Unauthorized"}, status_code=401)

        return await call_next(request)
