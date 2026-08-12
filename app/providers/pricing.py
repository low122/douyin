"""What a call cost, when that can be known honestly.

Only prices that have been checked against a provider's published rate belong
here. An unrecognised model returns None, and the caller stores a null cost
alongside the real token counts. A missing number is recoverable later; a
guessed one silently corrupts every cost figure computed from the table, and
those figures are the entire reason the table exists.

Verified 2026-08-10. Prices change — when a number here looks wrong, check the
provider rather than trusting this file.
"""

from decimal import Decimal

# USD per minute of audio.
PER_MINUTE: dict[tuple[str, str], Decimal] = {
    ("openai", "whisper-1"): Decimal("0.006"),
    ("openai", "gpt-4o-mini-transcribe"): Decimal("0.003"),
    ("openai", "gpt-transcribe"): Decimal("0.0045"),
    ("openai", "gpt-4o-transcribe"): Decimal("0.006"),
}

# USD per token, as (input, output). Deliberately empty: the text and vision
# rates have not been verified yet, and an unverified entry is worse than none.
PER_TOKEN: dict[tuple[str, str], tuple[Decimal, Decimal]] = {}


def transcription_cost(provider: str, model: str, seconds: float | None) -> Decimal | None:
    rate = PER_MINUTE.get((provider, model))
    if rate is None or seconds is None:
        return None
    return (rate * Decimal(str(seconds)) / Decimal("60")).quantize(Decimal("0.000001"))


def token_cost(
    provider: str, model: str, input_tokens: int | None, output_tokens: int | None
) -> Decimal | None:
    rates = PER_TOKEN.get((provider, model))
    if rates is None:
        return None
    per_in, per_out = rates
    total = per_in * Decimal(input_tokens or 0) + per_out * Decimal(output_tokens or 0)
    return total.quantize(Decimal("0.000001"))
