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
| `kind` | TEXT | `keyframe` \| `motion_test` \| `clip` \| `clip_1080p` \| `loop` \| `final` |
| `role` | TEXT | `shared` \| `sleep` \| `lookup` |
| `variant_index` | INTEGER | candidate index (e.g. 4 keyframe candidates) |
| `status` | TEXT | `queued` \| `running` \| `ready` \| `awaiting_review` \| `approved` \| `rejected` \| `superseded` \| `failed` |
| `path` | TEXT | file path once produced |
| `sha256` | TEXT | |
| `width` / `height` | INTEGER | |
| `duration_seconds` / `fps` | REAL | |
| `source_prompt` | TEXT | |
| `source_seed` | INTEGER | |
| `comfyui_prompt_id` | TEXT | ComfyUI's own job id, for provenance |
| `parent_asset_id` | INTEGER, FK -> `episode_assets.id`, nullable | e.g. a `clip_1080p` row points at its `clip` (480p) source |
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
| `task_type` | TEXT | `generate_keyframe` \| `generate_motion_test` \| `generate_clip` \| `upscale_clip` \| `build_loop` \| `render_final` |
| `status` | TEXT | `queued` \| `running` \| `done` \| `failed` |
| `payload_json` | TEXT | task-specific parameters |
| `attempts` | INTEGER | |
| `error` | TEXT | |
| `created_at` / `started_at` / `finished_at` | TEXT | |

Indexes: `idx_episode_assets_episode(episode_id, kind, role)`,
`idx_studio_tasks_status(status, id)`.

**Known gap**: `build_loop` and `render_final` are valid `task_type` values
in the schema's CHECK constraint, but `src/lyria_auto/studio/stages.py` has
no handler for either yet — a task reaching one of these fails loudly with
"no handler registered" by design (see `docs/architecture/studio-architecture-plan.md`
stages 4/5). This is the next real implementation gap, not a bug.

## Workflow assets referenced by `episode_assets.source_prompt` / stage code

Read by path from `comfyui-assets/` (see `config/settings.yaml`'s `studio:`
block for `comfyui_workflows_dir` / `chowchow_reference_path`):

- `comfyui-assets/workflows/api/cafe-keyframe-flux-chowchow-lora-mps.json` — keyframe generation using the trained `chowchow_mascot` character LoRA (`chowchow-identity-v1.safetensors`, **does not exist yet** — LoRA training incomplete, see `comfyui-assets/kaggle_upload/kernel_chowchow_lora/`).
- `comfyui-assets/workflows/api/cafe-flf2v-wan22-mps.json` — first/last-frame motion generation (sleep/lookup clips).
- `comfyui-assets/workflows/api/cafe-upscale-1080p-mps.json` — post-process ESRGAN upscale, local, no native high-res diffusion.
- `comfyui-assets/chowchow-prompts.md` — the prompt library keyed to the fixed identity description + `chowchow_mascot` trigger word.

Do not put production data, real credentials, or unredacted personal media
into this file. The real chow-chow reference photos/videos live under
`comfyui-assets/character-reference/chowchow/{source,working}/` and are
gitignored (only `approved/` and `.gitkeep` markers are tracked).
