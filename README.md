# Douyin Knowledge Base

*[中文说明](README.zh-CN.md)*

Turns saved Douyin videos into searchable moments. Share a video from your
phone; it comes back as time-bounded segments you can search, each linking to
the second it starts.

The problem it solves is specific: saving videos on Douyin is easy and finding
one again months later is not. This is a search engine for your own saved
videos, not a summariser.

```
你: 固定长度切分文档有什么问题

  3:02 – 4:08   语义切片与索引          全文#1 + 向量#1
  按语义单元切分而非固定长度，让每个片段保持完整的语义边界…
  ▶ 跳到 3:02
```

## How it works

```mermaid
flowchart LR
  A[iOS Shortcut] -->|share link| B[POST /ingest]
  B --> C[(queue)]
  C --> D[resolve + metadata]
  D --> E[audio → transcript<br/>with timings]
  E --> G[extract moments]
  G --> H[(Postgres<br/>+ pgvector)]
  H --> I[hybrid search]
```

Ingest records the link and returns; everything after takes minutes and runs on
a worker. No media survives the job — only text is kept.

Extraction reads the transcript alone. Keyframes were sent with it until they
were measured against the alternative and made coverage worse; see
[ADR-0009](docs/adr/0009-transcript-only-extraction.md) for the numbers and for
what that experiment does not establish.

Search runs full-text and vector retrieval and fuses them by rank. Vector alone
answers "pgvector" with everything about databases and misses the moment that
names it; full-text alone misses a moment that explains the idea in other words.

## Running it

Requires Docker and one OpenAI API key.

```bash
git clone https://github.com/low122/douyin.git
cd douyin
python3 scripts/setup_env.py     # creates .env, generates secrets
# put your key in .env: OPENAI_API_KEY=sk-...
docker compose up -d --build
docker compose run --rm api alembic upgrade head
```

Open <http://localhost:8000> and sign in with the `API_TOKEN` that
`setup_env.py` generated (it is in `.env`).

To add a video, share it from Douyin and post the text it copies — the whole
blob, no tidying:

```bash
curl -X POST http://localhost:8000/ingest \
  -H "Authorization: Bearer $API_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"text": "7.61 YzT:/ ... https://v.douyin.com/XXXXXXX/ 复制此链接..."}'
```

There is also a paste box at `/add`, which needs no terminal: copy the share
text in Douyin, paste, submit. It runs the same code path as the endpoint above.

An iOS Shortcut doing the same POST turns this into two taps from the Douyin
share sheet — see [docs/ios-shortcut.md](docs/ios-shortcut.md). It is the
fastest way in once built, and the fiddliest to set up; the paste box exists so
that is a choice rather than a prerequisite.

> When `.env.example` gains a new setting, run `python3 scripts/setup_env.py`
> again rather than copying it over `.env`. The script merges; copying destroys
> every generated secret and every key you pasted in.

## Configuration

Every AI step resolves to a provider and model independently, so model choice is
configuration rather than a call site. The defaults point all four at one
provider, which is why a fresh install needs exactly one key.

| Task | Default | Why |
|---|---|---|
| `TRANSCRIBE` | `whisper-1` | The only model that returns timestamps, and a moment is defined by its start and end. The newer models are cheaper, faster, better at Chinese, and cannot do this. |
| `EXTRACT` | `deepseek-v4-flash` | Extraction is text-only, so it does not need a multimodal model. Matched gpt-4o on coverage in the ADR-0009 runs; roughly a fifth of the input tokens, and about four times slower. |
| `EMBED` | `text-embedding-3-small` | Column width is fixed at migration time; changing this needs a migration, not just an env change. |

Splitting tasks across providers is opt-in — set `EXTRACT_BASE_URL` and
`EXTRACT_API_KEY` to point one task at any OpenAI-compatible endpoint.

## Evaluating changes

```bash
python evals/run_eval.py
```

Two layers, doing different jobs. Six **structural checks** need no labelling
and run against every video: does the extraction span the source, are the
timestamps real, is it written in a language the user can search in, does the
relevance score separate anything. A **labelled retrieval set** asks the
different question of whether a search finds the right moment.

Both matter. On one run every structural check passed while retrieval sat at 33%
Recall@1 — well-formed output that could not be found, because the index held a
60-character summary of a 53-second moment instead of what was said in it.

## What this does not do

- **No RAG.** Search returns moments; it does not generate an answer over them.
  A wrong ranking is visible — you scroll. A fabricated answer is not, because
  you are searching for something you already forgot. Generation waits until
  retrieval can be shown to be good enough to build on.
- **No batch import.** One shared link per request, deliberately. The absence is
  a scope boundary, not a missing feature.
- **No media is stored.** Video and audio exist for the length of one job.
- **Single user.** Shared-token auth, no accounts.

## Known limitations

- Retrieval is at 50% Recall@1 on a six-query seed set. Both remaining misses
  return an adjacent moment rather than something unrelated, which is a better
  failure mode but still a failure.
- Transcription makes homophone errors in Chinese (大场/大厂). Extraction
  corrects the ones it can from context; the residual rate is unmeasured.
- Douyin throttles the share page under repeated requests. Normal use is
  nowhere near it; a batch backfill would find it in minutes.
- Cost per video is not reported. Token counts are recorded for every call, but
  the price table only covers transcription, so extraction and embedding rows
  carry a null cost rather than a guessed one.

## Documentation

- [`docs/design-decisions.md`](docs/design-decisions.md) — every decision, why,
  and what was rejected
- [`docs/adr/`](docs/adr/) — the ones that were hard to reverse
- [`docs/douyin-platform-notes.md`](docs/douyin-platform-notes.md) — how Douyin
  actually behaves, measured rather than documented
- [`CONTEXT.md`](CONTEXT.md) — what "video" and "moment" mean here

Python 3.11 · FastAPI · Postgres 16 + pgvector · arq · Docker
