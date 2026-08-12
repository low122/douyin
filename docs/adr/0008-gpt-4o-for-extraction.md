# Extraction runs on gpt-4o, which is also the cheaper option

> **Superseded in part by [ADR-0009](0009-transcript-only-extraction.md).** The
> frames this decision was measured on are no longer sent, and extraction runs
> on `deepseek-v4-flash`. The token-multiplier finding below still stands for
> anyone sending images to a small vision model; it is simply no longer a
> situation this pipeline is in.

Extraction uses `gpt-4o` rather than `gpt-4o-mini`. The mini model was the
original default on the assumption that it was the economical choice for a
high-volume step. On a frame-heavy workload that assumption is inverted.

One 315-second video, 16 frames, identical prompt:

| | gpt-4o-mini | gpt-4o |
|---|---|---|
| Input tokens | 594,766 | **23,086** |
| Moments produced | 11 | 11 |
| Mean moment length | 4.2 s | **23.4 s** |
| Share of the video covered | 14.6% | **81.8%** |
| Invented timestamps | 7 of 11 | 1 of 11 |

Images are charged at a far higher token multiplier on the mini model: the same
sixteen frames cost twenty-six times more tokens to read. Multiplied against
each model's published per-token rate, gpt-4o comes out cheaper in absolute
terms — while covering more than five times as much of the video.

The quality gap is not subtle either. Asked for moments of 15 to 60 seconds,
gpt-4o-mini produced two-to-four-second slivers that named each of the video's
six sections without capturing any of their content; gpt-4o produced 26-to-42
second spans that contain the explanations. Both models were given the same
instruction; only one could act on it.

Two things this changes beyond the model name. "Use the small model to save
money" is not a safe default for vision — the token multiplier has to be
measured per model, and a price-per-token comparison alone is misleading. And a
prompt improvement was worth measuring separately: sharpening it moved
gpt-4o-mini from 7.6% to 14.6% coverage and got the section structure right,
which is what made it clear the remaining gap was capability rather than
phrasing.

## The full record, kept here because the rows are not

Four extraction runs over the same 312-second video, in the order they happened:

| Schema | Model | Moments | Mean length | Coverage |
|---|---|---|---|---|
| 1 | gpt-4o-mini | 10 | 2 s | 7.6% |
| 2 | **gpt-4o** | 11 | 23 s | **82.5%** |
| 2 | gpt-4o-mini | 11 | 4 s | 14.7% |
| 3 | gpt-4o | 9 | 30 s | **86.6%** |

The two middle rows are the ones that carry the argument: same schema, same
prompt, two models, 82.5% against 14.7%. Without both, the record reads 7.6% →
86.6% and cannot say whether the prompt or the model produced the change. That
comparison was only possible because a re-run was written alongside the previous
one instead of over it — the reason `model` was part of the moment's unique key.

That key has since been narrowed and the old rows deleted, because the design
was only half-built: extraction and embedding filtered on `(schema_version,
model)` while search filtered on `schema_version` alone. The write path treated
a model change as a separate dataset and the read path did not, so any install
that changed `EXTRACT_MODEL` — which ADR-0004 explicitly invites — and re-ingested
a video would have got every moment back twice under near-identical titles. The
capability was retired rather than completed: the comparison it existed for is
this table, the table is now written down, and one live set per video removes
both the duplicate-result bug and a store that grew on every re-run with nothing
to ever retire it.

What is kept is the finding, not the evidence for it. That is the right trade
once a decision is made and recorded — but it is one-way, so the numbers went in
here before the rows went out.
