# Design Decisions

One line per decision: what was chosen, why, and what was rejected. Detail lives in `docs/adr/`.

> Numbers marked `TBD` are not measured yet. Do not quote them until they are.

---

### Entry point: iOS Shortcuts, not a Douyin mini program

A mini program cannot work here, for three independent reasons: individual (non-business) identities cannot register one; video mounting needs a business entity plus an A-grade rating; and decisively, the open-platform video API only returns *the authorizing user's own* published videos, so a mini program can never read a third party's video content. The iOS share sheet is outside Douyin's control and costs zero development.

**Considered:** mini program (blocked), browser automation of the favourites list (breaks on every redesign), a native app with a Share Extension (Swift plus a paid developer account, for an entry point worth two taps).

### One shared bearer token, checked before anything else runs

A public IP is found by indiscriminate scanners within hours, and requesting a TLS certificate publishes the hostname to Certificate Transparency logs — the address is discoverable without ever being shared. Unauthenticated, the ingest endpoint spends the operator's model budget on request and the UI publishes everything they have saved. Two details do most of the work: the check is middleware ahead of all business logic, so a rejected request costs a string comparison rather than a link resolution and a model call; and the comparison is constant-time, because a short-circuiting `==` leaks how many leading characters matched. → ADR-0005

**Considered:** a full account system (contradicts the single-user scope, and is itself a larger attack surface), and a mesh VPN with no public exposure (defeats the attack outright, but asks a self-hoster to understand VPNs — documented as hardening instead).

### Every model runs behind an API; nothing is installed locally

Local transcription and embedding would cut per-video cost by roughly 60%, but need around 6 GB of RAM — more than the machines this is meant to be deployed on, and more than the author's own. Going API-only holds the footprint near 500 MB, which is what makes a small VPS a viable target and keeps the promise that a stranger can run this.

**Considered:** local `faster-whisper` and a local embedding model. Cheaper per video and offline-capable, but only on hardware that neither the author nor a typical self-hoster has. ADR-0004 means this stays a configuration change, not a rewrite, for anyone whose hardware differs.

### Moments, not Videos, are what search returns

The product's success condition is landing on the exact seconds that answer a question. Returning a twelve-minute video and leaving the user to scrub through it fails that regardless of ranking quality. → ADR-0002

**Considered:** video-level indexing — simpler extraction and simpler evaluation, but it moves the last mile of the search onto the user.

### Ranked search, not RAG — for now

Retrieval and generation fail differently. Bad ranking is visible (the right result is third instead of first); a fabricated synthesis is not, because the user came to the system precisely *because* they had forgotten. RAG is deferred until the eval set can show retrieval is accurate enough to build on.

**Considered:** RAG in V1. It is the more impressive demo and the weaker product until retrieval is measured.

### Nothing is discarded silently

Extraction scores relevance but never drops anything; filtering happens at query time. A wrong discard has no error, no log, and no symptom — the user never learns something was lost. Text is cheap; an unverified model judgement is not. → ADR-0001

**Considered:** a `worth_keeping` boolean at ingest. Revisit once evals can demonstrate discard precision.

### Async queue with an idempotency key

Ingest writes a row, enqueues, and returns; processing runs on `arq`. Video work is minutes long and cannot sit on an HTTP request. `aweme_id` is the unique key, resolved from the share link — sharing the same video twice must not pay for ASR and extraction twice. A per-stage state machine means a retry resumes rather than re-downloading.

**Considered:** synchronous processing (times out), and a `(url, timestamp)` key (share links differ per share, so it would deduplicate nothing).

### Extraction reads the transcript only

Keyframes were sampled and sent alongside the transcript, on the reasoning that slide-driven videos put the substance on screen. Measured against the alternative, they made extraction worse: on two talking-head videos, removing frames raised coverage from 92.4% to 98.9% and from 49.7% to 91.7% — with frames attached, the model segmented along images rather than along ideas and cut one video into 22 fragments averaging seven seconds. Removing them also drops the multimodal requirement, so extraction can run on any text model. → ADR-0009

