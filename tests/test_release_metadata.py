from pathlib import Path
import tomllib


def test_v035_docs_explain_caption_boundary_fix() -> None:
    root = Path(__file__).resolve().parents[1]
    pyproject = tomllib.loads(
        (root / "pyproject.toml").read_text(encoding="utf-8")
    )
    readme = (root / "README.md").read_text(encoding="utf-8")

    assert pyproject["project"]["version"] == "0.3.5"
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
    assert "后台运行时复用同一个 Telegram 连接" in readme
    assert "后台停止时仍可直接检索" in readme
    assert "不会并发打开同一会话数据库" in readme
    assert "取消检索不会中断当前下载" in readme
    assert "120 字符边界" in readme
    assert "后台共享检索" in readme
    assert "直接检索" in readme
