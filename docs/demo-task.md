# Task Brief

## Status

The round described below (close the code-level gaps left after merging
`Lyria-Auto-Publisher` + `comfyui-cafe-loop-generator` into this repo) is
**done** — see `artifacts/test-report.md` for what `codex_reviewer` and
`agy_ui_data` found and what was fixed in response (all 4 blocking findings
closed, 199 tests passing). The acceptance criteria below are kept as a
record; the "What to build next" section is the live brief for future runs.

## What to build (this round — complete)

1. ✅ `comfyui-assets/scripts/validate_project.py` now covers
   `workflows/api/*.json` and both chow-chow workflows, plus a UI/API drift
   check.
2. ✅ `src/lyria_auto/studio/stages.py` implements `build_loop` and
   `render_final` (concat-demuxer, `-c copy`), with tests in
   `tests/test_studio_stages.py`.
3. ✅ `chowchow-integration-plan.md` Part A1's prerequisite fixes confirmed
   present by `codex_reviewer`.
4. ✅ `comfyui-assets/README.md`'s directory listing updated.
5. ✅ `./scripts/verify-local.sh` passes.

## What to build next

1. **Get `chowchow-identity-v1.safetensors` trained.** The LoRA training
   kernel (`comfyui-assets/kaggle_upload/kernel_chowchow_lora/`) has its
   known idempotency bug fixed, but the training run itself still needs a
   human to trigger it on Kaggle and confirm it produces a usable file — no
   agent can do this (no GPU access).
2. **Real ComfyUI generation** (keyframe candidates, motion tests, official
   clips, upscales) through the six review gates — needs a human at the
   machine running `lyria-auto studio`. Not delegable.
3. Once real assets exist: wire `render_final`'s `payload_json.audio_path`
   to an actual finished music track from the `tracks`/`jobs` tables (this
   handler currently expects the caller to supply the path explicitly —
   deciding how the studio pipeline picks *which* finished track to use is
   unresolved, see `src/lyria_auto/studio/stages.py`'s `render_final`
   docstring).
4. `scripts/verify-local.sh` only checks the working-tree diff for
   whitespace, not the last commit (`git diff --check HEAD^ HEAD` catches
   more, per `codex_reviewer`'s review) — low priority, not yet fixed.
5. Product decision needed from the human, not an agent task: whether to
   actually rent a remote ComfyUI instance (`config/settings.yaml`'s
   `studio.comfyui_remote_base_url`, already wired in code but unset).

## Constraints

- Never commit, move, or reference the contents of
  `comfyui-assets/character-reference/chowchow/source/` or `.../working/`
  (real photos/videos of the user's pet) — these are gitignored on purpose
  and must stay that way. `approved/` reference images are fine to use.
- No production data, real credentials, or API keys in any artifact or code.
- No new runtime dependencies without human approval.
- Do not populate `config/settings.yaml`'s `studio.comfyui_remote_base_url`
  or otherwise wire in a paid remote ComfyUI instance without explicit
  human sign-off.
- Actual ComfyUI generation and the Kaggle LoRA training run require a
  human at the machine; do not attempt to simulate or fake a successful run
  of either — use the existing fake-provider test pattern instead.

## Verify command

```bash
./scripts/verify-local.sh
```

## Expected handoffs

- `codex_reviewer` reviews the working tree or a specific commit against
  this file and `AGENTS.md`, returns structured findings inline.
- `agy_ui_data` investigates UI/data questions as they come up (no
  `cao-mcp-server` for this profile — local synthetic fixtures only, per
  `docs/antigravity-mcp.md`), returns `artifacts/ui-notes.md`.
- `claude_lead` implements, integrates both workers' findings, runs
  `./scripts/verify-local.sh`, updates `artifacts/test-report.md`.
