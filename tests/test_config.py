from pathlib import Path

import pytest

from tg_video_downloader.config import ConfigStore
from tg_video_downloader.models import AppConfig, Credentials, GroupTarget
from tg_video_downloader.paths import ProjectPaths


def test_round_trip_config_and_credentials(tmp_path: Path) -> None:
    store = ConfigStore(ProjectPaths.from_root(tmp_path))
    config = AppConfig(groups=(GroupTarget(-1001, "A 群"), GroupTarget(-1002, "B 群")))
    credentials = Credentials(api_id=12345, api_hash="secret-hash", phone="+8613800000000")

    store.save_config(config)
    store.save_credentials(credentials)

    assert store.load_config() == config
    assert store.load_credentials() == credentials
    assert "secret-hash" not in (tmp_path / "config.toml").read_text(encoding="utf-8")


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
