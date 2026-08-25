from pathlib import Path

from tg_video_downloader.gui.instance import GuiInstanceCoordinator
from tg_video_downloader.paths import ProjectPaths


def test_duplicate_instance_signals_first_and_releases_cleanly(tmp_path: Path) -> None:
    paths = ProjectPaths.from_root(tmp_path)
    first = GuiInstanceCoordinator(paths)
    second = GuiInstanceCoordinator(paths)

    assert first.acquire_or_signal() is True
    assert first.activation_requested() is False
    assert second.acquire_or_signal() is False
    assert first.activation_requested() is True
    assert first.activation_requested() is False

    first.close()
    third = GuiInstanceCoordinator(paths)
    assert third.acquire_or_signal() is True
    third.close()


def test_stale_activation_token_is_baseline_not_a_new_request(tmp_path: Path) -> None:
    paths = ProjectPaths.from_root(tmp_path)
    paths.runtime.mkdir(parents=True)
    paths.gui_activation.write_text("stale-token\n", encoding="ascii")
    instance = GuiInstanceCoordinator(paths)

    assert instance.acquire_or_signal() is True
    assert instance.activation_requested() is False

    instance.close()


def test_close_is_idempotent(tmp_path: Path) -> None:
    instance = GuiInstanceCoordinator(ProjectPaths.from_root(tmp_path))
    assert instance.acquire_or_signal() is True

    instance.close()
    instance.close()


def test_corrupt_activation_file_does_not_block_gui_start(tmp_path: Path) -> None:
    paths = ProjectPaths.from_root(tmp_path)
    paths.runtime.mkdir(parents=True)
    paths.gui_activation.write_bytes(b"\xff\xfe")

    instance = GuiInstanceCoordinator(paths)

    assert instance.acquire_or_signal() is True
    assert instance.activation_requested() is False
    instance.close()
