from datetime import UTC, datetime
from pathlib import Path

from tg_video_downloader.gui.qr_view import (
    draw_qr,
    make_qr_matrix,
    retry_delay,
    seconds_until_expiry,
)


class FakeCanvas:
    def __init__(self) -> None:
        self.deleted: list[str] = []
        self.configured: dict[str, int] = {}
        self.rectangles: list[tuple[tuple[int, int, int, int], dict[str, str]]] = []

    def delete(self, tag: str) -> None:
        self.deleted.append(tag)

    def configure(self, **values: int) -> None:
        self.configured.update(values)

    def create_rectangle(self, *coords: int, **options: str) -> None:
        self.rectangles.append((coords, options))


def test_qr_matrix_and_canvas_render_without_files(tmp_path: Path) -> None:
    before = set(tmp_path.rglob("*"))
    matrix = make_qr_matrix("tg://login?token=secret-token")
    canvas = FakeCanvas()

    draw_qr(canvas, matrix, max_pixels=260)

    assert len(matrix) == len(matrix[0])
    assert any(any(row) for row in matrix)
    assert canvas.deleted == ["all"]
    assert canvas.rectangles
    assert set(tmp_path.rglob("*")) == before


def test_retry_delay_caps_network_errors_and_honors_flood_wait() -> None:
    assert [retry_delay(attempt) for attempt in range(5)] == [2, 5, 10, 20, 30]
    assert retry_delay(9) == 30
    assert retry_delay(0, retry_after=73) == 73


def test_seconds_until_expiry_normalizes_naive_utc() -> None:
    now = datetime(2026, 8, 25, 1, 0, tzinfo=UTC)

    assert seconds_until_expiry(datetime(2026, 8, 25, 1, 0, 5), now=now) == 5
