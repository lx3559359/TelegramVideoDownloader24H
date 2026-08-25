from __future__ import annotations

from datetime import UTC, datetime
from math import ceil
from typing import Any

import qrcode
from qrcode.constants import ERROR_CORRECT_M


QrMatrix = tuple[tuple[bool, ...], ...]


def make_qr_matrix(payload: str) -> QrMatrix:
    code = qrcode.QRCode(
        version=None,
        error_correction=ERROR_CORRECT_M,
        box_size=1,
        border=4,
    )
    code.add_data(payload)
    code.make(fit=True)
    return tuple(tuple(bool(cell) for cell in row) for row in code.get_matrix())


def draw_qr(canvas: Any, matrix: QrMatrix, *, max_pixels: int = 260) -> None:
    modules = len(matrix)
    if modules == 0 or any(len(row) != modules for row in matrix):
        raise ValueError("二维码矩阵必须为非空正方形")
    cell = max(1, max_pixels // modules)
    size = cell * modules
    canvas.delete("all")
    canvas.configure(width=size, height=size)
    canvas.create_rectangle(0, 0, size, size, fill="white", outline="")
    for row_index, row in enumerate(matrix):
        for column_index, dark in enumerate(row):
            if not dark:
                continue
            x1 = column_index * cell
            y1 = row_index * cell
            canvas.create_rectangle(
                x1,
                y1,
                x1 + cell,
                y1 + cell,
                fill="black",
                outline="",
            )


def retry_delay(attempt: int, *, retry_after: int | None = None) -> int:
    if retry_after is not None:
        return max(1, retry_after)
    return (2, 5, 10, 20, 30)[min(max(attempt, 0), 4)]


def seconds_until_expiry(
    expires_at: datetime,
    *,
    now: datetime | None = None,
) -> int:
    expires = (
        expires_at
        if expires_at.tzinfo is not None
        else expires_at.replace(tzinfo=UTC)
    )
    current = now or datetime.now(UTC)
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    remaining = expires.astimezone(UTC) - current.astimezone(UTC)
    return max(0, ceil(remaining.total_seconds()))
