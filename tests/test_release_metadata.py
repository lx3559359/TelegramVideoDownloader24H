from pathlib import Path
import tomllib


def test_v030_docs_explain_selective_download_boundaries() -> None:
    root = Path(__file__).resolve().parents[1]
    pyproject = tomllib.loads(
        (root / "pyproject.toml").read_text(encoding="utf-8")
    )
    readme = (root / "README.md").read_text(encoding="utf-8")

    assert pyproject["project"]["version"] == "0.3.0"
    assert "视频检索" in readme
    assert "最多 100" in readme
    assert "不建立本地索引" in readme
    assert "下载选中项" in readme
    assert "500 个候选" in readme
    assert "不会中断当前文件" in readme