**Considered:** keeping frames (the original design — retired on measurement, with the caveat that the slide-driven case it was built for was never tested), and RapidOCR locally (a whole extra stage and a de-duplication problem, to feed a signal that turned out to be harmful).

### Hybrid retrieval, and jieba over a Postgres extension

Vector search alone misses exact matches on proper nouns — a search for `pgvector` should not return "databases, retrieval, embeddings". Chinese full-text needs segmentation that stock Postgres does not do; segmenting in Python and storing space-joined text keeps the official `postgres:16` image with pgvector and nothing else.

**Considered:** `zhparser` / `pg_jieba` (compiled into the image; a deployment failure mode for every self-hoster), and vector-only retrieval (loses precise terms).

### Providers are configuration, not code

Each AI step — transcription, extraction, embedding — resolves to a provider and model through environment variables. Two adapters cover the field, since most models expose an OpenAI-compatible endpoint. The default points every task at one provider, so a new deployment needs exactly one key; per-task cost optimisation is opt-in. → ADR-0004

**Considered:** OpenRouter and LiteLLM. Both make a required dependency out of an optional convenience, and both were rejected for that reason rather than on capability.

### `instructor` rather than LangChain

The layer needs three things: call a model, enforce a JSON schema with retries, read token usage. `instructor` does the second — the only hard one — and nothing else. LangChain is a framework whose abstractions propagate through the codebase, and its main advantage, tracking new model features quickly, is not something this project needs.

**Considered:** LangChain (heavier than the LiteLLM already rejected on dependency grounds), and hand-rolling the retry and validation loop.

### Per-call cost is recorded, not inferred from the bill

Every model call writes task, provider, model, tokens, cost, and latency. A billing dashboard gives a monthly total; it cannot answer which task is expensive or what one video costs, which is the only evidence that per-task model routing was worth doing. Prices for the models actually in use live in a small local table; an unrecognised model records tokens with a null cost rather than a fabricated one.

---

## Implementation details worth defending

Smaller than the decisions above, but each one is a specific thing to point at.

