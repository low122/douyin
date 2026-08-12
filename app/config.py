from functools import lru_cache

from pydantic import Field, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    environment: str = "development"
    log_level: str = "INFO"

    # No default on purpose. A missing token must stop the service from
    # starting rather than silently leaving it open (ADR-0005). The length
    # floor rejects a placeholder like "changeme" as well as an empty value.
    api_token: str = Field(min_length=32)

    # Also no default: a fallback connection string with a password baked in
    # is exactly the kind of thing that ends up shipped by accident.
    database_url: str

    redis_url: str = "redis://localhost:6379/0"

    # Provider keys. Present as explicit fields rather than read loosely from the
    # environment so a missing one is a startup error, not a surprise three
    # minutes into a video.
    openai_api_key: str | None = None
    deepseek_api_key: str | None = None

    # Per-task model routing (ADR-0004). Each AI step resolves independently, so
    # the defaults can all point at one provider — one key for a fresh install —
    # while anyone who wants per-task cost optimisation splits them.
    transcribe_provider: str = "openai"
    transcribe_model: str = "whisper-1"
    transcribe_base_url: str | None = None
    transcribe_api_key: str | None = None

    extract_provider: str = "openai"
    extract_model: str = "gpt-4o-mini"
    extract_base_url: str | None = None
    extract_api_key: str | None = None

    embed_provider: str = "openai"
    embed_model: str = "text-embedding-3-small"
    embed_base_url: str | None = None
    embed_api_key: str | None = None

    @property
    def is_production(self) -> bool:
        return self.environment.strip().lower() == "production"


@lru_cache
def get_settings() -> Settings:
    """Load settings once, or fail loudly with something actionable."""
    try:
        return Settings()
    except ValidationError as exc:
        fields = ", ".join(
            ".".join(str(part) for part in error["loc"]).upper() for error in exc.errors()
        )
        raise RuntimeError(
            "Refusing to start: configuration is incomplete.\n"
            f"  Problem with: {fields}\n"
            "  Fix: cp .env.example .env, then fill in the required values.\n"
            '  Token: python -c "import secrets; print(secrets.token_urlsafe(32))"'
        ) from exc
