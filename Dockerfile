FROM python:3.11-slim

# ffmpeg is needed later for audio extraction and keyframe selection.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /srv

# Dependencies are their own layer so a source edit doesn't reinstall them.
COPY pyproject.toml ./
RUN uv pip install --system --no-cache -r pyproject.toml

COPY alembic.ini ./
COPY alembic ./alembic
# Operational scripts ship with the image for the same reason migrations do:
# a data backfill has to be runnable wherever the database is, and copying a
# file onto a running container at the moment you need it is not a procedure.
# Placed ahead of the app because it changes less often.
COPY scripts ./scripts
COPY app ./app

# Nothing this process does needs root, and the install steps above are already
# done by the time we drop. Temporary media goes to /tmp, which stays writable.
RUN useradd --system --create-home --uid 10001 app
USER app

EXPOSE 8000

# --proxy-headers with a wildcard allow-list: a platform terminates TLS at its
# edge and forwards plain HTTP inward, so without this the app sees every request
# as insecure and cannot tell a real HTTP deployment from a correctly proxied
# HTTPS one. The wildcard trusts X-Forwarded-* from any peer that can reach the
# container — acceptable because only the platform's proxy can, and it must be
# revisited if this is ever exposed directly.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", \
     "--proxy-headers", "--forwarded-allow-ips", "*"]
