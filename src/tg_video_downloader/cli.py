from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from tg_video_downloader.gateway import TelethonGateway
from tg_video_downloader.paths import ProjectPaths
from tg_video_downloader.service import DownloaderService


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("gui", "service"))
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parents[2]
    paths = ProjectPaths.from_root(root)
    if args.command == "gui":
        from tg_video_downloader.gui.app import run_gui

        run_gui(paths)
        return 0
    return asyncio.run(DownloaderService(paths, TelethonGateway).run())
