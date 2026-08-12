"""OpenAI implementation of the provider tasks.

Named openai_provider rather than openai so it does not shadow the package it
imports — a module named after its own dependency resolves to itself.
"""

import time
from dataclasses import dataclass
from pathlib import Path

import instructor
from openai import APIStatusError, AsyncOpenAI, AuthenticationError, RateLimitError

from app.extract.schema import ExtractionResult
from app.providers.base import TaskConfig, TranscriptionResult

# The transcription endpoint rejects anything larger. Checked before upload so
# the failure names the real problem instead of surfacing as a generic 413.
MAX_UPLOAD_BYTES = 25 * 1024 * 1024


class ProviderAuthError(Exception):
    """Bad key or exhausted quota. Retrying cannot help, and it will affect
    every video until a human intervenes — so it is worth distinguishing."""


class AudioTooLarge(ValueError):
    """Permanent for this file: no retry will shrink it."""


@dataclass
class ExtractionOutcome:
    result: ExtractionResult
    input_tokens: int | None
    output_tokens: int | None
    latency_ms: int


# The split is by whether a person is waiting, not by which task it is. A search
# that hangs is indistinguishable from a broken page, so anything on a request
# path gets roughly what someone will sit through. Work behind the queue gets
# minutes, because uploading four minutes of audio and reading a 169-line
# transcript both legitimately take them.
#
# The first version of this split named the long one TRANSCRIBE_TIMEOUT and gave
# everything else 30 seconds — which quietly put extraction, a background job, on
# the interactive budget. It then timed out three times and failed the job. Naming
# a timeout after one task invites exactly that: the next background caller
# inherits the wrong one and nothing says so.
INTERACTIVE_TIMEOUT = 30.0
BACKGROUND_TIMEOUT = 300.0


def _client(config: TaskConfig, timeout: float = INTERACTIVE_TIMEOUT) -> AsyncOpenAI:
    return AsyncOpenAI(api_key=config.api_key, base_url=config.base_url, timeout=timeout)


@dataclass
class EmbeddingOutcome:
    vectors: list[list[float]]
    input_tokens: int | None
    latency_ms: int


async def embed_texts(config: TaskConfig, texts: list[str]) -> EmbeddingOutcome:
    """Embed a batch in one request.

    Batched because the per-request overhead dominates for short strings, and a
    video's moments are always embedded together anyway.
    """
    if not texts:
        return EmbeddingOutcome(vectors=[], input_tokens=0, latency_ms=0)

    client = _client(config)
    started = time.monotonic()
    try:
        response = await client.embeddings.create(model=config.model, input=texts)
    except (AuthenticationError, RateLimitError) as exc:
        raise ProviderAuthError(f"{config.provider}/{config.model}: {exc}") from exc
    except APIStatusError as exc:
        if exc.status_code in (401, 402, 403, 429):
            raise ProviderAuthError(f"{config.provider}/{config.model}: {exc}") from exc
        raise
    finally:
        await client.close()

    # Sorted by index: the API does not promise input order in the response.
    items = sorted(response.data, key=lambda d: d.index)
    return EmbeddingOutcome(
        vectors=[item.embedding for item in items],
        input_tokens=getattr(response.usage, "prompt_tokens", None),
        latency_ms=int((time.monotonic() - started) * 1000),
    )


async def extract_moments(config: TaskConfig, messages: list[dict]) -> ExtractionOutcome:
    """Turn a transcript and its frames into structured moments.

    `instructor` is what makes the schema binding rather than advisory: it
    validates the response against the model and re-asks on failure, feeding the
    validation error back so the retry is informed. Written against the
    OpenAI-compatible surface, so the same path serves any provider exposing one
    (ADR-0004).
    """
    raw = _client(config, BACKGROUND_TIMEOUT)
    client = instructor.from_openai(raw, mode=instructor.Mode.JSON)
    started = time.monotonic()

    try:
        result, completion = await client.chat.completions.create_with_completion(
            model=config.model,
            messages=messages,
            response_model=ExtractionResult,
            max_retries=2,
        )
    except (AuthenticationError, RateLimitError) as exc:
        raise ProviderAuthError(f"{config.provider}/{config.model}: {exc}") from exc
    except APIStatusError as exc:
        if exc.status_code in (401, 402, 403, 429):
            raise ProviderAuthError(f"{config.provider}/{config.model}: {exc}") from exc
        raise
    finally:
        await raw.close()

    usage = getattr(completion, "usage", None)
    return ExtractionOutcome(
        result=result,
        input_tokens=getattr(usage, "prompt_tokens", None),
        output_tokens=getattr(usage, "completion_tokens", None),
        latency_ms=int((time.monotonic() - started) * 1000),
    )


async def transcribe(
    config: TaskConfig,
    audio_path: Path,
    language: str = "zh",
    prompt: str | None = None,
) -> TranscriptionResult:
    """Transcribe an audio file, with timestamps.

    `verbose_json` is what carries the segment boundaries. Only whisper-1
    accepts it — the newer models return plain text and cannot be used here at
    any price (ADR-0007). Asking for it also means a wrong model fails loudly
    rather than quietly returning a transcript with no timings.
    """
    size = audio_path.stat().st_size
    if size > MAX_UPLOAD_BYTES:
        raise AudioTooLarge(
            f"{audio_path.name} is {size / 1_048_576:.1f} MB; the limit is 25 MB"
        )

    client = _client(config, BACKGROUND_TIMEOUT)
    started = time.monotonic()
    try:
        with audio_path.open("rb") as handle:
            response = await client.audio.transcriptions.create(
                model=config.model,
                file=handle,
                response_format="verbose_json",
                language=language,
                timestamp_granularities=["segment", "word"],
                # Omitted rather than sent empty: the parameter biases decoding,
                # and an empty sample is not the same request as no sample.
                **({"prompt": prompt} if prompt else {}),
            )
    except (AuthenticationError, RateLimitError) as exc:
        raise ProviderAuthError(f"{config.provider}/{config.model}: {exc}") from exc
    except APIStatusError as exc:
        if exc.status_code in (401, 402, 403, 429):
            raise ProviderAuthError(f"{config.provider}/{config.model}: {exc}") from exc
        raise
    finally:
        await client.close()

    latency_ms = int((time.monotonic() - started) * 1000)
    payload = response.model_dump() if hasattr(response, "model_dump") else dict(response)

    segments = [
        {"start": s.get("start"), "end": s.get("end"), "text": (s.get("text") or "").strip()}
        for s in (payload.get("segments") or [])
    ]

    return TranscriptionResult(
        text=payload.get("text", ""),
        language=payload.get("language"),
        duration_sec=payload.get("duration"),
        segments=segments,
        words=payload.get("words") or None,
        latency_ms=latency_ms,
    )