- **`secrets.compare_digest`, not `==`.** String equality short-circuits on the first mismatched character, so the comparison finishes fractionally sooner the earlier it fails. That timing difference recovers the token one character at a time, turning an infeasible brute force into a few thousand requests. `compare_digest` takes the same time regardless.
- **Auth is middleware, not a route dependency**, and there is a test that proves it: with no database running, an unauthenticated request to a database-backed route returns 401 rather than 500. A dependency-based check would have reached the connection attempt. The property being protected is that a rejected request costs one string comparison — not a link resolution, not a model call.
- **The service refuses to start without a token.** The setting has no default and a length floor, so both an absent value and a leftover `changeme` fail at startup. The alternative — defaulting to an empty string — produces a service that runs perfectly and is wide open.
- **Liveness and readiness are separate endpoints.** `/health` is public, shallow, and says nothing about internals, because a container health check must answer before anything is configured. `/health/ready` is authenticated and touches the database, because whether the database is reachable is not a stranger's business.
- **Migrations read `DATABASE_URL` from the environment, not from the app's settings object.** A migration job needs database access and nothing else; coupling it to the app config would mean no migration could run without an API token set.
- **Enabling the pgvector extension is a migration, not a manual step.** A fresh database is reproducible from `alembic upgrade head` alone, with nothing to remember.
- **The local Python version is pinned to match the Dockerfile.** Left alone, the toolchain picked 3.14 locally against 3.11 in the image — the setup where a newer-syntax feature passes locally and fails only in the container.
- **The Compose project name is pinned rather than derived.** Compose builds it from the directory name, which sanitises to an empty string for a non-ASCII path and refuses to start. Pinning it also keeps container names stable whatever a self-hoster calls their clone.
- **Development conveniences live in a separate Compose overlay.** The base file has no source bind-mount, so it runs on any host; hot reload is opt-in through `docker-compose.dev.yml`. This came out of a failure rather than foresight — see below.
- **Idempotency is a unique constraint, not a check before insert.** Two workers can both look for a video, both find nothing, and both insert; only the constraint settles it. The conflicting insert runs inside a savepoint so the outer transaction survives the `IntegrityError` and can go on to find the row that won.
- **A failed stage returns its outcome instead of raising it.** Raising would carry the exception out through the transaction boundary and roll back the very row recording *why* the job failed — leaving a job that failed with no reason attached, which is the same silent-loss failure ADR-0001 exists to prevent. The stage returns, the transaction commits, and only then does the caller raise to trigger a retry.
- **Parsing happens at the edge; resolving happens in the worker.** Extracting the link from share text is a regular expression: offline, instant, and enough to reject nonsense with a 400 before anything is queued. Turning that link into an id needs a third-party request, which has no business on the response path.
- **Foreign keys are indexed explicitly.** Postgres does not create an index for a foreign key the way MySQL does, and Alembic's autogenerate only emits what the models declare — its own generated comment says `please adjust!`. Neither will remind you.
- **Retrieval fields live on the moment; there is no chunk table.** An earlier design had one, with a `kind` column, from when it was still open what a search should return. Once that settled on the moment (ADR-0002) the second table was only a join and a way for the two to drift apart.
- **Hybrid results are fused by rank, not by score.** A cosine distance and a `ts_rank` are not on comparable scales, and normalising them introduces constants that need tuning per corpus. Reciprocal Rank Fusion uses positions only, so it needs none — a moment that both retrievers rank first scores twice what either alone produces, which is visible in the results.
- **The query is segmented by the same function that segmented the index.** Segmenting one side only is a silent failure: the tokens never match, the full-text branch quietly contributes nothing, and the final ordering looks plausible because the vector branch still works.
- **Autogenerate produced a migration that could not run.** It referenced pgvector's type without importing it, and emitted neither the GIN expression index nor the HNSW vector index — an expression index and a vector index are both invisible to model introspection. Left as generated, both halves of hybrid search would have been sequential scans, and the migration would have raised `NameError` first.

- **Only the API publishes a port to the outside; the datastores publish to loopback.** `"5432:5432"` binds every interface, so the database and an unauthenticated Redis were reachable by anything sharing the Wi-Fi. That is harmless while the whole stack is one laptop talking to itself, and stops being harmless the moment the API is opened up for a phone to reach — which is the normal next step for this tool, not an exotic one. The containers never used those published ports anyway: they resolve each other by service name on the compose network. The general form of the mistake is publishing a port for the convenience of a `psql` on the host and not noticing that the same line answers the network.
- **Timeouts are set per task, not per client.** One `AsyncOpenAI` factory served every call, carrying the 300 seconds that transcription genuinely needs — a four-minute audio upload is not a fast request. Search reused the same factory, so a slow embedding call could hold a browser for five minutes with the page unchanged. The long timeout now belongs to the one call that earns it; the interactive default is 30 seconds, roughly what a person will sit through before deciding the page is broken.
- **A submitted form marks itself busy.** Search waits on a network round-trip, and for that whole time the page was byte-identical to the one before the click — which is how a working page gets reported as a hung one. A spinner on the button and a form that ignores a second click cost eleven lines and no dependency. The `pageshow` handler matters as much: the back button restores from the bfcache with the DOM exactly as it was left, so without it a returning reader lands on a button spinning over a page that finished loading.
- **A provider failure renders as a page, not as "nothing matched".** Only `MissingCredential` was caught, so a rate limit or a dropped connection became a 500. Worse, the handled case wrote its message onto `q` — which is echoed back into the search input, so the reader's next keystroke edited an error message. Errors now travel in their own field, and the empty state is suppressed when the search never ran: "没有找到相关片段" asserts a fact about the corpus, and stating it during an outage sends the reader off to rephrase a query that was never executed.
- **The setup script only ever adds.** `.env.example` used to say `cp .env.example .env`, which is correct exactly once; run it again after the example gains a setting and it silently destroys every generated secret and every key that was pasted in. `scripts/setup_env.py` merges instead: fills blanks, appends missing keys, never overwrites a value that exists.

