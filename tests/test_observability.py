import logging
from pathlib import Path

from tg_video_downloader.observability import HeartbeatWriter, SecretRedactionFilter


def test_log_filter_redacts_message_and_arguments() -> None:
    record = logging.LogRecord(
        "test",
        logging.INFO,
        __file__,
        1,
        "hash=%s code=%s",
        ("secret-hash", "123456"),
        None,
    )

    assert SecretRedactionFilter(("secret-hash", "123456")).filter(record)
    assert record.getMessage() == "hash=*** code=***"
    assert record.args == ()


def test_heartbeat_is_atomic_utf8_json(tmp_path: Path) -> None:
    path = tmp_path / ".runtime" / "heartbeat.json"
    writer = HeartbeatWriter(path)
    snapshot = {"status": "running", "message": "正在下载"}

    writer.write(snapshot)

    assert writer.read() == snapshot
    assert not path.with_name("heartbeat.json.new").exists()
    assert "正在下载" in path.read_text(encoding="utf-8")
