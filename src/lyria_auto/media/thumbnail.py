from __future__ import annotations

import math
import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFont


def _font(size: int):
    candidates = [
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/Hiragino Sans GB.ttc",
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
        "C:/Windows/Fonts/msjh.ttc",
        "C:/Windows/Fonts/arial.ttf",
    ]
    for path in candidates:
        if Path(path).exists():
            return ImageFont.truetype(path, size=size)
    return ImageFont.load_default()


def _fit_cover(img: Image.Image, width: int, height: int) -> Image.Image:
    ratio = max(width / img.width, height / img.height)
    resized = img.resize((math.ceil(img.width * ratio), math.ceil(img.height * ratio)), Image.Resampling.LANCZOS)
    left = (resized.width - width) // 2
    top = (resized.height - height) // 2
    return resized.crop((left, top, left + width, top + height))


def _generated_background(width: int, height: int, seed: int) -> Image.Image:
    rng = random.Random(seed)
    top = (rng.randint(35, 65), rng.randint(35, 60), rng.randint(25, 45))
    bottom = (rng.randint(120, 170), rng.randint(75, 115), rng.randint(45, 80))
    img = Image.new("RGB", (width, height))
    px = img.load()
    for y in range(height):
        t = y / max(1, height - 1)
        color = tuple(int(top[i] * (1 - t) + bottom[i] * t) for i in range(3))
        for x in range(width):
            px[x, y] = color
    draw = ImageDraw.Draw(img, "RGBA")
    draw.rectangle((0, int(height * .62), width, height), fill=(25, 18, 14, 150))
    draw.ellipse((int(width*.68), int(height*.63), int(width*.82), int(height*.88)), fill=(50, 30, 20, 230))
    draw.ellipse((int(width*.70), int(height*.61), int(width*.80), int(height*.70)), fill=(220, 195, 155, 220))
    draw.rectangle((int(width*.17), int(height*.18), int(width*.55), int(height*.58)), fill=(25, 35, 38, 130), outline=(230, 180, 95, 80), width=4)
    return img


def create_thumbnail(
    output_path: str | Path,
    title: str,
    subtitle: str,
    width: int,
    height: int,
    quality: int,
    background_dir: str | Path,
    seed: int,
) -> Path:
    bg_dir = Path(background_dir)
    candidates = [p for p in bg_dir.glob("*") if p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}]
    if candidates:
        chosen = random.Random(seed).choice(candidates)
        img = _fit_cover(Image.open(chosen).convert("RGB"), width, height)
        img = ImageEnhance.Brightness(img).enhance(0.62)
    else:
        img = _generated_background(width, height, seed)

    overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    draw.rounded_rectangle((60, 70, int(width*.72), height-70), radius=28, fill=(10, 10, 10, 145))
    title_font = _font(max(36, width // 18))
    subtitle_font = _font(max(22, width // 34))
    draw.multiline_text((100, 150), title, font=title_font, fill=(248, 236, 216, 255), spacing=12)
    draw.text((100, height-180), subtitle, font=subtitle_font, fill=(226, 195, 150, 255))
    img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out, "JPEG", quality=quality, optimize=True, progressive=True)
    if out.stat().st_size > 2 * 1024 * 1024:
        img.save(out, "JPEG", quality=max(65, quality - 20), optimize=True)
    return out
