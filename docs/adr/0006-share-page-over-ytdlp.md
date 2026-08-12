# Metadata and media come from the share page, not yt-dlp

Ingest fetches `https://www.iesdouyin.com/share/video/{aweme_id}/` and reads the
`_ROUTER_DATA` JSON embedded in the HTML. `yt-dlp` is not a dependency.

`yt-dlp` is the obvious tool and it does ship a Douyin extractor, which is why
this needs recording: on a real link it fails with *"Fresh cookies (not
necessarily logged in) are needed"*, and anonymous cookies cannot be acquired
over plain HTTP — the session cookies come from a JS challenge. The only ways to
satisfy it are browser-sourced cookies or a manually exported cookie file,
neither of which survives into a container that a stranger is meant to run with
one command. The share page has no such requirement and returns everything the
pipeline needs, including a directly fetchable media URL.

The trade is a hand-written parser against an undocumented shape instead of a
maintained extractor: if the page's JSON moves, this breaks and nobody upstream
fixes it. Accepted because the alternative does not work unattended at all, and
because the parse is small and pinned by a fixture. Field-level observations
live in `docs/douyin-platform-notes.md`.

Only `aweme_id` is kept from the resolved URL. The rest of its query string —
`did`, `iid`, `u_code`, `share_sign` — identifies the device that produced the
share, and storing it would put a fingerprint in the database.
