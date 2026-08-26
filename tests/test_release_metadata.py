from pathlib import Path
import tomllib


def test_v033_docs_explain_release_boundaries() -> None:
    root = Path(__file__).resolve().parents[1]
    pyproject = tomllib.loads(
        (root / "pyproject.toml").read_text(encoding="utf-8")
    )
    readme = (root / "README.md").read_text(encoding="utf-8")

    assert pyproject["project"]["version"] == "0.3.3"
    assert "视频检索" in readme
    assert "最多 100" in readme
    assert "不建立本地索引" in readme
    assert "下载选中项" in readme
    assert "500 个候选" in readme
    assert "不会中断当前文件" in readme
    assert "Windows 字节锁" in readme
    assert "更新前正确停止后台" in readme
    assert "弹出月历" in readme
    assert "不限" in readme
    assert "不增加第三方日历依赖" in readme
    assert "健康后台" in readme
    assert "不会重复打开同一会话" in readme
    assert "重试恢复" in readme
