"""Conservative title extraction from a video's own caption."""

import re


def extract_title(caption: str | None) -> str | None:
    if not isinstance(caption, str):
        return None
    candidates = re.findall(r"《([^《》\r\n]{1,120})》", caption)
    if not candidates:
        candidates = re.findall(r"【([^【】\r\n]{1,120})】", caption)
    if not candidates:
        candidates = re.findall(
            r"(?m)^\s*片名\s*[:：]\s*([^\r\n，,；;#《》]+)", caption
        )
    titles = {" ".join(value.split()) for value in candidates}
    titles.discard("")
    if len(titles) != 1:
        return None
    title = titles.pop()
    if title in {"高清", "完整版", "新剧分享", "今日精选", "剧情介绍", "视频", "合集"}:
        return None
    return title if len(title) <= 120 else None
