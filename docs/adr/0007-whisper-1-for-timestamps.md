# Transcription uses whisper-1, and the transcript is a timing skeleton

`whisper-1` is the transcription model, despite being the oldest and neither the
cheapest nor the most accurate option. It is the only one that returns
timestamps, and a Moment is defined by its start and end (ADR-0002) — without
them there is no unit to search.

Measured on a real 315-second Chinese video, 2026-08-10:

| Model | `verbose_json` | Latency | $/min |
|---|---|---|---|
| `gpt-4o-mini-transcribe` | rejected | 10.9 s | 0.003 |
| `gpt-transcribe` | rejected | 12.2 s | 0.0045 |
| `gpt-4o-transcribe` | rejected | 21.0 s | 0.006 |
| `whisper-1` | 200 segments, 1749 word timings | 29.4 s | 0.006 |

All three newer models refuse `verbose_json` outright — this is not a quality
trade, it is a capability one. They cannot do the job at any price.

The awkward part is that they transcribe Chinese *better*. On the opening
sentence `gpt-4o-mini-transcribe` — the cheapest of the four — was the only one
to get 大厂 rather than the homophone 大场, and it punctuated properly;
`gpt-4o-transcribe`, the most expensive, garbled a phrase the cheapest one got
right. Paying more bought nothing.

Rather than run two models and align their output, the transcript's role
changes: it supplies **timing and rough content**, not finished prose.
Extraction already reads the transcript alongside the video's frames and
caption, so it has the context to resolve a homophone that transcription alone
cannot. Errors get corrected where there is evidence to correct them with, at no
additional cost.

Two consequences worth knowing. Per-video cost is double what the cheapest model
would have been, and transcription is now the slowest stage by a wide margin.
And whether a two-call hybrid — cheap model for text, whisper-1 for timings,
aligned — is worth $0.003/min more is an open question that belongs to the eval
set, not to a comparison of one sentence.
