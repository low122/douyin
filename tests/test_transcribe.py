"""The two halves of the mishearing fix: prevention and repair.

Both exist because of one measured failure — `RAG` heard once as `RAJ`, then
written into six of seven moments by the extraction step that read the
transcript as fact.
"""

from app.providers.base import TranscriptionResult
from app.transcribe.corrections import CORRECTIONS, correct_text, correct_transcription
from app.transcribe.vocabulary import TERMS, transcription_prompt

# whisper-1 caps `prompt` at 224 tokens and drops the overflow without saying
# so, which would silently discard whatever sits at the end of the list. There
# is no tokeniser in this project's dependencies, so the budget is checked in
# characters with room to spare rather than pretended to be exact.
PROMPT_CHAR_BUDGET = 600


def test_the_prompt_stays_inside_the_token_budget():
    """A list that grows past the cap loses its tail in silence, and the tail is
    where a newly added term would be."""
    assert len(transcription_prompt()) < PROMPT_CHAR_BUDGET


def test_the_prompt_contains_the_term_that_failed():
    assert "RAG" in transcription_prompt()


def test_no_duplicate_terms():
    """A duplicate spends budget twice and buys nothing."""
    assert len(TERMS) == len(set(TERMS))


def test_a_correction_target_is_never_itself_a_wrong_spelling():
    """Order of application would otherwise decide the result: a rule rewriting
    into a string another rule rewrites away is a bug that shows up only for
    whichever entry happens to run second."""
    assert not set(CORRECTIONS.values()) & set(CORRECTIONS)


def test_the_observed_mishearing_is_repaired():
    """Verbatim from the stored transcript, no spaces added.

    The first version of this test padded the acronym with spaces and passed
    against a rule that could never fire on the real data: Python counts CJK as
    word characters, so \\bRAJ\\b finds no boundary between 响 and R. Written
    with spaces it tested the assumption instead of the corpus.
    """
    fixed, n = correct_text("影响RAJ系统的准确性")
    assert fixed == "影响RAG系统的准确性"
    assert n == 1


def test_the_mishearing_is_repaired_with_spaces_too():
    fixed, n = correct_text("这套 RAJ 系统的准确性")
    assert fixed == "这套 RAG 系统的准确性"
    assert n == 1


def test_correction_is_case_insensitive_but_writes_the_canonical_form():
    fixed, _ = correct_text("raj 和 Raj")
    assert fixed == "RAG 和 RAG"


def test_a_latin_key_does_not_fire_inside_a_longer_word():
    """Without word boundaries this would corrupt any word containing the key."""
    fixed, n = correct_text("RAJA RAJESH")
    assert n == 0
    assert fixed == "RAJA RAJESH"


def test_correction_reaches_the_segments_not_only_the_full_text():
    """The regression that matters. Segments are what moments quote and what
    reaches the search index, so repairing `text` alone would leave the
    searchable copy wrong — which is the failure being fixed."""
    result = TranscriptionResult(
        text="RAJ 很重要",
        segments=[{"start": 0.0, "end": 2.0, "text": "RAJ 很重要"}],
        words=[{"start": 0.0, "end": 0.5, "word": "RAJ"}],
    )
    fixed, total = correct_transcription(result)

    assert fixed.text == "RAG 很重要"
    assert fixed.segments[0]["text"] == "RAG 很重要"
    assert fixed.words[0]["word"] == "RAG"
    assert total == 3


def test_correction_preserves_timings():
    """Rewriting text must not disturb the boundaries moments are snapped to."""
    result = TranscriptionResult(
        text="RAJ",
        segments=[{"start": 1.5, "end": 4.25, "text": "RAJ"}],
    )
    fixed, _ = correct_transcription(result)
    assert fixed.segments[0]["start"] == 1.5
    assert fixed.segments[0]["end"] == 4.25


def test_clean_text_is_left_alone():
    fixed, n = correct_text("RAG 和 大厂都没问题")
    assert n == 0
    assert fixed == "RAG 和 大厂都没问题"


def test_the_observed_homophone_is_repaired():
    """Verbatim from the transcript. 「字节二面」 in the next breath is what makes
    大厂面试 the reading rather than a guess."""
    fixed, n = correct_text("每天体验一场大场面试 今天我们要面的是 字节二面")
    assert "大厂面试" in fixed
    assert n == 1


def test_a_real_word_containing_the_homophone_survives():
    """The reason the key is the whole phrase and not 大场: this is a genuine
    word, and a narrower rule that fixes the observed error without touching it
    is strictly better than a broad one that needs luck."""
    fixed, n = correct_text("那真是个大场面")
    assert n == 0
    assert fixed == "那真是个大场面"


def test_missing_words_stay_missing():
    """whisper returns no word list for some inputs; a None must not become []."""
    fixed, _ = correct_transcription(TranscriptionResult(text="RAJ", words=None))
    assert fixed.words is None
