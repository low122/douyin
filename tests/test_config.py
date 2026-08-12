import pytest
from pydantic import ValidationError

from app.config import Settings

# _env_file=None skips .env so these exercise the environment alone.


def test_missing_token_is_rejected(monkeypatch):
    """A missing token must stop startup, not default to something open."""
    monkeypatch.delenv("API_TOKEN", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://x:y@localhost/z")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_placeholder_token_is_rejected(monkeypatch):
    """A short token is almost always a placeholder someone forgot to replace."""
    monkeypatch.setenv("API_TOKEN", "changeme")
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://x:y@localhost/z")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_missing_database_url_is_rejected(monkeypatch):
    monkeypatch.setenv("API_TOKEN", "t" * 40)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_production_flag(monkeypatch):
    monkeypatch.setenv("API_TOKEN", "t" * 40)
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://x:y@localhost/z")

    monkeypatch.setenv("ENVIRONMENT", "Production")
    assert Settings(_env_file=None).is_production is True

    monkeypatch.setenv("ENVIRONMENT", "development")
    assert Settings(_env_file=None).is_production is False
