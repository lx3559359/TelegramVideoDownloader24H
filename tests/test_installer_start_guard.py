import importlib.util
from pathlib import Path


def entry_module():
    p=Path(__file__).resolve().parents[1]/'packaging/windows_entry.py'
    spec=importlib.util.spec_from_file_location('windows_entry_test',p)
    module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


def test_no_update_lock_allows_start(tmp_path):
    assert not entry_module().installer_update_in_progress(tmp_path)


def test_active_update_lock_blocks_start(tmp_path):
    from tg_video_downloader.windows import SingleInstance
    lock=tmp_path/'.runtime/installer-update.lock'
    with SingleInstance(lock):
        assert entry_module().installer_update_in_progress(tmp_path)
    assert not entry_module().installer_update_in_progress(tmp_path)
