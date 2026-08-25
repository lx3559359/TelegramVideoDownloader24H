from pathlib import Path

import pytest

from tg_video_downloader.config import ConfigStore
from tg_video_downloader.models import AppConfig, Credentials, GroupTarget
from tg_video_downloader.paths import ProjectPaths
from tg_video_downloader.storage import effective_download_root


def test_round_trip_config_and_credentials(tmp_path: Path) -> None:
    store = ConfigStore(ProjectPaths.from_root(tmp_path))
    config = AppConfig(groups=(GroupTarget(-1001, "A 群"), GroupTarget(-1002, "B 群")))
    credentials = Credentials(api_id=12345, api_hash="secret-hash", phone="+8613800000000")

    store.save_config(config)
    store.save_credentials(credentials)

    assert store.load_config() == config
    assert store.load_credentials() == credentials
    assert "secret-hash" not in (tmp_path / "config.toml").read_text(encoding="utf-8")


def test_legacy_group_defaults_history_to_enabled(tmp_path: Path) -> None:
    paths = ProjectPaths.from_root(tmp_path)
    paths.config.write_text(
        '[[groups]]\nchat_id = -1001\ntitle = "旧频道"\n',
        encoding="utf-8",
    )

    assert ConfigStore(paths).load_config().groups == (
        GroupTarget(-1001, "旧频道", download_history=True),
    )


def test_history_policy_round_trips_explicitly(tmp_path: Path) -> None:
    paths = ProjectPaths.from_root(tmp_path)
    store = ConfigStore(paths)
    config = AppConfig(groups=(GroupTarget(-1001, "频道", False),))

    store.save_config(config)

    assert store.load_config() == config
    assert "download_history = false" in paths.config.read_text(encoding="utf-8")


def test_history_policy_must_be_boolean(tmp_path: Path) -> None:
    paths = ProjectPaths.from_root(tmp_path)
    paths.config.write_text(
        '[[groups]]\nchat_id = -1001\ntitle = "频道"\n'
        'download_history = "yes"\n',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="download_history"):
        ConfigStore(paths).load_config()


def test_qr_credentials_allow_empty_phone_but_phone_login_rejects_it() -> None:
    credentials = Credentials(api_id=12345, api_hash="secret-hash")

    assert credentials.validate_api() is credentials
    assert credentials.validate() is credentials
    with pytest.raises(ValueError, match="手机号"):
        credentials.validate_phone_login()


def test_load_credentials_defaults_missing_phone_to_empty(tmp_path: Path) -> None:
    paths = ProjectPaths.from_root(tmp_path)
    paths.ensure_directories()
    paths.credentials.write_text(
        'api_id = 12345\napi_hash = "secret-hash"\n',
        encoding="utf-8",
    )

    assert ConfigStore(paths).load_credentials() == Credentials(12345, "secret-hash")


def test_require_non_empty_whitelist() -> None:
    with pytest.raises(ValueError, match="至少选择一个群"):
        AppConfig().require_targets()


def test_reloader_keeps_last_valid_config(tmp_path: Path) -> None:
    paths = ProjectPaths.from_root(tmp_path)
    store = ConfigStore(paths)
    valid = AppConfig(groups=(GroupTarget(-1001, "A 群"),))
    store.save_config(valid)
    reloader = store.reloader()
    assert reloader.load_if_changed() == valid

    paths.config.write_text("[[groups]\n", encoding="utf-8")
    assert reloader.load_if_changed() == valid
    assert reloader.last_error is not None


def test_old_config_uses_project_downloads(tmp_path: Path) -> None:
    paths = ProjectPaths.from_root(tmp_path)
    paths.config.write_text("config_poll_seconds = 5\n", encoding="utf-8")

    config = ConfigStore(paths).load_config()

    assert config.download_root is None
    assert effective_download_root(paths, config) == paths.downloads


def test_config_round_trips_external_download_root(tmp_path: Path) -> None:
    paths = ProjectPaths.from_root(tmp_path / "project")
    selected = (tmp_path / "媒体 目录").resolve()
    store = ConfigStore(paths)

    store.save_config(AppConfig(download_root=selected))

    assert store.load_config().download_root == selected
    assert str(selected).replace("\\", "\\\\") in paths.config.read_text(
        encoding="utf-8"
    )


def test_download_root_must_be_a_string(tmp_path: Path) -> None:
    paths = ProjectPaths.from_root(tmp_path)
    paths.config.write_text("download_root = 123\n", encoding="utf-8")

    with pytest.raises(ValueError, match="download_root"):
        ConfigStore(paths).load_config()
