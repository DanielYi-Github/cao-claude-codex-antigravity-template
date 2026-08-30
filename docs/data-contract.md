# Data Contract

SQLite database at `workspace/state.sqlite3` (`StateDB`, `src/lyria_auto/db.py`).
Tables `jobs`, `tracks`, `events` are pre-existing (music generation) and
unchanged by the chow-chow studio work. New tables added for the studio
pipeline (already implemented in `db.py`'s `SCHEMA`, not a to-do):

## `episodes`

One row per two-hour video project.

| Column | Type | Meaning |
|---|---|---|
| `id` | INTEGER PK | |
| `slug` | TEXT, unique | URL-safe episode identifier |
| `title` | TEXT | |
| `status` | TEXT | `draft` \| `in_progress` \| `complete` \| `archived` |
| `music_job_id` | INTEGER, FK -> `jobs.id`, nullable | |
| `state_version` | INTEGER | optimistic-lock counter |
| `created_at` / `updated_at` | TEXT (ISO 8601) | |

## `episode_assets`

Every generated artifact, with version and lineage.

| Column | Type | Meaning |
|---|---|---|
| `id` | INTEGER PK | |
| `episode_id` | INTEGER, FK -> `episodes.id` | |
| `kind` | TEXT | `keyframe` \| `motion_test` \| `clip` \| `clip_1080p` \| `loop_preview` \| `loop` \| `music_track` \| `music_mix` \| `final` |
| `role` | TEXT | `shared` \| `sleep` \| `lookup` |
| `variant_index` | INTEGER | candidate index (e.g. 12 keyframe candidates, or 0-11 for the 12 `music_track` rows) |
| `status` | TEXT | `queued` \| `running` \| `ready` \| `awaiting_review` \| `approved` \| `rejected` \| `superseded` \| `failed` |
| `path` | TEXT | file path once produced |
| `sha256` | TEXT | |
| `width` / `height` | INTEGER | |
| `duration_seconds` / `fps` | REAL | |
| `source_prompt` | TEXT | |
| `source_seed` | INTEGER | |
| `comfyui_prompt_id` | TEXT | ComfyUI's own job id, for provenance |
| `parent_asset_id` | INTEGER, FK -> `episode_assets.id`, nullable | e.g. a `clip_1080p` row points at its `clip` (480p) source |
| `track_id` | INTEGER, FK -> `tracks.id`, nullable | required (CHECK-enforced) when `kind='music_track'`; links to the classic pipeline's `tracks` table row |
| `error` | TEXT | |
| `state_version` | INTEGER | optimistic-lock counter |
| `created_at` / `updated_at` | TEXT | |

## `studio_tasks`

Background worker queue.

| Column | Type | Meaning |
|---|---|---|
| `id` | INTEGER PK | |
| `episode_id` | INTEGER, FK -> `episodes.id` | |
| `asset_id` | INTEGER, FK -> `episode_assets.id`, nullable | |
| `task_type` | TEXT | `generate_keyframe` \| `generate_motion_test` \| `generate_clip` \| `upscale_clip` \| `build_loop_preview` \| `build_loop` \| `generate_music_tracks` \| `build_music_mix` \| `render_final` |
| `status` | TEXT | `queued` \| `running` \| `done` \| `failed` |
| `payload_json` | TEXT | task-specific parameters |
| `attempts` | INTEGER | |
| `error` | TEXT | |
| `created_at` / `started_at` / `finished_at` | TEXT | |

Indexes: `idx_episode_assets_episode(episode_id, kind, role)`,
`idx_studio_tasks_status(status, id)`.

**Migrations**: `0002_studio_foundation` created these three tables with a
narrower set of `kind`/`task_type` values. `0003_studio_production`
(`docs/architecture/studio-console-v2-plan.md`) widened both CHECK
constraints and added `episode_assets.track_id` — SQLite can't `ALTER` a
CHECK constraint, so this is a rebuild-and-copy migration, not an in-place
edit of `STUDIO_SCHEMA_SQL`. Both migrations are checksum-tracked in
`schema_migrations`; do not edit either SQL string in place — add a new
migration id instead, the same way `0003` was added on top of `0002`.

**Status as of `0003` + Phase 2**: `generate_keyframe`, `generate_motion_test`,
`generate_clip`, `upscale_clip`, `build_loop_preview`, `build_loop`, and
`render_final` all have working handlers in `src/lyria_auto/studio/
stages.py`. `generate_music_tracks` and `build_music_mix` are still
schema-only — no handler registered yet (planned for `studio-console-v2-
plan.md` Phase 4). `episodes.music_job_id` is likewise schema-only: no
code currently reads or writes it (Phase 4).

**`generate_keyframe` (Phase 1, studio-console-v2-plan.md tab 1)**: unlike
every other stage, this task carries no `asset_id` — it fans one ComfyUI
batch submission out into N `episode_assets` rows (`variant_index`
monotonically increasing per episode across every batch, including
superseded ones, never reset to 0 — see `StateDB.next_variant_index`) rather
than filling in a single pre-created placeholder. The prompt/negative-prompt/
batch-size come from the task's `payload_json`, not from an asset's
`source_prompt`, since no asset exists yet when the task is enqueued.
Default batch size is `config/settings.yaml`'s `studio.keyframe_batch_size`
(currently 12, **untested against real ComfyUI hardware** — the checked-in
workflow's own prior default was 4, and larger batches at 1280×720 are
memory/time-unverified). `POST /api/episodes/{id}/keyframes/generate`
(`src/lyria_auto/studio/app.py`) is the only way to enqueue it — both the
initial "Generate" and "regenerate all" tab-1 actions call this same
endpoint, which supersedes any still-`awaiting_review` candidates from a
prior call first. `episode_assets.kind='keyframe'` does not support
per-asset `POST .../reject` (400) — there's no "replace candidate #7" in a
12-up grid; approving one candidate supersedes the rest instead
(`app.py`'s `_continue_after_approval`). `create_episode` no longer
auto-enqueues a keyframe task the way it did before `0003` — an episode
starts with zero assets until tab 1's Generate is called. The generate
endpoint returns 409 (does not supersede/enqueue anything) if a keyframe is
already `approved` for the episode, or if a `generate_keyframe` task is
already `queued`/`running` — these guard the two races codex_reviewer's
Phase 1 review found (double-approval, and a stale in-flight batch
publishing after a newer regenerate already superseded it).
`enqueue_task()` raises `ValueError` for a non-null `asset_id` on
`generate_keyframe` — that task type creates its own batch of rows and has
no single placeholder to fill in (this guard only covers future calls; the
Phase 0→1 migration itself never produced any legacy rows shaped that way —
verified against the live database, zero rows — so no backfill was needed).
`generate_keyframe`'s handler downloads and validates every image in a
batch before creating any `episode_assets` row, so a mid-batch
download/decode failure never leaves a partial batch sitting at
`awaiting_review` next to a task marked `failed`. `POST
.../assets/{id}/approve` also 409s for `kind='keyframe'` while the
episode's `generate_keyframe` task is still `queued`/`running` — the
handler publishes its batch one row at a time, so without this guard an
early candidate could be approved (fanning out motion-test tasks) while
its siblings were still being written. `StudioWorker.start()` now calls
`recover_stale_tasks()` first, failing any task still `running` from a
previous process — otherwise a crash mid-generation (e.g. a ComfyUI OOM on
an untested `keyframe_batch_size`) would leave that episode permanently
409ing both the regenerate and approve guards with no way to recover.

**`build_loop_preview`/`build_loop` (Phase 2, studio-console-v2-plan.md tab
2)**: one handler (`stages.py`'s `_build_loop_variant`) backs both task
types — `build_loop_preview` concatenates two approved `motion_test`
assets (768x432) into a `loop_preview` asset, `build_loop` does the same
from `clip_1080p` (1920x1080) into `loop`; both stay 7×sleep+1×lookup at
8s/segment, 64s total. `POST /api/episodes/{id}/motion/assemble-preview`
(tab 2's "Assemble 64s Preview" button) is the only way to enqueue
`build_loop_preview` — it resolves whichever `motion_test` assets are
approved *at request time* and writes their ids into the task's
`payload_json.source_asset_ids`, and the handler re-validates against
exactly those ids (still approved, right kind, right episode) instead of
re-resolving "whatever's newest approved" when it actually runs — a build
always matches what the reviewer saw when they clicked, not whatever
happened to be approved by the time the worker got to it (codex_reviewer
design consult, `studio-console-v2-plan.md` 7.8). `build_loop` keeps
falling back to the latest-approved lookup when no payload is given, since
its only current trigger — `clip_1080p`'s fan-in in `app.py`'s
`_continue_after_approval` — still enqueues it with none. The assemble
endpoint 409s under the same two conditions as keyframe's
generate/regenerate (a `loop_preview` already `approved`; a
`build_loop_preview` task already `queued`/`running`).

Approving a `motion_test` asset no longer auto-advances to `clip`
generation — `_NEXT_KIND` in `app.py` dropped that entry. Reason: `clip`
and `upscale_clip` share one remote ComfyUI client (`stages.py`'s
`clip_comfyui`), and tab 3 (not built yet) is what will collect the cloud
credential that client needs — auto-advancing on a tab-2 approval would
fire a request needing a token before the reviewer ever reaches tab 3.
Approving a `loop_preview` asset is correspondingly a no-op in
`_continue_after_approval` for now (explicitly, not a silent fallthrough):
it only unlocks tab 3's UI; enqueuing `generate_clip` for both roles is
deferred to tab 3's own "start cloud processing" action once Phase 3 adds
it. `enqueue_task()`'s asset-id guard (previously `generate_keyframe`-only)
now covers `build_loop_preview` and `build_loop` too, via
`_SELF_CREATING_TASK_TYPES` — same reasoning, both create their own asset
row(s) and ignore any `asset_id` they'd be given. `POST
.../assets/{id}/reject`'s 400 (previously `keyframe`-only, via
`_NO_PER_ASSET_REJECT`) now also covers `loop_preview`, `loop`, and
`final` — all three share the same "handler self-creates, ignores
asset_id" shape (this closes a latent bug in the pre-existing `loop`/
`final` paths too, not just the new `loop_preview`, per codex_reviewer's
design consult).

**Known limitation, not yet fixed**: `StateDB` shares one `sqlite3.Connection`
(`check_same_thread=False`) between FastAPI's request-handling threads and
the background `StudioWorker` thread with no locking — codex_reviewer
reproduced `InterfaceError` and dozens of failures under a concurrent-
request stress test, and separately reproduced the `POST
.../keyframes/generate` 409 guards themselves being non-atomic (two
concurrent requests can both pass the check before either supersedes/
enqueues, since the check and the act are separate statements, not one
transaction). Both are the same root cause: no synchronization or explicit
transaction boundary across a check-then-act sequence spanning multiple
`StateDB` calls. This predates Phase 1 and applies to every stage, not
just keyframes; fixing it (a lock around every `StateDB` method, per-thread
connections, or `BEGIN IMMEDIATE`-wrapped check+act operations) is its own
piece of work, tracked in `docs/architecture/studio-console-v2-plan.md`,
not done here — the current guards are correct for a single browser tab
clicking sequentially, which is the actual deployment, not for concurrent
requests. `StateDB.next_variant_index()` is similarly not atomic across
independent connections — safe only because exactly one `StudioWorker`
process runs today (see its docstring and `worker.py`'s module docstring).

## Workflow assets referenced by `episode_assets.source_prompt` / stage code

Read by path from `comfyui-assets/` (see `config/settings.yaml`'s `studio:`
block for `comfyui_workflows_dir` / `chowchow_reference_path`):

- `comfyui-assets/workflows/api/cafe-keyframe-flux-chowchow-lora-mps.json` — keyframe generation using the trained `chowchow_mascot` character LoRA (`chowchow-identity-v2.safetensors`, trained on `comfyui-assets/kaggle_upload/kernel_chowchow_lora/`'s 39-photo v2 dataset; identity fidelity is still under active tuning as of 2026-08-30, see `artifacts/ui-notes.md`'s visual QA notes).
- `comfyui-assets/workflows/api/cafe-inpaint-lookup-flux-fill-mps.json` — manual local-touchup/inpaint pass for a specific keyframe candidate, not yet wired into `stages.py`.
- `comfyui-assets/workflows/api/cafe-flf2v-wan22-mps.json` — first/last-frame motion generation (sleep/lookup clips).
- `comfyui-assets/workflows/api/cafe-upscale-1080p-mps.json` — post-process ESRGAN upscale, local, no native high-res diffusion.
- `comfyui-assets/chowchow-prompts.md` — the prompt library keyed to the fixed identity description + `chowchow_mascot` trigger word.

Do not put production data, real credentials, or unredacted personal media
into this file. The real chow-chow reference photos/videos live under
`comfyui-assets/character-reference/chowchow/{source,working}/` and are
gitignored (only `approved/` and `.gitkeep` markers are tracked).
