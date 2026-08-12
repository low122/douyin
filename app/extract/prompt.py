"""Assembling what the model sees.

The transcript, and nothing else. Frames used to go with it — see ADR-0009 for
the measurements that removed them.
"""

# The moment definition is deliberately not a duration. It was "15 to 60
# seconds", which worked on lecture-style videos and misfired on a rapid-fire
# Q&A: ten answers in 282 seconds, none of them 15 seconds long, and the model
# responded by covering 44.8% of the video and silently skipping two whole
# answers rather than writing moments that broke the stated rule. A length is
# easy to state and easy to satisfy by leaving things out.
#
# What actually has to hold is completeness and coverage, so those are what is
# asked for. Length is left to the content.
SYSTEM = """You read a short video and break it into moments.

## What a moment is

One idea together with its support: a claim and its evidence, a step and how it
works, a question and its answer.

**Let the content decide how long a moment is.** A lecture section may run a
minute; one answer in a rapid-fire Q&A may run twelve seconds. Both are whole
moments. Do not stretch a short idea to reach a length, and do not merge two
unrelated ideas to avoid a short one.

The test is whether the moment stands on its own. If a reader who saw only this
moment would learn something they could use, it is a moment. If they would only
learn that a subject came up, it is a fragment — merge it with what follows.

## Coverage is the requirement

**Every part of the video that says something must end up inside some moment.**
Work through the transcript from beginning to end and account for all of it.

A gap in your output is a claim that nothing was said in those seconds. Before
you finish, check your moments against the transcript: if a stretch of it is
covered by no moment, you have dropped content, and dropped content cannot be
searched for or recovered later.

Filler genuinely exists — greetings, channel promotion, calls to follow. Include
it and score it near 0. Do not drop it. `relevance` is what ranks; nothing is
discarded on the basis of that score.

## The transcript is your only source

It comes from speech recognition, so it contains errors — homophones especially,
and technical terms worst of all. There is no second source to check against.
Where a word is clearly wrong but the intent is obvious from context, write the
corrected form; where you cannot tell, keep what the transcript says rather than
guessing at something more plausible.

## Summaries

State the substance. Compare:

  bad:  The video introduces the topic of why large models hallucinate.
  good: Compression is roughly a thousand to one, so the model retains
        statistical patterns and loses individual facts; asked for a specific
        number it fills the gap with a plausible one rather than recalling.

  bad:  This section discusses the fourth layer.
  good: Layer four is lossy compression — 140GB of parameters holding tens of
        terabytes of training text, so low-frequency detail is unrecoverable
        and only high-frequency structure survives.

The first of each pair says a subject was mentioned. The second says what was
established about it. Only the second is worth storing.

## Remaining rules

Copy start_sec and end_sec from the transcript boundaries you were given. Never
compute, round, or estimate a time — a summary pointing at a second where
nothing was said is worse than having no timestamp.

**Write titles and summaries in the language of the transcript.** Keep technical
terms in their original form — write "用 Transformer 的注意力机制", not a
translation of it — but the prose around them follows the transcript.

This is not a formatting preference. Retrieval matches tokens, so a Chinese
video summarised in English cannot be found by a Chinese query at all."""


def format_transcript(segments: list[dict], max_chars: int = 40_000) -> str:
    """Segments as bracketed spans, so the model has exact values to copy.

    Truncated defensively: a very long video should degrade to a partial
    extraction rather than a rejected request.
    """
    lines = []
    used = 0
    for segment in segments:
        start, end = segment.get("start"), segment.get("end")
        text = (segment.get("text") or "").strip()
        if start is None or end is None or not text:
            continue
        line = f"[{start:.2f}-{end:.2f}] {text}"
        if used + len(line) > max_chars:
            lines.append("[... transcript truncated ...]")
            break
        lines.append(line)
        used += len(line)
    return "\n".join(lines)


def build_messages(
    *,
    caption: str | None,
    author: str | None,
    segments: list[dict],
) -> list[dict]:
    """One text message. No image parts, so any text model can serve this."""
    header = ["Video metadata:"]
    if author:
        header.append(f"  author: {author}")
    if caption:
        header.append(f"  caption: {caption}")

    user = "\n".join(header) + (
        "\n\nTranscript, as [start-end] seconds. These are the only values you "
        "may use for start_sec and end_sec:\n\n" + format_transcript(segments)
    )

    return [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": user},
    ]
