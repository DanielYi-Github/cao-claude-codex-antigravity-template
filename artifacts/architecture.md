# Five-tab Studio architecture decisions

## Runtime shape

The Studio remains one local FastAPI process plus one serialized background worker over SQLite. The browser controls approval state; ComfyUI and ffmpeg stages run in the worker. Lyria generation is the deliberate exception: it runs synchronously inside the initiating request so the supplied Gemini API key never enters a queued payload or outlives that request.

## Pipeline

```text
keyframe -> sleep/lookup motion tests -> 64 s preview
         -> production clips -> 1080p clips -> approved loop
         -> 12 reviewed music tracks -> FLAC duration-floor mix
         -> final MP4 -> approval/download -> optional YouTube upload
```

Every transition that can spend compute or publish content has an explicit human action. Approving the cheap preview does not start production; YouTube upload defaults to private and requires both a checked confirmation and a second browser confirmation.

## Security and persistence

- Imported keyframes must resolve inside the configured project workspace and must decode as PNG, JPEG, or WebP.
- Episode slugs are restricted before they can become filesystem path components.
- Gemini API keys use request `SecretStr` values, are redacted from surfaced failures, and are never stored in SQLite, task JSON, configuration, or metadata.
- Production ComfyUI credentials are configured outside the browser when the process starts. A vendor-specific browser credential flow is intentionally deferred.
- Media, provider outputs, OAuth files, and other secrets remain runtime files and are not committed.

## Media decisions

Twelve tracks form one coherent album. Mixing and whole-album extension use FLAC; the configured target duration is a lower bound, so the last track is never cut merely to hit an exact timestamp. The final mux copies the reviewed video stream and encodes audio to AAC. YouTube metadata is a deterministic editable English draft and always declares synthetic media through the provider model.

## Scaling boundary

The design is verified for a single Studio process and worker. The paid music entry point has an atomic SQLite in-flight guard, but full multi-process operation requires additional uniqueness/reservation constraints for older check-then-enqueue routes.

Detailed rationale and historical phase design are in `docs/architecture/studio-console-v2-plan.md`; acceptance criteria are in `artifacts/spec.md`.
