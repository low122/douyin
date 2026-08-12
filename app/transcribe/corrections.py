"""Known mishearings, repaired after the fact.

The vocabulary prompt makes these rarer; it does not make them impossible, and
it does nothing at all for transcripts already in the database. This is the
deterministic backstop — and being deterministic is the whole point, because the
alternative on offer is asking a model to notice its own input was wrong.

What it cannot do is catch an error nobody has seen yet. Only observed
mishearings are listed, so this shrinks a known problem rather than solving the
general one.

Every entry is also a liability. `RAJ` is a name; a video that genuinely says it
gets corrupted here, and the correction is invisible in the stored transcript
because the original is not kept. That is why the list is short, matched on word
boundaries, and restricted to terms whose correct form is overwhelmingly more
likely in this corpus than the literal reading.
"""

import re
from dataclasses import replace

from app.providers.base import TranscriptionResult

# Wrong spelling -> right one. Keys are matched case-insensitively on word
# boundaries; the replacement is written exactly as it should appear.
CORRECTIONS: dict[str, str] = {
    # Observed four times in one six-minute video against one correct spelling,
    # in contexts that leave no doubt: 「RAJ系统」, 「Agent的RAJ」, and — decisively
    # — 「RAJ 检索增强」, which names the expansion in the same breath.
    "RAJ": "RAG",
    # Observed twice, in 「每天体验一场大场面试」 alongside 「字节二面」: 大厂面试,
    # a big-tech interview. Keyed on the whole phrase rather than 大场 alone,
    # because a bare 大场 would also rewrite 大场面 — a real word, and one this
    # corpus could plausibly contain. The narrower key fixes what was actually
    # seen and cannot fire on anything else.
    "大场面试": "大厂面试",
}

# \b is the wrong tool here, and wrong in the case that matters most. Python
# counts CJK as word characters, so in 影响RAJ系统 there is no boundary between 响
# and R — a \bRAJ\b never fires on an acronym embedded in Chinese, which is the
# only place these acronyms ever appear. What is actually wanted is "not glued to
# more Latin", so RAJ is still refused inside RAJA.
#
# CJK keys are matched literally: there is no boundary to anchor to at all, which
# is why those entries have to be chosen for having no common longer form.
def _pattern(wrong: str) -> re.Pattern[str]:
    if wrong.isascii():
        return re.compile(
            rf"(?<![A-Za-z0-9]){re.escape(wrong)}(?![A-Za-z0-9])", re.IGNORECASE
        )
    return re.compile(re.escape(wrong))


_COMPILED = [(_pattern(wrong), right) for wrong, right in CORRECTIONS.items()]


def correct_text(text: str) -> tuple[str, int]:
    """Return the corrected string and how many substitutions were made."""
    total = 0
    for pattern, right in _COMPILED:
        text, n = pattern.subn(right, text)
        total += n
    return text, total


def correct_transcription(result: TranscriptionResult) -> tuple[TranscriptionResult, int]:
    """Apply the corrections across every field that carries text.

    Segments and words are corrected too, not just the full text: the segments
    are what moments quote and what reaches the search index, so fixing only
    `text` would leave the searchable copy wrong — which is the exact failure
    this exists to stop.
    """
    text, total = correct_text(result.text)

    segments = []
    for segment in result.segments:
        fixed, n = correct_text(segment.get("text") or "")
        total += n
        segments.append({**segment, "text": fixed})

    words = None
    if result.words is not None:
        words = []
        for word in result.words:
            fixed, n = correct_text(word.get("word") or "")
            total += n
            words.append({**word, "word": fixed})

    return replace(result, text=text, segments=segments, words=words), total
