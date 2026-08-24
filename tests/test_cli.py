from pathlib import Path

from tg_video_downloader.cli import main
from tg_video_downloader.diagnostics import DiagnosticCheck, DiagnosticReport


def test_doctor_command_prints_summary_and_returns_report_exit_code(
    monkeypatch,
    capsys,
) -> None:
    report = DiagnosticReport(
        generated_at="2026-08-24T12:00:00+00:00",
        checks=(
            DiagnosticCheck("paths", "pass", "ok"),
            DiagnosticCheck("telegram", "warning", "offline"),
        ),
    )

    class FakeDoctor:
        def __init__(self, paths, gateway_factory) -> None:
            self.paths = paths

        async def run(self) -> DiagnosticReport:
            return report

        def save(self, value: DiagnosticReport) -> Path:
            assert value is report
            return self.paths.logs / "diagnostics" / "fake.json"

    monkeypatch.setattr("tg_video_downloader.cli.Doctor", FakeDoctor)

    assert main(["doctor"]) == 1
    output = capsys.readouterr().out
    assert "通过 1" in output
    assert "警告 1" in output
    assert "失败 0" in output
    assert "fake.json" in output
