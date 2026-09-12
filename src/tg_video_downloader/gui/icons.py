"""Shared application identity for source installs and PyInstaller bundles."""
from functools import lru_cache
from pathlib import Path
from typing import Any

from PIL import Image


# PyInstaller preserves the package-relative __file__ location inside its bundle.
ASSET_DIR = Path(__file__).resolve().parents[1] / "assets"


@lru_cache(maxsize=8)
def _load_logo(size: int) -> Image.Image | None:
    try:
        with Image.open(ASSET_DIR / "app.png") as source:
            return source.convert("RGBA").resize((size, size), Image.Resampling.LANCZOS)
    except (OSError, ValueError):
        return None


def load_app_icon(size: int = 64) -> Image.Image | None:
    """Return an independent image; decode and resize only once per requested size."""
    image = _load_logo(size)
    return image.copy() if image is not None else None


def set_window_icon(root: Any) -> bool:
    """Set Tk window/taskbar identity, without preventing launch if assets fail."""
    from tkinter import TclError

    icon = ASSET_DIR / "app.ico"
    if not icon.is_file():
        return False
    try:
        root.iconbitmap(default=str(icon))
    except (OSError, TclError):
        return False
    return True
