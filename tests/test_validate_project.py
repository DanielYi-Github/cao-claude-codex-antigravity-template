import importlib.util
import json
import shutil
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1] / "comfyui-assets"

_SPEC = importlib.util.spec_from_file_location(
    "validate_project", PROJECT_ROOT / "scripts" / "validate_project.py"
)
_MODULE = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader
_SPEC.loader.exec_module(_MODULE)
validate_project = _MODULE.validate_project


def _copy_project(root: Path) -> None:
    shutil.copytree(PROJECT_ROOT / "workflows", root / "workflows")
    (root / "scripts").mkdir()
    shutil.copy(
        PROJECT_ROOT / "scripts" / "download-models.sh",
        root / "scripts" / "download-models.sh",
    )


def _patch_widget(path: Path, node_type: str, index: int, value: object) -> None:
    """Edit one widget of the first node of a given type (UI-format file).

    Earlier revisions of these tests patched workflows with literal string
    replacement, which silently became a no-op whenever the widget values were
    reformatted -- the test then asserted against an unmodified file and failed.
    Editing the parsed JSON keeps the tests tied to structure instead of spelling.
    """
    workflow = json.loads(path.read_text(encoding="utf-8"))
    for node in workflow["nodes"]:
        if node.get("type") == node_type:
            node["widgets_values"][index] = value
            path.write_text(json.dumps(workflow, indent=2), encoding="utf-8")
            return
    raise AssertionError(f"no {node_type} node in {path.name}")


def _patch_api_input(path: Path, class_type: str, input_key: str, value: object) -> None:
    """Edit one named input of the first node of a given class (API-format file).

    validate_project() now cross-checks the UI and API exports of the same
    workflow for drift, so a test that retunes the UI file's widgets must
    apply the equivalent change to the API file too -- exactly what a human
    would have to do in practice after re-exporting.
    """
    workflow = json.loads(path.read_text(encoding="utf-8"))
    for node in workflow.values():
        if isinstance(node, dict) and node.get("class_type") == class_type:
            node["inputs"][input_key] = value
            path.write_text(json.dumps(workflow, indent=2), encoding="utf-8")
            return
    raise AssertionError(f"no {class_type} node in {path.name}")


