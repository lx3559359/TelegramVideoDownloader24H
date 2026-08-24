from pathlib import Path

from tg_video_downloader.paths import ProjectPaths


def test_create_runtime_directories_under_project(tmp_path: Path) -> None:
    paths = ProjectPaths.from_root(tmp_path)
    paths.ensure_directories()

    for path in paths.writable_directories:
        assert path.is_dir()
        assert path.is_relative_to(tmp_path.resolve())


def test_reject_download_directory_outside_project(tmp_path: Path) -> None:
    paths = ProjectPaths.from_root(tmp_path)
    outside = tmp_path.parent / "outside-downloads"

    try:
        paths.assert_within_root(outside)
    except ValueError as error:
        assert "项目目录之外" in str(error)
    else:
        raise AssertionError("outside path should be rejected")
