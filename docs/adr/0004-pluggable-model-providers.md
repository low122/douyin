# Every AI step is a task with a swappable provider

Transcription, frame understanding, extraction, and embedding are each a named task resolved to a provider and model through environment variables. Model choice is configuration, never a call site. Two adapters cover the field: one for Anthropic, one for any OpenAI-compatible endpoint, which reaches most other hosted and local models by changing a base URL.

An aggregator such as OpenRouter or LiteLLM would have collapsed this to a single key and a single client. We declined because it makes a mandatory dependency out of what should be an optional convenience: the default configuration points every task at one provider, so a new deployment needs exactly one key, and only someone who actually wants per-task cost optimisation pays the cost of extra accounts.

Consequences worth knowing before extending this. Per-token prices live in a local table that goes stale — an unrecognised model records its token counts with a null cost rather than a fabricated one. Structured-output support varies by provider and some models cannot enforce a schema at all, so the layer degrades to prompt-plus-validate-plus-retry; this is the least pleasant part of the abstraction. Configuration is validated at startup so a missing key fails immediately rather than partway through a video.
