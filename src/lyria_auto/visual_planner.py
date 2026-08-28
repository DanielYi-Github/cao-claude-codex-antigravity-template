from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .visual_models import ScenePlan, VisualPlan, WorldBible


@dataclass
class VisualPlanner:
    """從專輯的 PromptPlan 產生視覺 world bible 與場景規劃。"""

    seed: int

    def compose(
        self,
        representative: Any,
        target_minutes: int,
    ) -> VisualPlan:
        """產生完整的視覺規劃。

        Args:
            representative: 代表性的 PromptPlan（包含 scene, mood, genre 等）
            target_minutes: 目標影片長度（分鐘）

        Returns:
            VisualPlan 包含世界設定與四個場景的規劃
        """
        scene = getattr(representative, "scene", "coffeehouse")
        mood = getattr(representative, "mood", "cozy")
        genre = getattr(representative, "genre", "lofi jazz")
        texture = getattr(representative, "texture", "warm wood")
        production = getattr(representative, "production", "intimate")

        # Build world bible from prompt plan attributes
        world = WorldBible(
            architecture=f"Single-story {scene} with large windows and cozy seating areas",
            materials=f"{texture} surfaces, {production} lighting fixtures, vintage furniture",
            color_palette=f"{mood} tones with warm amber and soft natural light",
            time_of_day="late afternoon",
            weather="light rain outside",
            window_view="blurred cityscape through rain-streaked glass",
            props="coffee cups, books, small plants, table lamps",
        )

        # World anchor prompt - the reference image for all scenes
        world_anchor_prompt = (
            f"Photorealistic interior of a {mood} {scene}, {texture} materials, "
            f"{production} atmosphere, {genre} aesthetic, no people, no text, "
            f"16:9 aspect ratio, 2K resolution, warm color grading, "
            f"soft natural lighting from large windows, light rain visible outside"
        )

        # Four fixed camera positions in the same café
        scenes = (
            ScenePlan(
                position=1,
                label="A",
                role="Window-side main view, also source for thumbnail world",
                allowed_motion="rain drops on glass, coffee steam, distant bokeh movement",
                image_prompt=(
                    f"Photorealistic {mood} {scene} interior, view from window seat, "
                    f"large window with rain drops, {texture} table with coffee cup, "
                    f"soft natural light, no people, no text, 16:9, 2K"
                ),
                motion_prompt=(
                    "Single continuous shot, locked-off camera, gentle rain drops on window, "
                    "subtle steam rising from coffee cup, no people, no cuts, 8 seconds loop"
                ),
            ),
            ScenePlan(
                position=2,
                label="B",
                role="Book wall or reading corner",
                allowed_motion="wall lamp glow, book pages or curtain minimal movement",
                image_prompt=(
                    f"Photorealistic {mood} {scene} interior, book wall corner, "
                    f"{texture} shelves with books, warm wall lamp, cozy reading nook, "
                    f"no people, no text, 16:9, 2K"
                ),
                motion_prompt=(
                    "Single continuous shot, locked-off camera, subtle wall lamp flicker, "
                    "gentle curtain movement from draft, no people, no cuts, 8 seconds loop"
                ),
            ),
            ScenePlan(
                position=3,
                label="C",
                role="Bar or coffee equipment area",
                allowed_motion="steam from espresso machine, pendant light reflection, subtle shadows",
                image_prompt=(
                    f"Photorealistic {mood} {scene} interior, coffee bar area, "
                    f"espresso machine, {texture} counter, pendant lights, "
                    f"no people, no text, 16:9, 2K"
                ),
                motion_prompt=(
                    "Single continuous shot, locked-off camera, gentle steam from machine, "
                    "subtle light reflection changes, no people, no cuts, 8 seconds loop"
                ),
            ),
            ScenePlan(
                position=4,
                label="D",
                role="Fireplace or deep seating area",
                allowed_motion="fireplace flames, plant leaves, warm light changes",
                image_prompt=(
                    f"Photorealistic {mood} {scene} interior, fireplace corner, "
                    f"deep seating area, {texture} fireplace, small plants, "
                    f"warm ambient light, no people, no text, 16:9, 2K"
                ),
                motion_prompt=(
                    "Single continuous shot, locked-off camera, gentle fireplace flames, "
                    "subtle plant leaf movement, warm light shifts, no people, no cuts, 8 seconds loop"
                ),
            ),
        )

        # Thumbnail background prompt (same world as scene A, but dedicated composition)
        thumbnail_prompt = (
            f"Photorealistic {mood} {scene} interior, wide angle from window seat, "
            f"{texture} table in foreground, large window with rain, "
            f"clean composition suitable for text overlay, no people, no text, 16:9, 2K"
        )

        return VisualPlan(
            version=1,
            world=world,
            world_anchor_prompt=world_anchor_prompt,
            scenes=scenes,
            thumbnail_prompt=thumbnail_prompt,
        )
