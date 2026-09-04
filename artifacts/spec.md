# Five-tab Chow Chow ambience Studio specification

## Objective

Provide one local FastAPI web console that can take a selected scenic Chow Chow keyframe through motion review, production/upscale, a 12-track original jazz album, a two-hour-or-longer final render, download, and optional YouTube upload.

## Acceptance criteria

- Tab 1 can generate candidates or safely import a PNG/JPEG/WebP from the configured project `workspace`; approving one keyframe starts the two motion tests.
- Tab 2 independently reviews Sleep and Lookup, then builds and reviews a 7×Sleep + 1×Lookup 64-second preview.
- Tab 3 has an explicit compute/possible-cost confirmation before creating production clips, reviews both 1024×576 clips and 1080p upscales, and builds the approved 1080p loop.
- Tab 4 accepts an editable album prompt and request-scoped Gemini API key, generates exactly 12 ordered tracks with per-track persistence/review/regeneration, and builds a crossfaded lossless mix at least as long as `video.target_duration_minutes`.
- Tab 5 renders the approved loop against the approved mix, supports HTTP Range preview and MP4 download, provides editable English metadata with synthetic-media disclosure, and never uploads without a checkbox plus a second confirmation.
- Secrets never enter SQLite, task payloads, committed config, generated metadata, or surfaced provider errors.

## Durable decisions

- The user-selected starting image is `workspace/temp/scenic-cafe-personal-chowchow-v3/01-spring-sunny.png`; the UI accepts its absolute path rather than copying generated media into Git.
- The configured duration is a lower bound. Complete album cycles are preserved, so the output may be longer than 120 minutes but will not cut a track in half.
- Intermediate album mixing/extension uses FLAC; AAC is encoded only at final mux after the already-normalized generated tracks.
- Lyria calls are synchronous and request-scoped so an entered API key does not outlive the request. A running `studio_tasks` row with no payload provides progress and an atomic duplicate-spend guard.
- Production ComfyUI uses the client configured when Studio starts. No remote URL or token is written by the browser; local MPS remains the default.
- YouTube metadata is a deterministic, editable English draft rather than an additional Gemini call. Upload uses the existing OAuth channel configuration and defaults to private.

## Out of scope / human gates

- No real ComfyUI, Lyria, remote GPU, or YouTube operation is run as part of automated verification.
- Visual candidate quality, 1080p flicker/seams, final two-hour playback, OAuth channel identity, and provider billing require explicit human validation.
- General multi-process locking for every older Studio check-then-act path remains Phase 6; the paid music start path has its own narrow atomic guard.
