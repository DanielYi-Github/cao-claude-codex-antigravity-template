import importlib.util
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "staged_workflow_server",
    ROOT / "comfyui-assets" / "scripts" / "staged_workflow_server.py",
)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(MODULE)


class StagedWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.state = MODULE.initial_state()
        self.state.update(
            approved_image="approved-frame.png",
            image_prompt="a quiet cafe",
            motion_prompt="locked camera, gentle steam",
            preview_approved=True,
        )

    def test_preview_is_eight_seconds_and_reuses_one_frame(self):
        workflow = MODULE.workflow_for("preview", self.state)
        by_type = {node["type"]: node for node in workflow["nodes"]}
        self.assertEqual(by_type["WanFirstLastFrameToVideo"]["widgets_values"][:3], [768, 432, 65])
        self.assertEqual(by_type["ImageFromBatch"]["widgets_values"][1], 128)
        self.assertEqual(by_type["CreateVideo"]["widgets_values"][0], 16)
        self.assertNotIn("ImageUpscaleWithModel", by_type)
        images = [node["widgets_values"][0] for node in workflow["nodes"] if node["type"] == "LoadImage"]
        self.assertEqual(images, ["approved-frame.png", "approved-frame.png"])

    def test_cloud_stage_is_deferred(self):
        """1080p is now a local post-process upscale, not a remote-GPU bundle."""
        with self.assertRaisesRegex(ValueError, "cafe-upscale-1080p-mps"):
            MODULE.workflow_for("cloud", self.state)

    def test_image_workflow_generates_four_candidates(self):
        workflow = MODULE.workflow_for("image", self.state)
        latent = next(node for node in workflow["nodes"] if node["type"] == "EmptyLatentImage")
        self.assertEqual(latent["widgets_values"][2], 4)

    def test_approval_accepts_input_directory_and_input_image(self):
        with tempfile.TemporaryDirectory() as directory:
            comfy = Path(directory)
            (comfy / "input").mkdir()
            (comfy / "output").mkdir()
            image = comfy / "input" / "external.png"
            image.write_bytes(b"png")

            self.assertEqual(MODULE.normalize_comfyui_dir(str(comfy / "input")), comfy.resolve())
            self.assertEqual(MODULE.find_approval_image(comfy.resolve(), image.name), image.resolve())

            with self.assertRaisesRegex(ValueError, "你是不是要選：external.png"):
                MODULE.find_approval_image(comfy.resolve(), "exernal.png")

    def test_approval_rejects_file_outside_comfyui(self):
        with tempfile.TemporaryDirectory() as directory:
            comfy = Path(directory) / "ComfyUI"
            (comfy / "input").mkdir(parents=True)
            outside = Path(directory) / "outside.png"
            outside.write_bytes(b"png")

            with self.assertRaisesRegex(ValueError, "input 或 output"):
                MODULE.find_approval_image(comfy, str(outside))


if __name__ == "__main__":
    unittest.main()
