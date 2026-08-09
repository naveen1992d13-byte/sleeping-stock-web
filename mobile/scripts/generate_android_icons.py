#!/usr/bin/env python3
"""Generate Expo/Android icon assets from the official Sleeping Stock logo source."""
from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image


def _fit_center(im: Image.Image, canvas: int, scale: float, bg: tuple[int, int, int, int]) -> Image.Image:
    target = int(canvas * scale)
    ratio = min(target / im.width, target / im.height)
    w, h = int(im.width * ratio), int(im.height * ratio)
    resized = im.resize((w, h), Image.Resampling.LANCZOS)
    out = Image.new("RGBA", (canvas, canvas), bg)
    out.paste(resized, ((canvas - w) // 2, (canvas - h) // 2), resized)
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("--assets-dir", type=Path, default=Path(__file__).resolve().parents[1] / "assets")
    args = parser.parse_args()
    source = args.source.resolve()
    assets = args.assets_dir.resolve()
    if not source.is_file():
        raise SystemExit(f"Source icon not found: {source}")

    im = Image.open(source).convert("RGBA")
    bg = (5, 7, 6, 255)

    # Legacy/Play icon: full logo with modest padding so edges stay visible.
    icon = _fit_center(im, 1024, 0.88, bg)
    icon.save(assets / "icon.png", optimize=True)

    # Adaptive foreground: keep artwork inside ~66% safe zone to avoid launcher crop.
    adaptive = _fit_center(im, 1024, 0.58, (0, 0, 0, 0))
    adaptive.save(assets / "adaptive-icon.png", optimize=True)

    splash = _fit_center(im, 1024, 0.72, bg)
    splash.save(assets / "splash-logo.png", optimize=True)

    notif = _fit_center(im, 96, 0.82, bg)
    notif.save(assets / "notification-icon.png", optimize=True)

    print(f"Generated icons in {assets} from {source}")


if __name__ == "__main__":
    main()