class ValidateProjectTests(unittest.TestCase):
    def test_current_project_passes_validation(self):
        self.assertEqual(validate_project(PROJECT_ROOT), [])

    def test_dev_checkpoint_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _copy_project(root)

            _patch_widget(root / "workflows" / "cafe-keyframe-flux-mps.json",
                          "UnetLoaderGGUF", 0, "flux1-dev-Q4_K_S.gguf")

            errors = validate_project(root)

            self.assertTrue(
                any("flux1-dev" in error.lower() for error in errors),
                errors,
            )

    def test_mps_keyframe_wrong_steps_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _copy_project(root)

            # KSampler widgets: [seed, control_after_generate, steps, cfg, ...]
            _patch_widget(root / "workflows" / "cafe-keyframe-flux-mps.json",
                          "KSampler", 2, 8)

            errors = validate_project(root)

            self.assertTrue(
                any("steps must be 4" in error for error in errors),
                errors,
            )

    def test_keyframe_batch_must_generate_four_candidates(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _copy_project(root)
            _patch_widget(
                root / "workflows" / "cafe-keyframe-flux-mps.json",
                "EmptyLatentImage",
                2,
                1,
            )

            errors = validate_project(root)

            self.assertTrue(
                any("batch size must be 4" in error for error in errors),
                errors,
            )

    def test_generation_loop_length_drift_is_rejected(self):
        """Raising the generated frame count without retuning the trim breaks 8s.

        The generated frame count itself is a free tuning knob (lowering it cuts
        peak memory), so what is asserted is the coupling: 81 generated frames
        interpolate to 161, and keeping 128 of them no longer closes the loop.
        """
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _copy_project(root)

            _patch_widget(root / "workflows" / "cafe-flf2v-wan22-mps.json",
                          "WanFirstLastFrameToVideo", 2, 81)

            errors = validate_project(root)

            self.assertTrue(
                any("keep 160 frames, not 128" in error for error in errors),
                errors,
            )

    def test_generation_lower_frame_count_stays_valid_when_retuned(self):
        """The documented memory mitigation (49 frames, keep 96, 12 fps) must pass."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _copy_project(root)

            path = root / "workflows" / "cafe-flf2v-wan22-mps.json"
            _patch_widget(path, "WanFirstLastFrameToVideo", 2, 49)
            _patch_widget(path, "ImageFromBatch", 1, 96)
            _patch_widget(path, "CreateVideo", 0, 12)

            api_path = root / "workflows" / "api" / "cafe-flf2v-wan22-mps.json"
            _patch_api_input(api_path, "WanFirstLastFrameToVideo", "length", 49)
            _patch_api_input(api_path, "ImageFromBatch", "length", 96)
            _patch_api_input(api_path, "CreateVideo", "fps", 12)

            self.assertEqual(validate_project(root), [])

    def test_generation_duplicate_loop_frame_is_rejected(self):
        """Keeping all interpolated frames leaves a duplicate at the loop point."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _copy_project(root)

            # ImageFromBatch widgets: [batch_index, length]
            _patch_widget(root / "workflows" / "cafe-flf2v-wan22-mps.json",
                          "ImageFromBatch", 1, 129)

            errors = validate_project(root)

            self.assertTrue(
                any("stutters at the loop point" in error for error in errors),
                errors,
            )

    def test_generation_cfg_above_one_is_rejected(self):
        """The Lightning LoRA is distilled for CFG 1.0; anything else is a misconfig."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _copy_project(root)

            # KSamplerAdvanced widgets: [..., steps(3), cfg(4), ...]
            _patch_widget(root / "workflows" / "cafe-flf2v-wan22-mps.json",
                          "KSamplerAdvanced", 4, 3.5)

            errors = validate_project(root)

            self.assertTrue(
                any("requires CFG=1.0" in error for error in errors),
                errors,
            )

    def test_noncommercial_upscaler_is_rejected(self):
        """Remacri and UltraSharp are CC-BY-NC-SA-4.0 and must never sneak in."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _copy_project(root)

            _patch_widget(root / "workflows" / "cafe-upscale-1080p-mps.json",
                          "UpscaleModelLoader", 0, "4x_foolhardy_Remacri.pth")

            errors = validate_project(root)

            self.assertTrue(
                any("non-commercial" in error for error in errors),
                errors,
            )

    def test_upscale_workflow_missing_esrgan_node_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _copy_project(root)

            path = root / "workflows" / "cafe-upscale-1080p-mps.json"
            workflow = json.loads(path.read_text(encoding="utf-8"))
            workflow["nodes"] = [
                node for node in workflow["nodes"]
                if node.get("type") != "ImageUpscaleWithModel"
            ]
            path.write_text(json.dumps(workflow, indent=2), encoding="utf-8")

            errors = validate_project(root)

            self.assertTrue(
                any("expected exactly one ImageUpscaleWithModel" in error for error in errors),
                errors,
            )

    def test_upscale_workflow_wrong_output_size_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _copy_project(root)

            # ImageScale widgets: [upscale_method, width, height, crop]
            path = root / "workflows" / "cafe-upscale-1080p-mps.json"
            _patch_widget(path, "ImageScale", 1, 1280)
            _patch_widget(path, "ImageScale", 2, 720)

            errors = validate_project(root)

            self.assertTrue(
                any("final resize must be 1920x1080" in error for error in errors),
                errors,
            )

    def test_upscale_workflow_wrong_fps_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _copy_project(root)

            _patch_widget(root / "workflows" / "cafe-upscale-1080p-mps.json",
                          "CreateVideo", 0, 24)

            errors = validate_project(root)

            self.assertTrue(
                any("output fps must stay 16" in error for error in errors),
                errors,
            )

    def test_chowchow_lora_wrong_lora_name_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _copy_project(root)

            _patch_api_input(
                root / "workflows" / "api" / "cafe-keyframe-flux-chowchow-lora-mps.json",
                "LoraLoader", "lora_name", "wrong-lora.safetensors",
            )

            errors = validate_project(root)

            self.assertTrue(
                any("lora_name must be chowchow-identity-v2.safetensors" in error for error in errors),
                errors,
            )

    def test_chowchow_lora_wrong_cfg_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _copy_project(root)

            _patch_api_input(
                root / "workflows" / "api" / "cafe-keyframe-flux-chowchow-lora-mps.json",
                "KSampler", "cfg", 3.5,
            )

            errors = validate_project(root)

            self.assertTrue(
                any("CFG must be 1" in error for error in errors),
                errors,
            )

    def test_chowchow_composite_character_cutout_node_id_is_enforced(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _copy_project(root)

            path = root / "workflows" / "api" / "cafe-keyframe-flux-chowchow-composite-mps.json"
            workflow = json.loads(path.read_text(encoding="utf-8"))
            workflow["10"]["class_type"] = "SomethingElse"
            path.write_text(json.dumps(workflow, indent=2), encoding="utf-8")

            errors = validate_project(root)

            self.assertTrue(
                any("node '10' must be a LoadImage node" in error for error in errors),
                errors,
            )

    def test_api_generation_workflow_cfg_above_one_is_rejected(self):
        """The API file is what actually runs, so it needs the same CFG guard as the UI file."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _copy_project(root)

            _patch_api_input(
                root / "workflows" / "api" / "cafe-flf2v-wan22-mps.json",
                "KSamplerAdvanced", "cfg", 3.5,
            )

            errors = validate_project(root)

            self.assertTrue(
                any("requires CFG=1.0" in error for error in errors),
                errors,
            )

    def test_ui_api_drift_is_rejected(self):
        """Retuning only the UI export and forgetting the API export must fail loudly."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _copy_project(root)

            _patch_widget(root / "workflows" / "cafe-flf2v-wan22-mps.json",
                          "CreateVideo", 0, 12)
            # API file deliberately left at fps=16 -- this is the drift.

            errors = validate_project(root)

            self.assertTrue(
                any("CreateVideo fps drifted from" in error for error in errors),
                errors,
            )


if __name__ == "__main__":
    unittest.main()
