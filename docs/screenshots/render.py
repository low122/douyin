"""Regenerate the screenshots in this folder.

Every image is the real UI over real data — nothing is mocked up, because a
mockup drifts from the product the moment either changes and the difference is
invisible until someone notices the screenshot promises something missing.

    docker compose up -d
    python3 docs/screenshots/render.py

Needs playwright on the system python. Reads pages through the API with the
token from .env, so it must run from the repo root with the stack up.
"""

import asyncio
import os
import subprocess
import tempfile
import urllib.parse
from pathlib import Path

from playwright.async_api import async_playwright

BASE = os.environ.get("KB_BASE", "http://localhost:8000")

# 360x640 at 3x is exactly 1080x1920 — a phone frame. Rendering small and
# scaling up is what keeps the text large enough to read on a phone; a wide
# viewport shrunk to fit produces something technically correct and unwatchable.
VIEWPORT = {"width": 360, "height": 640}
SCALE = 3

PAGES = [
    ("01-搜索结果", "/?q=" + urllib.parse.quote("PDF 解析怎么做")),
    ("02-搜索结果2", "/?q=" + urllib.parse.quote("大模型为什么会胡说")),
    ("03-粘贴添加", "/add"),
    ("04-处理记录", "/admin/jobs"),
]


def _token() -> str:
    for line in Path(".env").read_text().splitlines():
        if line.startswith("API_TOKEN="):
            return line.split("=", 1)[1].strip()
    raise SystemExit("no API_TOKEN in .env")


async def main() -> None:
    token = _token()
    out = Path(__file__).parent

    # Fetched with curl and rendered from disk rather than pointed at the live
    # URL: the browser cannot send the auth header, and typing the token into a
    # page would put it in the browser's history and forms.
    with tempfile.TemporaryDirectory() as tmp:
        files = []
        for name, path in PAGES:
            dest = Path(tmp) / f"{name}.html"
            subprocess.run(
                ["curl", "-sf", "-H", f"Authorization: Bearer {token}", BASE + path,
                 "-o", str(dest)],
                check=True,
            )
            files.append((name, dest))

        async with async_playwright() as p:
            browser = await p.chromium.launch()
            for scheme in ("dark", "light"):
                page = await browser.new_page(
                    viewport=VIEWPORT, device_scale_factor=SCALE, color_scheme=scheme
                )
                for name, src in files:
                    await page.goto(f"file://{src.resolve()}")
                    await page.wait_for_timeout(250)
                    await page.screenshot(path=str(out / f"{scheme}-{name}.png"))
                await page.close()
            await browser.close()

    print(f"wrote {2 * len(PAGES)} images to {out}")


if __name__ == "__main__":
    asyncio.run(main())
