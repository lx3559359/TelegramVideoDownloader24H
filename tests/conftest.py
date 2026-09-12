import tkinter as tk

import pytest


@pytest.fixture(autouse=True)
def isolate_online_licensing(monkeypatch):
    """Existing tests must never register real devices or contact licensing."""
    from tg_video_downloader import licensing

    class TestGate:
        status = None

        async def ensure(self):
            pass

        def require_cached(self):
            pass

    monkeypatch.setattr(licensing, 'create_license_gate', TestGate)


@pytest.fixture(scope="session")
def tk_root():
    root = tk.Tk()
    root.geometry("900x720")
    root.update_idletasks()
    try:
        yield root
    finally:
        root.update_idletasks()
        root.destroy()
