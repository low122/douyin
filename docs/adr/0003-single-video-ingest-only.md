# Ingest accepts one shared video at a time, by design

There is no batch endpoint and no crawler. The system takes a single share link per request, and media files are deleted once their text has been derived — only transcripts, extractions, and the source URL are retained.

The deletion side of this is easy to justify: the asset is the knowledge, not the video, and keeping media would add storage and a volume to every deployment for no gain. The absent batch endpoint is the deliberate part. This repository is public and meant to be self-hosted by others, and a batch interface would make bulk scraping of a third-party platform the path of least resistance — a capability we do not want to have authored.

Expect this to be re-proposed as a convenience feature. It is a scope boundary, not a missing feature.
