# Douyin Platform Notes

Measured against three real videos on 2026-08-10, not inferred from documentation.
Everything here is the kind of thing that costs an afternoon to rediscover.

## Share text

What the app copies is not a URL. It is a blob with a numeric prefix, several
obfuscation tokens, the caption, the link, and a trailing instruction:

```
2.51 03/15 :3pm a@A.go jpQ:/ <caption> https://v.douyin.com/8IC3BBBzMAY/ 复制此链接...
9.41 h@b.aN 09/30 :0pm YZM:/ <caption> https://v.douyin.com/OXnlTiRkdXw/ 复制此链接...
7.61 YzT:/ :5pm 11/17 G@I.IV <caption> https://v.douyin.com/aBf60JvMaMU/ 复制此链接...
```

**The obfuscation tokens appear in a different order every time** — compare the
three samples above. Any parser that keys off position will work on the sample
you tested and fail on the next one. Match the URL and discard everything else:

```
https?://v\.douyin\.com/[A-Za-z0-9]+
```

Do not pin the short code's length; treat it as opaque.

## Resolving the short link

A `HEAD` with redirects followed lands on:

```
https://www.iesdouyin.com/share/video/{aweme_id}/?region=…&u_code=…&did=…&iid=…&share_sign=…
```

`aweme_id` is a 19-digit numeric id and is the only part worth keeping.

> ⚠️ **The query string identifies the person who shared it.** `did` and `iid`
> are device and install identifiers and were byte-identical across all three
> links from the same phone; `u_code` is the sharer's code. Persist the
> canonical `https://www.douyin.com/video/{aweme_id}` and drop the rest — a
> stored resolved URL is a device fingerprint sitting in the database.

## Metadata: use the share page, not yt-dlp

`yt-dlp` has a `Douyin` extractor and it is not marked broken, but on a real
link it fails:

```
ERROR: [Douyin] …: Fresh cookies (not necessarily logged in) are needed
```

Anonymous cookies cannot be obtained with plain HTTP — a bare request to
`douyin.com` yields only `__ac_nonce`; the session cookies come from a JS
challenge. That leaves browser-sourced cookies, which do not survive into a
container.

**The share page needs no cookies at all.** `GET`
`https://www.iesdouyin.com/share/video/{aweme_id}/` with a mobile user agent
returns ~38 KB of HTML with a JSON blob:

```
_ROUTER_DATA = { loaderData: { "video_(id)/page": { videoInfoRes: { item_list: [ … ] } } } }
```

One item, carrying `aweme_id`, `desc`, `author.nickname`, `video.duration` (ms),
`statistics`, and `video.play_addr.url_list`.

Fields that sound useful and are **empty on all three samples** — do not build
on them:

| Field | Observed |
|---|---|
| `video_text` | `null` — no captions come for free |
| `chapter_list` | `null` — no chapters either |
| `aweme_type` | `4` on all three (not the `0`/`68` that circulating notes claim) |

The empty `video_text` is what makes reading the frames non-optional: for a
slide-driven video, the substance is on screen and nowhere else.

### Throttling looks almost exactly like a schema change

Request the share page too often and Douyin keeps serving 200s with the shell
intact — `_ROUTER_DATA`, `loaderData`, and the page object all present — and
simply omits `videoInfoRes`. The response is a few kilobytes smaller, gains
`serverToken`, `abParams` and `isSpider`, and mentions `captcha`.

A parser that only checks for `videoInfoRes` reports this as "the JSON moved",
which is the opposite of the truth and the more damaging mistake of the two: a
stale parser is permanent and must not be retried, throttling clears on its own
and must be. The distinguishing rule:

| Observed | Meaning |
|---|---|
| No `_ROUTER_DATA`, or `loaderData` shaped differently | Parser is stale — permanent |
| Shell intact, `videoInfoRes` absent | Throttled — retry later |
| `videoInfoRes` present but malformed | Parser is stale — permanent |

Triggered here by a few dozen requests in an afternoon of testing. Ordinary use
— a handful of shared videos a day — is nowhere near it, but a batch backfill or
a tight test loop will find it quickly.

## Downloading

`play_addr.url_list[0]` is directly fetchable — no cookies, no signing. It
serves `video/mp4`, honours range requests (`HTTP 206`), and the payload is a
normal H.264/AAC MP4. One 315-second 720p video was 31 MB in 50 s.

The URL path contains `playwm`: the file is watermarked. Irrelevant for
transcription and frame reading.

A partial download will not open in `ffprobe` — MP4 keeps its index at the end
of the file. That is not a broken download.

## Keyframe selection — the part that is easy to get wrong

`select='gt(scene,T)'` on a 315 s slide video:

| Threshold | Frames |
|---|---|
| 0.4 | 12 |
| 0.3 | 19 |
| 0.2 | 39 |
| 0.1 | 126 |
| 0.02 | 662 |

0.3 looks right, and the frames it produces are **useless**. Scene detection
fires on the moment of maximum change, which in an animated slide deck is the
transition — the card mid-flight, half off-frame, or the background between two
slides. What you want is the moment the animation has settled, which is
precisely where the scene score is lowest.

Sampling a fixed 1.5 s after each detected change fixes most of it: the same
timestamp that produced a motion-blurred card produced a legible slide. It is
not enough for every transition — one frame at +1.5 s still caught body text
mid-typewriter, doubled and unreadable — because a fixed offset assumes every
animation runs for the same length of time.

### Frame count adapts to the format, for free

Same threshold, three real videos:

| Video | Length | Format | Frames at `scene>0.3` |
|---|---|---|---|
| 水球泡 | 538 s | talking-head podcast | **1** |
| AI大模型日记 | 380 s | animated slides | 21 |
| AI大模型日记 | 315 s | animated slides | 19 |

Slide-driven videos land near one frame per 18 s; a static talking head produces
almost nothing. Vision spend therefore tracks how much is actually on screen
without any classification step — a talking head costs nothing extra precisely
because there is nothing extra to read.

> ⚠️ **Unverified risk.** Nine minutes yielding a single frame is the desired
> behaviour *if* that video genuinely has no visual content. If a mostly-static
> video cuts to a chart for twenty seconds, threshold 0.3 may well miss it, and
> nothing in the pipeline would report the omission. Whether 0.3 is too coarse
> is a question for the eval set, not for intuition.

**What the pipeline does instead:** scene detection supplies *boundaries only*;
frames come from the midpoint of each stable span, where nothing is moving by
construction. Boundaries closer than ~2 s are merged first — the run above
produced pairs at 16.60/16.63 and 24.90/25.03, which yield near-identical frames
and pay for the same vision tokens twice. When the count exceeds the budget the
longest spans win, on the reasoning that a slide held for twenty seconds is
likelier to carry the substance than one that flashes past.

Measured through the implementation: 19 boundaries became 16 frames on the
315-second video, 21 became 20 on the 380-second one. The frame covering the
densest slide came out with both the info card and the body text fully settled —
where the fixed-offset version had caught that same text mid-animation. Midpoint
sampling is not a refinement of the offset approach; it removes the assumption
the offset was built on.

> `showinfo` logs at info level. Running it under `ffmpeg -v error` silently
> prints nothing and looks exactly like "no frames detected".

## Burned-in subtitles

Every sampled frame carries the creator's burned-in subtitle line, which repeats
what the audio already said. Frame text and transcript therefore overlap by
construction, and the extraction step has to reconcile them rather than treat
them as two independent sources.
