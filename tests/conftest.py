import tkinter as tk

import pytest


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
