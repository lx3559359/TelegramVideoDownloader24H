import tkinter as tk
from tkinter import ttk

from tg_video_downloader.gui.app import DownloaderApp


def test_account_page_exposes_saved_session_retry_control(tk_root: tk.Tk) -> None:
    tk_root.deiconify()
    app = DownloaderApp.__new__(DownloaderApp)
    ttk.Frame.__init__(app, tk_root, padding=12)
    app.pack(fill="both", expand=True)
    try:
        app._build_account_page()
        tk_root.update()

        assert app.notebook.winfo_viewable()
        assert app.qr_login_button.winfo_viewable()
        assert not app.session_retry_button.winfo_ismapped()
        assert app.session_retry_button.cget("text") == "重试恢复"
        assert app.phone_toggle_button.winfo_viewable()

        app._show_session_retry()
        tk_root.update()

        assert app.session_retry_button.winfo_viewable()
    finally:
        app.destroy()
        tk_root.withdraw()
