from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_NAMES = ("launch-gui.ps1", "run-supervisor.ps1", "check.ps1")


def test_powershell_scripts_keep_runtime_data_inside_project() -> None:
    for name in SCRIPT_NAMES:
        text = (ROOT / "scripts" / name).read_text(encoding="utf-8")
        assert "$env:TEMP" in text
        assert "$env:TMP" in text
        assert "$env:PIP_CACHE_DIR" in text
        assert "$env:PYTHONPYCACHEPREFIX" in text
        assert ".venv" in text

        lowered = text.lower()
        assert "schtasks" not in lowered
        assert "\\startup" not in lowered
        assert "c:\\" not in lowered
        assert "appdata" not in lowered
        assert "userprofile" not in lowered


def test_supervisor_is_hidden_stoppable_and_bounded() -> None:
    text = (ROOT / "scripts" / "run-supervisor.ps1").read_text(encoding="utf-8")

    assert "stop.flag" in text
    assert "-WindowStyle Hidden" in text
    assert "-Wait" in text
    assert "-PassThru" in text
    assert "300" in text


def test_gui_launcher_does_not_hide_interactive_window() -> None:
    text = (ROOT / "scripts" / "launch-gui.ps1").read_text(encoding="utf-8")
    assert "-WindowStyle Hidden" not in text


def test_double_click_entry_calls_gui_launcher() -> None:
    text = (ROOT / "打开配置器.cmd").read_text(encoding="utf-8")
    assert "scripts\\launch-gui.ps1" in text
