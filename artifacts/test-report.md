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
