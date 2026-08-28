# Task Brief

## What to build

Close the code-level gaps left after merging the chow-chow "music companion"
video pipeline (formerly two standalone repos, `Lyria-Auto-Publisher` and
`comfyui-cafe-loop-generator`) into this repo. The full product goal and
architecture are recorded in `artifacts/spec.md` and
`docs/architecture/studio-architecture-plan.md` — read those first. This
task covers the items that do **not** require a human physically operating
ComfyUI or Kaggle; the actual image/video generation and human review gates
are out of scope here and remain manual.

## Acceptance criteria

1. `comfyui-assets/scripts/validate_project.py` gains checks that reference
   `workflows/api/*.json` and both chow-chow workflows
   (`cafe-keyframe-flux-chowchow-lora-mps.json`,
   `cafe-keyframe-flux-chowchow-composite-mps.json`) — currently it has zero
   coverage of these files. `chowchow-integration-plan.md` Part A2 sketches
   the manifest/arithmetic checks to add; the "manifest" itself has since
   been superseded by the `episodes`/`episode_assets` DB tables (see
   `docs/data-contract.md`), so validate against those where the two
   disagree.
2. `src/lyria_auto/studio/stages.py` implements handlers for the
   `build_loop` and `render_final` task types (currently unimplemented,
   fails loudly by design). `build_loop` assembles the 64s macro-loop
   (7x sleep + 1x lookup clip) from approved `clip_1080p` assets.
   `render_final` repeats that loop to match the music track's length using
   the concat-demuxer + `-c:v copy` approach documented in
   `docs/architecture/studio-architecture-plan.md` (取捨 3) — not a full
   re-encode. Cover both with tests using the existing fake-ComfyUI-client
   pattern (`tests/test_studio_stages.py`).
3. Confirm whether `chowchow-integration-plan.md` Part A1's prerequisite
   fixes (`validate_project.py`'s stale checks,
   `staged_workflow_server.py`'s `workflow_for()` node-deletion assumption,
   `console/local/`'s dangling `onclick` on a removed button) are actually
   present in the merged tree or still outstanding, and close any that
   remain.
4. `comfyui-assets/README.md`'s directory-structure section is updated to
   mention `workflows/api/` and the chow-chow workflow files (currently
   stale, lists only the pre-chow-chow files).
5. `./scripts/verify-local.sh` passes (pytest, ruff, the comfyui validator,
   and the required-template-files check).

## Constraints

- Never commit, move, or reference the contents of
  `comfyui-assets/character-reference/chowchow/source/` or `.../working/`
  (real photos/videos of the user's pet) — these are gitignored on purpose
  and must stay that way. `approved/` reference images are fine to use.
- No production data, real credentials, or API keys in any artifact or code
  — see `artifacts/spec.md` for a rotated-key incident already handled
  separately.
- No new runtime dependencies without human approval.
- Do not populate `config/settings.yaml`'s `studio.comfyui_remote_base_url`
  or otherwise wire in a paid remote ComfyUI instance — the user has
  confirmed local-only ESRGAN upscaling for now.
- Actual ComfyUI generation (keyframes, motion clips, upscales) and the
  Kaggle LoRA training run require a human at the machine; do not attempt
  to simulate or fake a successful run of either in place of the real
  thing — use the existing fake-provider test pattern instead.

## Verify command

```bash
./scripts/verify-local.sh
```

## Expected handoffs

- `codex_reviewer` reviews `comfyui-assets/kaggle_upload/kernel_chowchow_lora/kernel_entry_chowchow_lora.py`
  for correctness of its OOM-workaround patches (static review only, no GPU
  needed), reviews the new `validate_project.py` checks and the
  `build_loop`/`render_final` implementation against the concat-demuxer
  design, and confirms Part A1's fix status. Returns structured findings
  inline per `AGENTS.md`'s review contract.
- `agy_ui_data` inspects `src/lyria_auto/studio/web/` across the six review
  gates (accessibility, responsive behavior of the HTTP-Range video
  scrubber) and checks the `episodes`/`episode_assets`/`studio_tasks` schema
  against `docs/data-contract.md`. No `cao-mcp-server` for this profile —
  use local synthetic fixtures, per `docs/antigravity-mcp.md`. Returns
  `artifacts/ui-notes.md`.
- `claude_lead` implements the acceptance criteria above, integrates both
  workers' findings, runs `./scripts/verify-local.sh`, and writes
  `artifacts/test-report.md`.
