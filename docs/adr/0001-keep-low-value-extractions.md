# Never let the model silently discard an extraction

Extraction produces a relevance score, but nothing is dropped on the basis of it — every extraction is stored, and filtering happens at query time.

The tempting alternative is a `worth_keeping` boolean that discards low-value material at ingest, keeping the store clean. We rejected it because its failure mode is silent: when the model judges wrongly, the user never learns that something was lost — you do not go looking for a thing you don't know exists. There is no error, no log, no symptom. Text is cheap to store (kilobytes per video) and the model's judgement is unverified until the eval set exists, so paying storage to keep the mistake recoverable is the better trade.

This may be revisited once the eval set can demonstrate that discard precision is high enough — but that reversal must be driven by measurement, not by the store looking untidy.
