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
