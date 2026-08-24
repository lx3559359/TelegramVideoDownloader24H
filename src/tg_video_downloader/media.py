from tg_video_downloader.models import MessageInfo


def is_downloadable_video(message: MessageInfo) -> bool:
    if message.is_animated or message.is_round:
        return False
    return message.is_video or bool(
        message.mime_type and message.mime_type.lower().startswith("video/")
    )
