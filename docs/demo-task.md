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

1. **Human-run the completed five-tab Studio using real providers.** Import
   `workspace/temp/scenic-cafe-personal-chowchow-v3/01-spring-sunny.png`,
   approve it, and validate the Sleep/Lookup motion, 1080p upscale, 12 Lyria
   tracks, two-hour mix, and final MP4. Automated tests use synthetic/fake
   providers and do not claim visual or provider success.
2. **Decide whether a remote ComfyUI service is actually needed.** Tab 3 now
   works with the ComfyUI client configured at Studio startup; local MPS is
   the default and `comfyui_remote_base_url` remains intentionally empty.
   A vendor-specific browser token flow is not implemented because no vendor
   or auth protocol has been selected.
3. **Perform browser visual/accessibility QA.** The 2026-09-05 implementation
   environment had no connected in-app/external browser, so API and JavaScript
   syntax are tested but responsive layout and keyboard/screen-reader behavior
   still need a real browser pass.
4. `scripts/verify-local.sh` only checks the working-tree diff for
   whitespace, not the last commit (`git diff --check HEAD^ HEAD` catches
   more, per `codex_reviewer`'s review) — low priority, not yet fixed.

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