## Measured: one transcription error becomes six

Searching `RAG` returned a single moment about traceability and evaluation —
plausible-looking, and chosen for none of the reasons a reader would assume.
Counting the actual strings explains it:

| Where | `RAJ` | `RAG` |
| --- | --- | --- |
| Whisper's transcript (one 6-minute video) | 4 | 1 |
| Indexed moment text | 6 | 1 |

Whisper got the acronym wrong **four times out of five** — in
「RAJ系统」, 「Agent的RAJ」, and 「RAJ 检索增强」, the last naming the expansion in
the same breath as the misspelling. The extraction model then read that
transcript and reproduced the ratio, six wrong to one right. A full-text search
for `RAG` therefore matches the single moment that happened to get it right, and
that is the entire reason it ranked first.

Two things worth taking from it:

- **No stage corrects the one before it.** The extraction model had 「RAJ 检索增强」
  in front of it — the term and its Chinese expansion side by side — and wrote
  `RAJ` anyway. Every stage treats its input as ground truth, so the error is
  carried forward at roughly the rate it arrived. There is no point in the
  pipeline where being wrong gets noticed.
- **The first measurement of this was itself wrong, for the same reason the fix
  was.** Counting with `\bRA[GJ]\b` reported one occurrence, not four: Python
  counts CJK as word characters, so `\b` finds no boundary in 影响RAJ系统 and the
  pattern silently skipped every acronym that was actually embedded in Chinese —
  which is all of them. A measurement tool sharing a bug with the code it
  measures agrees with it.
- **The distance gate looked mis-calibrated and was not.** Every moment sat
  outside the 0.70 cutoff for this query — `RAG` → 0.722 nearest, `RAG是什么` →
  0.711 — which reads exactly like a threshold set too tight, and the obvious
  next move is to loosen it. Repairing the text instead moved the same
  neighbours to **0.604** and **0.576**, comfortably inside a gate that never
  changed:

  | Query | Before repair | After repair |
  | --- | --- | --- |
  | `RAG是什么` | 0.711 (blocked) | 0.576 (passes) |
  | `RAG` | 0.722 (blocked) | 0.604 (passes) |

  The reading that fit the first set of numbers — "bare acronyms embed poorly" —
  was wrong, and wrong in a way that would have caused damage: raising the gate
  to 0.75 to admit them also admits a moment from the *hallucination* video at
  0.746, so the corpus would have been permanently noisier to work around a bug
  in the corpus. **A measurement taken over corrupt data describes the
  corruption, not the component being measured.** The vector half was not
  failing to compensate for the keyword half; there was simply nothing near
  `RAG` to find, because nothing in the index said `RAG`.

Current state. A hit no retriever strongly vouched for is labelled rather than
hidden, and a result set with no semantic support at all says so above the list.
The cause is addressed in two places, deliberately separate because they are
different kinds of thing:

- **Prevention** — `app/transcribe/vocabulary.py` sends whisper a domain
  vocabulary as its `prompt`, so the acronym has a likelier decoding than a name
  that sounds similar. Capped at 224 tokens by the API and silently truncated
  past that, so the list is short by necessity, not by taste.
- **Repair** — `app/transcribe/corrections.py` rewrites mishearings that have
  actually been observed. Deterministic, and strictly a backstop: it cannot
  catch an error nobody has seen. Each entry is a liability as well as a fix,
  which is why 大场面试 is keyed on the whole phrase — a bare 大场 would also
  rewrite 大场面, a real word this corpus could contain.

