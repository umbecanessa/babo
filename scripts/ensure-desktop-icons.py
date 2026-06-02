#!/usr/bin/env python3
"""Generate desktop/build/icon.png and icon.ico from the frontend Babo asset."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "frontend" / "src" / "assets" / "images" / "babo.png"
OUT_DIR = ROOT / "desktop" / "build"


def main() -> int:
    try:
        from PIL import Image
    except ImportError:
        print("Install Pillow: pip install pillow", file=sys.stderr)
        return 1

    if not SRC.is_file():
        print(f"Missing source image: {SRC}", file=sys.stderr)
        return 1

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    icon_png = OUT_DIR / "icon.png"
    icon_ico = OUT_DIR / "icon.ico"

    if icon_png.is_file() and icon_ico.is_file():
        print(f"Icons already present in {OUT_DIR}")
        return 0

    img = Image.open(SRC).convert("RGBA")
    img = img.resize((256, 256), Image.Resampling.LANCZOS)
    img.save(icon_png)
    sizes = [(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)]
    img.save(icon_ico, format="ICO", sizes=sizes)
    print(f"Wrote {icon_png} and {icon_ico}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
