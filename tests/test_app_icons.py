from importlib.util import find_spec
import ast
from pathlib import Path
from types import SimpleNamespace

from PIL import Image

from tg_video_downloader.gui.tray import RUNNING_COLOR, create_status_icon


ASSETS = Path(__file__).resolve().parents[1] / "src/tg_video_downloader/assets"


def test_packaging_paths_resolve_independently_of_working_directory(monkeypatch, tmp_path):
    root = Path(__file__).resolve().parents[1]
    spec = root / "packaging/TelegramVideoDownloader.spec"
    tree = ast.parse(spec.read_text(encoding="utf-8"))
    tree.body = [node for node in tree.body if not (
        isinstance(node, ast.ImportFrom) and node.module.startswith("PyInstaller")
    )]
    captured = {}

    def analysis(scripts, **kwargs):
        captured.update(scripts=scripts, **kwargs)
        return SimpleNamespace(pure=[], scripts=[], binaries=[], datas=[])

    def executable(*args, **kwargs):
        captured["icon"] = kwargs["icon"]

    namespace = dict(SPECPATH=str(spec.parent), Analysis=analysis, EXE=executable,
                     PYZ=lambda *args: None, COLLECT=lambda *args, **kwargs: None,
                     collect_all=lambda *args: ([], [], []),
                     copy_metadata=lambda *args, **kwargs: [])
    monkeypatch.chdir(tmp_path)
    exec(compile(tree, str(spec), "exec"), namespace)
    assert Path(captured["scripts"][0]) == root / "packaging/windows_entry.py"
    assert Path(captured["pathex"][0]) == root / "src"
    assert Path(captured["datas"][0][0]) == ASSETS
    assert Path(captured["icon"]) == ASSETS / "app.ico"
    assert all(Path(path).exists() for path in (
        captured["scripts"][0], captured["pathex"][0],
        captured["datas"][0][0], captured["icon"],
    ))


def test_distribution_icons_include_all_windows_sizes():
    assert (ASSETS / "app.png").is_file()
    with Image.open(ASSETS / "app.ico") as icon:
        assert icon.ico.sizes() == {(n, n) for n in (16, 24, 32, 48, 64, 128, 256)}


def test_tray_preserves_logo_and_adds_status_badge():
    image = create_status_icon(RUNNING_COLOR)
    assert image.getpixel((53, 53)) == RUNNING_COLOR
    with Image.open(ASSETS / "app.png") as source:
        logo = source.convert("RGBA").resize((64, 64), Image.Resampling.LANCZOS)
    assert image.crop((0, 0, 42, 64)).tobytes() == logo.crop((0, 0, 42, 64)).tobytes()


def test_shared_icon_loader_caches_and_returns_independent_images(monkeypatch):
    assert find_spec("tg_video_downloader.gui.icons") is not None
    from tg_video_downloader.gui import icons

    icons._load_logo.cache_clear()
    original_open = Image.open
    reads = []

    def counting_open(*args, **kwargs):
        reads.append(args[0])
        return original_open(*args, **kwargs)

    monkeypatch.setattr(Image, "open", counting_open)
    first = icons.load_app_icon(64)
    second = icons.load_app_icon(64)
    assert len(reads) == 1
    first.putpixel((0, 0), (123, 45, 67, 255))
    assert second.getpixel((0, 0)) != first.getpixel((0, 0))


def test_missing_assets_degrade_without_crash(monkeypatch, tmp_path):
    assert find_spec("tg_video_downloader.gui.icons") is not None
    from tg_video_downloader.gui import icons

    monkeypatch.setattr(icons, "ASSET_DIR", tmp_path)
    icons._load_logo.cache_clear()
    assert icons.load_app_icon(64) is None
    assert create_status_icon(RUNNING_COLOR).size == (64, 64)
    assert icons.set_window_icon(object()) is False
    icons._load_logo.cache_clear()


def test_window_uses_packaged_ico():
    assert find_spec("tg_video_downloader.gui.icons") is not None
    from tg_video_downloader.gui import icons

    class Window:
        def iconbitmap(self, **kwargs):
            self.path = kwargs["default"]

    window = Window()
    assert icons.set_window_icon(window) is True
    assert Path(window.path) == ASSETS / "app.ico"
