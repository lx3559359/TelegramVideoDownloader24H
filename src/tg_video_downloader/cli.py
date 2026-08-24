from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from tg_video_downloader.diagnostics import Doctor
from tg_video_downloader.gateway import TelethonGateway
from tg_video_downloader.paths import ProjectPaths
from tg_video_downloader.service import DownloaderService


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("gui", "service", "doctor"))
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parents[2]
    paths = ProjectPaths.from_root(root)
    if args.command == "gui":
        from tg_video_downloader.gui.app import run_gui

        run_gui(paths)
        return 0
    if args.command == "doctor":
        doctor = Doctor(paths, TelethonGateway)
        report = asyncio.run(doctor.run())
        saved = doctor.save(report)
        counts = report.counts
        print(
            f"自检完成：通过 {counts['pass']}，警告 {counts['warning']}，"
            f"失败 {counts['fail']}"
        )
        print(f"报告：{saved}")
        return report.exit_code
    return asyncio.run(DownloaderService(paths, TelethonGateway).run())
