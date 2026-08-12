# The iOS Shortcut

Douyin is a phone app, so the thing that saves a video has to live on the phone.
This is the entry point the whole design assumes — see
[ADR-0003](adr/0003-single-video-ingest-only.md) for why one link at a time.

What you end up with: watch something worth keeping, tap share, tap the
shortcut, done. Processing happens on the server and takes a few minutes; you do
not wait for it.

## Before you start

The phone has to be able to reach the API. Two cases:

- **Same Wi-Fi as the machine running it.** Use the machine's LAN address —
  `ipconfig getifaddr en0` on macOS, `hostname -I` on Linux. Nothing else to set
  up. Note that the token then travels the local network in the clear; on a home
  network that is a reasonable trade, on a café network it is not.
- **Anywhere else.** The LAN address is meaningless outside the network, so the
  API needs a public address — a tunnel or a deployment. Not covered here.

Get your token from `.env`:

```bash
grep '^API_TOKEN=' .env | cut -d= -f2-
```

## Build it

In the Shortcuts app, tap **+**, then:

1. Add action → search **Get Contents of URL**.
2. **URL**: `http://YOUR-LAN-IP:8000/ingest`
3. Tap the **▸** to expand the action.
4. **Method**: `POST`
5. **Headers** → Add header
   - Key: `Authorization`
   - Value: `Bearer YOUR-TOKEN` — the word `Bearer`, a space, then the token.
6. **Request Body**: `JSON` → Add new field
   - Type: **Text**
   - Key: `text`
   - Value: tap the field, then pick **Shortcut Input** from the variable bar.

   The key must be exactly `text`. Anything else returns 422, because the
   request model names that field and nothing else.

Then open the shortcut's settings (the **ⓘ** or the name at the top):

7. Turn on **Show in Share Sheet**.
8. Under accepted input, keep **Text** and **URLs**, turn the rest off. Douyin
   hands over a blob of text with the link inside it; different iOS versions
   present it as one or the other, and accepting both means you never have to
   care which.
9. Rename it to whatever you want to see in the share sheet.

Optional but worth it — add a second action after the first:

10. **Show Notification**, with `Contents of URL` as the body. A successful save
    returns `{"job_id": 12, "status": "queued"}`. Without this the shortcut
    succeeds and fails identically: silently.

## Use it

In Douyin: **share → more → your shortcut**. That is the whole loop.

To search what you have saved, open the API's address in Safari on the phone and
sign in once with the same token. The cookie lasts 30 days, so it is one
sign-in, not one per search. Add it to the home screen and it behaves like an
app.

## What the responses mean

| Response | Meaning |
| --- | --- |
| `202` `{"job_id": …}` | Accepted and queued. Processing has not happened yet. |
| `400` `no Douyin link found` | The shared text had no link in it. |
| `401` | The token is wrong or the header is malformed. |
| `422` | The JSON field is not named `text`. |
| No response at all | The phone cannot reach the machine — asleep, different Wi-Fi, or the stack is not running. |

A `202` means queued, not finished. Whether the work actually succeeded is on
the `/admin/jobs` page, which is exactly why that page exists: a job that fails
at 3am is otherwise never noticed.

## Do not share the shortcut

An iCloud shortcut link exports every action, including the header you just put
the token into. Sending someone "the shortcut" sends them your credential and
your address. Send them this page instead.
