# Summary

Branch `codex/chowchow-lora-v3` upgrades the Chow Chow identity dataset and LoRA from v2 to v3, then tunes the MPS keyframe fallback from LoRA strength 0.65 to the tested 0.35. The default composition now keeps the dog's full body unobstructed and explicitly constrains the front paws, total legs, tail, camera angle, and absence of people.

Acceptance criteria checked: Kaggle v3 training completes; the final LoRA downloads and loads in ComfyUI; matched-seed v2/v3 renders are produced; the selected configuration produces one dog with no duplicate head or obvious extra limb in the final four-image smoke test; project validation and relevant tests pass.

# Blocking findings

None for using the current Schnell workflow as an improved local fallback.

# Non-blocking findings

- **High — model compatibility — `cafe-keyframe-flux-chowchow-lora-mps.json`, node 1:** v3 was trained on FLUX.1-dev but the installed text-to-image base is `flux1-schnell-Q4_K_S.gguf`. Identity and anatomy improved in the sampled seeds, but this mismatch limits reliability. Install a properly licensed FLUX.1-dev base and create a separate dev workflow before claiming full LoRA compatibility.
- **Medium — prompt compliance — keyframe node 2:** several Schnell samples still drew an Apple-like laptop mark despite `no logos`. CFG=1 makes the negative-conditioning node inert, and positive wording is not a guarantee. Remove logo-prone props, mask/inpaint them, or reject those candidates before publication.
- **Medium — statistical confidence — generated A/B samples:** 32 matched stress-test candidates and four final smoke-test candidates are enough to tune the fallback, not to estimate a production failure rate. Continue human approval before WAN animation.

# Tests executed

- `python3 comfyui-assets/scripts/build_chowchow_lora_dataset_v3.py --force` — built 26 JPG/TXT pairs from the manifest.
- Kaggle kernel `danielyiyi/chowchow-mascot-lora-train`, version 26 — completed 1,000/1,000 steps and saved v3.
- SHA-256 of downloaded and installed final LoRA — `c7f4fd8e664839e531ab7dbc38c997e3fa74d663bf8b746e232a0b19b95c1019`.
- ComfyUI A/B, seed base `26090220`, batch 8 each — v2 0.50; v3 0.35, 0.50, and 0.65; 32 images total.
- ComfyUI final workflow smoke test, seed base `26090280`, batch 4 — completed; one dog and one head in all four, no obvious extra limb.
- `python3 comfyui-assets/scripts/validate_project.py` — PASS.
- `/Users/danielyi/Documents/Projects/cao-claude-codex-antigravity-forVideo/.venv/bin/pytest -q tests/test_studio_stages.py tests/test_studio_app.py tests/test_staged_workflow.py tests/test_validate_project.py` — 82 passed, one upstream deprecation warning.

# Suggested patch

Use the patch on `codex/chowchow-lora-v3`: v3 model filenames, keyframe strength 0.35, unobstructed anatomy-aware default prompt, v3 dataset builder/manifest, Kaggle v3 training configuration, and synchronized validator tests/documentation.

# Confidence and unknowns

Confidence is high that the workflow runs and is materially safer for the tested resting pose. Confidence is medium that it generalizes across arbitrary poses because the base-model mismatch remains. Standing, walking, rear-facing, crowded-prop, and WAN motion tests were not performed.

---

# Five-tab Studio completion review (2026-09-05)

## Summary

Branch `codex/chowchow-lora-v3` now implements the previously incomplete Tab 3–5 production path and connects it to the existing Tab 1–2 review flow. Tab 1 can safely import the user's selected scenic keyframe from the configured workspace; Tab 3 has an explicit production-cost boundary, clip/upscale review, and approved loop build; Tab 4 creates and reviews exactly 12 request-scoped Lyria tracks before a lossless album mix; Tab 5 renders, previews, downloads, approves, and optionally uploads a final video with editable metadata and two explicit upload confirmations.

The implementation was reviewed against `artifacts/spec.md`, the current database contract, the existing phase plan, the changed API/UI/stage/provider code, and the full automated suite. No real paid provider request or YouTube upload was made during review.

## Blocking findings

None in the implemented local/API flow under the documented single-process Studio assumption.

## Non-blocking findings

- **Medium — real-provider validation — `src/lyria_auto/studio/music.py`, `src/lyria_auto/studio/stages.py`, `src/lyria_auto/studio/final.py`:** automated tests use fake Lyria, ComfyUI, ffmpeg metadata, and YouTube clients. A human must still inspect the real Sleep/Lookup motion, 1080p loop seams, all 12 music tracks, final audio/video synchronization, and the intended OAuth channel before publication.
- **Medium — browser visual/accessibility QA — `src/lyria_auto/studio/web/`:** the in-app browser reported that no browser was available, so the frontend received JavaScript syntax checks and API integration tests but no responsive, keyboard, screen-reader, or screenshot pass. Run the Studio in Chrome/Safari before treating the UI polish as final.
- **Medium — concurrency scope — `src/lyria_auto/db.py:start_synchronous_task`, `src/lyria_auto/studio/app.py:start_production`:** paid music starts have an atomic SQLite duplicate-spend guard, but several older check-then-enqueue paths still assume the one web process/one worker topology described in the architecture plan. Add broader database uniqueness/reservation constraints before supporting multiple Studio processes.
- **Low — remote ComfyUI credentials — `src/lyria_auto/cli.py`, `src/lyria_auto/studio/app.py:create_app`:** Tab 3 uses the production client configured when Studio starts; it intentionally does not accept a browser-entered vendor token. A provider-specific credential flow remains a product decision after a remote service and authentication protocol are selected.
- **Low — repository-wide lint baseline — `.cao/workflows/three_agent_demo.py`, `comfyui-assets/scripts/`:** `ruff check .` reports 13 pre-existing findings outside the Studio scope. All Python files changed by this implementation pass Ruff; unrelated scripts were preserved.

## Tests executed

- `/Users/danielyi/Documents/Projects/cao-claude-codex-antigravity-forVideo/.venv/bin/pytest -q` — **276 passed**, one upstream Starlette/httpx deprecation warning.
- Ruff on every Python source/test file changed by this implementation — **PASS**.
- `/Users/danielyi/Documents/Projects/cao-claude-codex-antigravity-forVideo/.venv/bin/python comfyui-assets/scripts/validate_project.py` — **PASS**.
- `node --check` on `app.js` and Tab 1/3/4/5 modules — **PASS**.
- `git diff --check` — **PASS**.
- `ruff check .` — **not clean** because of 13 pre-existing, out-of-scope findings listed above.
- Local Studio server startup — **PASS** on `127.0.0.1:8798`; browser navigation could not proceed because the browser plugin exposed no browser instance.

## Suggested patch

Use this branch's patch as the integrated Studio implementation. Before a public release, perform one real-provider episode using `workspace/temp/scenic-cafe-personal-chowchow-v3/01-spring-sunny.png`, record the visual/audio review outcome, and then decide whether remote ComfyUI authentication and multi-process reservations are required.

## Confidence and unknowns

Confidence is high in API state transitions, secret non-persistence, media-stage selection rules, approval gates, and fake-provider behavior because those paths are covered by the 276-test suite. Confidence is medium in frontend presentation and end-to-end external-provider behavior because no browser, paid Lyria request, real production ComfyUI run, two-hour playback, or YouTube upload was performed. `artifacts/ui-notes.md` and `artifacts/schema.md` do not exist in this worktree, so there were no newer Antigravity UI/data notes to incorporate.
