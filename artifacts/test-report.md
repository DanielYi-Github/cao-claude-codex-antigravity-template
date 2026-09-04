# Chow Chow LoRA v3 test report

- Date: 2026-09-02 (Asia/Taipei)
- Branch: `codex/chowchow-lora-v3`
- Baseline preparation commit: `b4837bf`
- Acceptance result: PASS for the improved Schnell fallback; FLUX.1-dev compatibility remains follow-up work.

## Training result

Private Kaggle dataset `danielyiyi/chowchow-mascot-lora-dataset-v3` contains 26 curated image/caption pairs: 14 preferred older cute references and 12 newer pose/anatomy references. Kernel `danielyiyi/chowchow-mascot-lora-train` version 26 completed 1,000 training steps without OOM. The final artifact was installed as `/Users/danielyi/ComfyUI-Shared/models/loras/chowchow-identity-v3.safetensors`.

## Visual result

The matched-seed stress test generated 32 images. All showed one dog and no duplicate head. v3 at 0.35 preserved the identity while avoiding the more conspicuous paired/tucked rear paws sometimes produced at 0.50 and 0.65. A separate four-image run using the committed workflow produced four single-dog, single-head candidates with no obvious extra limb. Logo-like laptop marks remain a rejection/inpainting case.

A later wide-composition iteration (`wide-final-v2`, seed base `26090360`, batch 4)
placed the dog at roughly 28–38% of image width while keeping the full body readable.
All four candidates remained single-dog and single-head with no obvious extra limb. The
prompt now uses concrete camera distance and spatial scale constraints and removes the laptop,
which also avoided the recurring logo failure in this batch.

The bare-floor iteration (`bare-floor`, seed base `26090400`, batch 4) removed rugs and
mats in all four candidates. Three candidates were acceptable single-dog images; one contained
a duplicated head and must be rejected. The production prompt therefore reinforces one continuous
body and exactly one head near the pose description, but human candidate approval remains required.

A second bare-floor batch (`bare-floor-v2`, seed base `26090440`, batch 4) again produced
no rugs or mats in all four images. Three were valid single-dog candidates and one duplicated
the head/body. The repeated 3/4 result confirms the floor change while showing that explicit
single-head wording does not eliminate Schnell's anatomy failure mode.

Ignored local evidence is under `workspace/temp/chowchow-v3-stress/`; it is intentionally not committed because it is generated media. The selected Kaggle output is under `workspace/temp/kaggle-chowchow-v3-selected/` and is also intentionally ignored.

## Commands

```text
python3 comfyui-assets/scripts/validate_project.py
/Users/danielyi/Documents/Projects/cao-claude-codex-antigravity-forVideo/.venv/bin/pytest -q tests/test_studio_stages.py tests/test_studio_app.py tests/test_staged_workflow.py tests/test_validate_project.py
```

Result: validation PASS; 82 tests passed; one Starlette/httpx deprecation warning.

## Night jazz-bistro composition

The final night iteration (`night-jazz-v2`, seed base `26090540`, batch 4) replaced the
daylit reading-room look with dark walnut, burgundy banquettes, amber practical lights,
blue rainy windows, and an empty after-hours jazz-bistro atmosphere. All four candidates
showed a single dog directly on bare wood with no physical people or duplicate heads.
Candidate 2 is the preferred production reference; the others require rejection or cleanup
for repeated pianos, synthetic exterior lettering, or portrait artwork.

---

# Five-tab Studio completion test report

- Date: 2026-09-05 (Asia/Taipei)
- Branch: `codex/chowchow-lora-v3`
- Specification: `artifacts/spec.md`
- Acceptance result: **PASS for implementation and fake-provider verification**; real external-provider and browser acceptance remains a human gate.

## Covered behavior

- Safe PNG/JPEG/WebP keyframe import restricted to the configured workspace, including path-traversal-resistant episode slugs.
- Explicit Tab 3 production start/cancel, both motion roles, approval-driven 1080p upscale, and final loop build.
- Atomic, payload-free synchronous music task guard; exactly 12 persistent ordered track slots; per-track failure/retry/review; API-key redaction and SQLite non-persistence.
- Twelve-track FLAC crossfade and complete-album cycling to at least the configured duration floor.
- Approved-loop plus approved-mix final rendering, Range-capable playback/download through the existing asset endpoint, editable English metadata, private-by-default YouTube upload, synthetic-media disclosure, callback persistence, and idempotent repeat upload.

## Commands and results

```text
/Users/danielyi/Documents/Projects/cao-claude-codex-antigravity-forVideo/.venv/bin/pytest -q
276 passed, 1 warning in 92.21s

/Users/danielyi/Documents/Projects/cao-claude-codex-antigravity-forVideo/.venv/bin/ruff check <all changed Python source and test files>
All checks passed!

/Users/danielyi/Documents/Projects/cao-claude-codex-antigravity-forVideo/.venv/bin/python comfyui-assets/scripts/validate_project.py
PASS: commercial project validation

node --check src/lyria_auto/studio/web/app.js
node --check src/lyria_auto/studio/web/tabs/tab1-keyframes.js
node --check src/lyria_auto/studio/web/tabs/tab3-production.js
node --check src/lyria_auto/studio/web/tabs/tab4-music.js
node --check src/lyria_auto/studio/web/tabs/tab5-final.js
all passed

git diff --check
passed
```

The single pytest warning is an upstream `StarletteDeprecationWarning` about the current `httpx` TestClient compatibility layer. It does not indicate a failed Studio behavior.

## Known verification limits

- `ruff check .` reports 13 pre-existing findings in `.cao/` and `comfyui-assets/scripts/`; the exact changed-file lint command passes.
- `scripts/verify-local.sh` was not invoked directly because this worktree intentionally reuses the repository-root virtual environment instead of owning `.venv`. Its substantive checks were run manually with that environment.
- The local server started successfully, but the browser skill found zero available browsers. No screenshot, responsive-layout, keyboard, or screen-reader test was possible.
- Real ComfyUI/Lyria/ffmpeg two-hour production and YouTube OAuth/upload were not invoked, so no provider cost was incurred and no content was published.

## Scenic destination-cafe composition

Five user-provided ambience references were distilled into a project-local target image at
`workspace/temp/chowchow-v3-targets/scenic-lakeside-cafe-target-v1.png`. The matching ComfyUI
iteration (`scenic-lake-cafe`, seed base `26090600`, batch 4) uses a landscape-first open lakeside
pavilion at blue hour, with natural timber and stone framing, warm lanterns, and the dog as a
secondary middle-distance subject. All four candidates contained one dog, one head, no people,
and no floor mat. Candidates 1 and 3 best satisfy the long-form ambience composition.
