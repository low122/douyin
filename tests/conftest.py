import os

# Set, not setdefault. With setdefault these values yield to whatever is already
# exported, so the suite passes when run in a bare shell and fails when run
# after sourcing .env — the token the tests assert on is not the token the app
# loads. A test that only passes given a particular way of invoking it is not
# passing; the environment has to be pinned here.
os.environ["API_TOKEN"] = "t" * 40
os.environ["DATABASE_URL"] = "postgresql+asyncpg://test:test@localhost:5432/test"
os.environ["OPENAI_API_KEY"] = "sk-test-not-a-real-key"

# Reset the per-task routing too, so a developer's own .env cannot change what
# the tests exercise.
for task in ("TRANSCRIBE", "VISION", "EXTRACT", "EMBED"):
    os.environ[f"{task}_PROVIDER"] = "openai"
    os.environ.pop(f"{task}_BASE_URL", None)
    os.environ.pop(f"{task}_API_KEY", None)
os.environ["TRANSCRIBE_MODEL"] = "whisper-1"
os.environ["VISION_MODEL"] = "gpt-4o-mini"
os.environ["EXTRACT_MODEL"] = "gpt-4o-mini"
os.environ["EMBED_MODEL"] = "text-embedding-3-small"
