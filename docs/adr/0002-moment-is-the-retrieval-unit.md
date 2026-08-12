# Search returns Moments, not Videos

The retrieval unit is the Moment — a time-bounded span inside a Video, carrying its own summary. A query returns Moments across many Videos, grouped by their source; the Video itself is only provenance.

Indexing whole Videos would have been simpler, and it is what a "video knowledge base" usually means. We rejected it because the product's success condition is landing the user on the exact few seconds that answer their question. Returning a twelve-minute video and leaving them to find the passage fails that, no matter how good the ranking is.

The cost is paid at extraction: the model must split a transcript into self-contained spans with defensible boundaries, which is harder to get right — and harder to evaluate — than summarising a video as a whole.
