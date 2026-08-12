# Extraction reads the transcript only; frames are removed

Keyframes were sampled with ffmpeg and sent to the extraction model alongside
the transcript, on the reasoning in [ADR-0008](0008-gpt-4o-for-extraction.md):
slide-driven videos put the substance on screen and use narration as connective
filler, so a transcript alone would miss most of it.

Measured on two videos, three conditions, one variable changed at a time. Same
prompt (v4) throughout, and coverage is the share of the video's runtime that
falls inside some moment.

| | | A: gpt-4o + frames | B: gpt-4o, no frames | C: deepseek-v4-flash, no frames |
|---|---|---|---|---|
| **Video 15** | moments | 10 | 13 | 15 |
| | coverage | 92.4% | **98.9%** | 97.8% |
| | mean length | 34 s | 28 s | 24 s |
| **Video 16** | moments | 22 | 16 | 15 |
| | coverage | **49.7%** | 91.7% | **98.2%** |
| | mean length | 7 s | 17 s | 19 s |

**A against B is the frames, and frames lost.** Removing them raised coverage on
both videos, by 42 points on the second. With 13 frames attached, gpt-4o cut
video 16 into 22 moments averaging seven seconds and covering half the runtime —
the fragmentation the v4 prompt had just fixed, reintroduced by the images.

The mechanism is a hypothesis, not a measurement: the model transcribes every
frame into `frame_notes` before writing moments, and appears to then segment
along frames rather than along ideas. Frames become the unit.

**B against C is the model**, and on text the two are equivalent — 98.9/91.7
against 97.8/98.2, with deepseek ahead on the video gpt-4o fragmented.

## What this does not establish

Both videos are talking-head Q&A from similar creators. Their frames show a
person speaking over a burned-in subtitle strip, which is narration repeated —
no new information, and plausibly a distraction. **The slide-driven case that
justified frames in the first place was not tested at all.** Two videos of one
format cannot retire a feature on the evidence; the decision to remove it was
made with that stated.

So this is reversible by measurement, and the shape of the counter-evidence is
known in advance: a deck-heavy video where a transcript-only extraction misses
what is written on screen.

Reversing it, however, means writing the code again. The frame sampler, the
image parts of the prompt, and `frame_notes` were deleted from a working tree
that had never been committed, so they are not in this repository's history and
cannot be recovered from it — only rebuilt. That is a worse position than this
document first claimed, and it is recorded here rather than quietly corrected:
the cost of removing a subsystem is only knowable if you know whether you can
get it back.

## What removing it also bought

- **Any text model can serve extraction.** The image parts were the only reason
  the model had to be multimodal, which ruled out most cheaper providers. The
  configured model is now `deepseek-v4-flash`.
- **The `vision` task is gone.** It had been declared in `TASKS` and configured
  in four settings, and was never resolved anywhere in the pipeline — dead
  configuration that would have been copied into a self-hoster's `.env` and
  wondered about.
- **Extraction input dropped roughly five-fold.** 21,515 input tokens with
  frames against 4,720 without, on the same video. Note the other direction
  though: deepseek returned 47,029 output tokens across three calls against
  gpt-4o's 8,062 across four, and averaged 101 seconds per call against 23. It
  is cheaper on input and slower by four times, and whether that is cheaper
  overall depends on prices this repository does not have verified numbers for.
