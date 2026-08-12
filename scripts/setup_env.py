#!/usr/bin/env python3
"""Create or update .env from .env.example, without destroying anything.

    python3 scripts/setup_env.py

Safe to run any number of times. It only ever adds: keys present in the example
but missing from .env are appended, blank secrets are filled with generated
values, and any key that already has a value is left exactly as it is.

This exists because the obvious move when .env.example gains a new setting is to
copy it over .env again — which silently wipes every generated secret and every
key you pasted in. Run this instead.

Standard library only, so it works before dependencies are installed.
"""

from __future__ import annotations

import re
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXAMPLE = ROOT / ".env.example"
TARGET = ROOT / ".env"

# Blank values for these get a generated secret rather than the example's value.
GENERATED = {
    "API_TOKEN": lambda: secrets.token_urlsafe(32),
    "POSTGRES_PASSWORD": lambda: secrets.token_urlsafe(16),
}

# Values that must be supplied by hand; reported at the end if still blank.
MANUAL = ("OPENAI_API_KEY",)

LINE = re.compile(r"^([A-Z][A-Z0-9_]*)=(.*)$")


def parse(text: str) -> dict[str, str]:
    return {m.group(1): m.group(2) for m in (LINE.match(ln) for ln in text.splitlines()) if m}


def main() -> int:
    if not EXAMPLE.exists():
        print(f"error: {EXAMPLE.name} is missing", file=sys.stderr)
        return 1

    example = parse(EXAMPLE.read_text())
    existing = parse(TARGET.read_text()) if TARGET.exists() else {}

    # Start from the example so comments and ordering survive; substitute values.
    lines: list[str] = []
    generated: list[str] = []
    kept = 0

    for raw in EXAMPLE.read_text().splitlines():
        m = LINE.match(raw)
        if not m:
            lines.append(raw)
            continue
        key, example_value = m.groups()
        current = existing.get(key, "")

        if current:
            value = current
            kept += 1
        elif key in GENERATED:
            value = GENERATED[key]()
            generated.append(key)
        else:
            value = example_value

        lines.append(f"{key}={value}")

    merged = parse("\n".join(lines))

    # Keep the connection string's password in step with the database password,
    # but only when it still looks like the untouched placeholder — a URL
    # pointing at someone's own database must not be rewritten.
    url = merged.get("DATABASE_URL", "")
    password = merged.get("POSTGRES_PASSWORD", "")
    if password and ("CHANGEME" in url or not url):
        new_url = f"postgresql+asyncpg://douyin:{password}@localhost:5432/douyin"
        lines = [f"DATABASE_URL={new_url}" if ln.startswith("DATABASE_URL=") else ln for ln in lines]
        merged["DATABASE_URL"] = new_url
    elif password and (m := re.search(r"://[^:]+:([^@]+)@", url)) and m.group(1) != password:
        print("  !  DATABASE_URL's password differs from POSTGRES_PASSWORD — left as-is.")

    # Anything the user added that the example doesn't know about stays.
    extra = [k for k in existing if k not in example]
    if extra:
        lines.append("")
        lines.append("# Keys not present in .env.example, preserved from your previous .env")
        lines.extend(f"{k}={existing[k]}" for k in extra)

    TARGET.write_text("\n".join(lines) + "\n")

    added = [k for k in example if k not in existing]
    print(f"{'updated' if existing else 'created'} .env")
    print(f"  kept {kept} existing value(s)")
    if generated:
        print(f"  generated: {', '.join(generated)}")
    if added:
        print(f"  added from example: {', '.join(added)}")
    if extra:
        print(f"  preserved unknown key(s): {', '.join(extra)}")

    missing = [k for k in MANUAL if not merged.get(k)]
    if missing:
        print(f"\n  Still needs a value from you: {', '.join(missing)}")
        print("  Edit .env and fill it in.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
