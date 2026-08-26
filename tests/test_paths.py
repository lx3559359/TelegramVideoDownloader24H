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


def test_gui_control_files_stay_inside_runtime(tmp_path: Path) -> None:
    paths = ProjectPaths.from_root(tmp_path)

    assert paths.gui_lock == paths.runtime / "gui.lock"
    assert paths.gui_activation == paths.runtime / "gui-activate.request"
    assert paths.gui_lock.is_relative_to(paths.root)
    assert paths.gui_activation.is_relative_to(paths.root)


def test_update_control_files_stay_inside_project(tmp_path: Path) -> None:
    paths = ProjectPaths.from_root(tmp_path)

    assert paths.update_request == paths.runtime / "update-request.json"
    assert paths.update_result == paths.runtime / "update-result.json"
    assert paths.update_log == paths.logs / "update.log"
    assert paths.update_request.is_relative_to(paths.root)
    assert paths.update_result.is_relative_to(paths.root)
    assert paths.update_log.is_relative_to(paths.root)


def test_search_control_files_stay_inside_runtime(tmp_path: Path) -> None:
    paths = ProjectPaths.from_root(tmp_path)

    assert paths.search_endpoint == paths.runtime / "search-endpoint.json"
    assert paths.telegram_client_lock == paths.runtime / "telegram-client.lock"
    assert paths.search_endpoint.is_relative_to(paths.root)
    assert paths.telegram_client_lock.is_relative_to(paths.root)
