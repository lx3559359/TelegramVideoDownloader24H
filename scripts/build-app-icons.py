"""Mechanically resize the approved master into packaged Windows icons."""
from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
MASTER = ROOT / "packaging/assets/app-icon-master-v1.png"
OUTPUT = ROOT / "src/tg_video_downloader/assets"
SIZES = (16, 24, 32, 48, 64, 128, 256)


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    with Image.open(MASTER) as source:
        image = source.convert("RGBA")
        if image.width != image.height:
            raise ValueError("Approved icon master must be square")
        image = image.resize((256, 256), Image.Resampling.LANCZOS)
        image.save(OUTPUT / "app.png", optimize=True)
        image.save(OUTPUT / "app.ico", sizes=[(size, size) for size in SIZES])


if __name__ == "__main__":
    main()
