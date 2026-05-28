"""Generate electron-builder icon.ico (256x256) from babo.png."""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image


def main() -> None:
    root = Path(__file__).resolve().parent
    src = root.parent.parent / "frontend" / "src" / "assets" / "images" / "babo.png"
    icon_png = root / "icon.png"
    icon_ico = root / "icon.ico"
    if len(sys.argv) >= 2:
        src = Path(sys.argv[1])
    img = Image.open(src).convert("RGBA")
    img256 = img.resize((256, 256), Image.Resampling.LANCZOS)
    img.save(icon_png)
    img256.save(
        icon_ico,
        format="ICO",
        sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)],
    )
    print(f"Wrote {icon_png} and {icon_ico}")


if __name__ == "__main__":
    main()
