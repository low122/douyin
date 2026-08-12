"""Domain vocabulary handed to the transcription model.

whisper-1 takes a `prompt`, which is not an instruction but a sample of the kind
of text to expect. Terms appearing in it become likelier decodings, which is
exactly what an acronym needs: `RAG` spoken inside a Chinese sentence gives the
model almost no context to disambiguate from `RAJ`, `RAC` or `拉哥`.

This is the cheapest place in the pipeline to fix a term, because every stage
after transcription treats the transcript as ground truth. Measured on this
corpus: `RAG` was misheard once in a six-minute video, and the extraction model
then wrote that misspelling into six moments out of seven. An error here is
quoted downstream, not corrected there.

The API caps the prompt at 224 tokens and silently ignores the overflow, so this
list has to stay short. It earns its length by covering only terms that are
(a) acronyms or English words spoken inside Chinese, where the model has the
least context to work with, and (b) actually likely in the videos being saved.
Adding every word in the field would push the useful ones past the cutoff.
"""

# Grouped by why each one is here, not alphabetically — the grouping is what
# makes it obvious where a new term belongs, and what can be dropped when the
# list next runs long.
TERMS: tuple[str, ...] = (
    # Retrieval and search: the vocabulary this tool is itself built from, and
    # the group the observed failure came from.
    "RAG", "embedding", "vector", "pgvector", "chunk", "rerank", "recall",
    "BM25", "OCR", "PDF",
    # Models and training.
    "LLM", "GPT", "Claude", "Gemini", "token", "prompt", "fine-tune",
    "transformer", "attention", "context window", "temperature", "logits",
    "hallucination", "benchmark", "evaluation", "SFT", "RLHF",
    # Agents and tooling.
    "agent", "function calling", "MCP", "workflow", "pipeline", "API",
    "latency", "throughput", "GPU", "inference",
    # Engineering, for the career and business videos.
    "Docker", "Kubernetes", "Postgres", "Redis", "backend", "deploy",
    "code review", "system design",
)

# Framed as a sentence rather than handed over as a bare list: the parameter is
# documented as a sample of expected text, and a fragment that reads like speech
# biases decoding better than an inventory does.
_FRAME = "以下是一段中文技术讲解，其中会出现这些英文术语："


def transcription_prompt(terms: tuple[str, ...] = TERMS) -> str:
    """The `prompt` value for a transcription request."""
    return _FRAME + "、".join(terms) + "。"
