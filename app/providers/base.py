"""Resolving a task to the provider and model that will serve it.

Every AI step names a task — transcribe, extract, embed — and each one
resolves independently through configuration (ADR-0004). Nothing in the pipeline
mentions a model by name.
"""

from dataclasses import dataclass, field

from app.config import Settings, get_settings

TASKS = ("transcribe", "extract", "embed")


class MissingCredential(RuntimeError):
    """A task points at a provider whose key is not configured.

    Raised at resolve time so the failure is 'operator' rather than a confusing
    401 partway through a video (see FailureKind.OPERATOR).
    """


@dataclass(frozen=True)
class TaskConfig:
    task: str
    provider: str
    model: str
    api_key: str
    base_url: str | None = None


@dataclass
class TranscriptionResult:
    text: str
    language: str | None = None
    duration_sec: float | None = None
    # [{"start": float, "end": float, "text": str}]
    segments: list[dict] = field(default_factory=list)
    words: list[dict] | None = None
    latency_ms: int = 0


def _provider_key(settings: Settings, provider: str, task_key: str | None) -> str | None:
    """A key set on the task wins; otherwise fall back to the provider's own.

    The fallback is what makes a single-key install work: leave every
    {TASK}_API_KEY blank and one OPENAI_API_KEY covers all four tasks.
    """
    if task_key:
        return task_key
    return {
        "openai": settings.openai_api_key,
        "deepseek": settings.deepseek_api_key,
    }.get(provider)


# Every provider here speaks the OpenAI wire format, so the client is the same
# one and only the address changes. Supplied as a default rather than demanded
# in .env: a self-hoster switching provider should have to set the provider and
# the model, not go and find a URL that is fixed per provider anyway.
DEFAULT_BASE_URL = {
    "deepseek": "https://api.deepseek.com",
}


def resolve_task(task: str, settings: Settings | None = None) -> TaskConfig:
    if task not in TASKS:
        raise ValueError(f"unknown task {task!r}; expected one of {TASKS}")

    settings = settings or get_settings()
    provider = getattr(settings, f"{task}_provider")
    model = getattr(settings, f"{task}_model")
    base_url = getattr(settings, f"{task}_base_url") or DEFAULT_BASE_URL.get(provider)
    api_key = _provider_key(settings, provider, getattr(settings, f"{task}_api_key"))

    if not api_key:
        raise MissingCredential(
            f"{task.upper()}_PROVIDER is '{provider}' but no key is configured. "
            f"Set {task.upper()}_API_KEY, or the provider's own key in .env."
        )

    return TaskConfig(
        task=task, provider=provider, model=model, api_key=api_key, base_url=base_url
    )
