"""Downloading a video once and deriving everything from it.

Media is a means, not an asset: it exists long enough to produce audio and
frames, and is then gone (ADR-0003). The whole operation runs inside a temporary
directory so success, failure, and cancellation all clean up identically — the
failure path is exactly where leaked files accumulate until a disk fills.

Transcription and frame reading share one download. Deriving them separately
would fetch the same 30-plus megabytes twice.
"""

import asyncio
import logging
import tempfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

import httpx

log = logging.getLogger(__name__)

# Streamed rather than buffered: a nine-minute video is around 50 MB and has no
# reason to pass through memory.
CHUNK_BYTES = 1 << 16

# Well past any plausible short-form video; a runaway response should not fill
# the disk before anything notices.
MAX_MEDIA_BYTES = 512 * 1024 * 1024


class MediaError(Exception):
    """Downloading or converting failed."""


@dataclass(frozen=True)
class PreparedMedia:
    """Valid only inside the context manager that produced it."""

    # The download is still needed — the audio is extracted out of it — but
    # nothing outside this module reads the video any more. Kept private so the
    # next caller does not rediscover it as an interface.
    _video_path: Path
    audio_path: Path
    workdir: Path


async def _download(client: httpx.AsyncClient, url: str, target: Path) -> int:
    written = 0
    try:
        async with client.stream("GET", url, follow_redirects=True) as response:
            if response.status_code >= 400:
                raise MediaError(f"media download returned {response.status_code}")
            with target.open("wb") as handle:
                async for chunk in response.aiter_bytes(CHUNK_BYTES):
                    written += len(chunk)
                    if written > MAX_MEDIA_BYTES:
                        raise MediaError(f"media exceeded {MAX_MEDIA_BYTES} bytes")
                    handle.write(chunk)
    except httpx.HTTPError as exc:
        raise ConnectionError(f"media download failed: {exc}") from exc

    if written == 0:
        raise MediaError("media download produced an empty file")
    return written


async def _extract_audio(source: Path, target: Path) -> None:
    """Mono 16 kHz MP3 — what speech recognition wants, at a fraction of the
    original size. A 315-second video yields about 1.5 MB, comfortably under the
    transcription endpoint's 25 MB limit."""
    process = await asyncio.create_subprocess_exec(
        "ffmpeg",
        "-v", "error",
        "-y",
        "-i", str(source),
        "-vn",            # drop the video stream
        "-ac", "1",       # mono
        "-ar", "16000",   # 16 kHz
        "-c:a", "libmp3lame",
        "-q:a", "6",
        str(target),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await process.communicate()
    if process.returncode != 0:
        raise MediaError(f"ffmpeg failed ({process.returncode}): {stderr.decode()[:500]}")
    if not target.exists() or target.stat().st_size == 0:
        raise MediaError("ffmpeg produced no audio")


@asynccontextmanager
async def prepared_media(client: httpx.AsyncClient, media_url: str) -> AsyncIterator[PreparedMedia]:
    """Yield the downloaded video and its audio, then delete everything.

    There is no exit path — including an exception mid-transcription — that
    leaves a file behind.
    """
    with tempfile.TemporaryDirectory(prefix="douyin-") as tmpdir:
        root = Path(tmpdir)
        video_path = root / "source.mp4"
        audio_path = root / "audio.mp3"

        size = await _download(client, media_url, video_path)
        await _extract_audio(video_path, audio_path)
        log.info(
            "prepared %.1f MB video -> %.1f MB audio",
            size / 1_048_576,
            audio_path.stat().st_size / 1_048_576,
        )

        yield PreparedMedia(_video_path=video_path, audio_path=audio_path, workdir=root)