- **Backfill** — `scripts/backfill_corrections.py` applies the current rules to
  data already stored, because a rule added today does nothing for a video saved
  yesterday, and an unfindable term stays unfindable. Dry run by default.

  Its own first version was half a repair, in the same shape as the bug it was
  written for: it cleared the embedding of the two moments whose *title* changed
  and left the rest, having missed that a moment's indexed text also contains
  the speech inside its time range — so correcting a transcript invalidates the
  index of every moment in that video. Three visible strings fixed, three more
  left in the index.

Re-transcribing was considered and not done. The vocabulary prompt improves
future transcripts and cannot improve old ones; the corrections already repair
the known errors in the stored text. Paying whisper again would buy only the
chance of catching an error nobody has identified yet. The backfill re-embeds
18 moments and costs a fraction of a cent — after which `RAG` returns three
results with two of them semantically matched, where it previously returned one
keyword coincidence.

## A capability retired rather than completed

Moments were keyed on `(video_id, schema_version, model, start_sec)` so that two
models could be extracted side by side and compared — the method that chose
gpt-4o, with the numbers now in [ADR-0008](adr/0008-gpt-4o-for-extraction.md).

It was half-built. Extraction and embedding filtered on `(schema_version,
model)`; **search filtered on `schema_version` alone**. The write path treated a
model change as a separate dataset and the read path did not, so the two sets the
key carefully kept apart came back interleaved. Demonstrated on the stored data:
at schema 2, video 1 holds `大模型产生幻觉的原因` from gpt-4o at 4s and the same
title from gpt-4o-mini at 5s. Any install that changed `EXTRACT_MODEL` — which
[ADR-0004](adr/0004-pluggable-model-providers.md) explicitly invites — and
re-ingested a video would have got every moment back twice.

It stayed dormant only because every video happened to hold one model at the
current schema. That is a coincidence, not an invariant, and it was one
configuration change from ending.

The fix was to retire the capability rather than finish it. The key is now
`(video_id, start_sec)`, `schema_version` and `model` are metadata rather than
identity, and a successful extraction deletes what the video carried before —
after the new set is back and snapped, so a failed call leaves the old one
standing. 42 superseded rows went in the migration.

Two things worth keeping from it:

- **A comparison mechanism needs an end condition.** "Keep both so they can be
  compared" was right while comparing and became unbounded growth the moment the
  decision was made, because nothing was ever going to declare it over. The
  design had a create path and no retire path.
- **What survives a comparison is the finding, not the evidence.** The four
  coverage numbers went into the ADR; the 42 rows did not need to stay to prove
  them. That trade is one-way, so the order mattered: numbers written down
  first, rows deleted second.

## Likely follow-ups

- **How do you know the extraction is any good?** — A hand-labelled eval set and a scoring script; accuracy went from `TBD` to `TBD` by `TBD`. Model selection was decided by that set, not by picking the most expensive option.
- **What breaks first at scale?** — Ingest is fine. Because every model call is an API call, the work is IO-bound rather than CPU-bound, so the limit is provider rate limits and per-video cost, not worker throughput.
- **Why Postgres for vectors instead of a dedicated vector database?** — One datastore, one backup path, and transactional consistency between a moment and its embedding. A dedicated store earns its place at a scale this does not reach.
- **What would you do differently?** — I put development conveniences in the base Compose file: a bind-mount of the working tree plus `--reload`. It ran fine locally right up until the host declined to share the directory — macOS treats `~/Documents` as protected and Docker failed with `operation not permitted`. The base file had stopped being the one that runs anywhere, and I only found out because the environment happened to refuse. Conveniences belong in an overlay you opt into; the default should be the configuration with the fewest assumptions about the host. *(More to add as the build continues — this section is only worth having if it stays honest.)*
