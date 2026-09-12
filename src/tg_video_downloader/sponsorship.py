"""Bounded, fixed-origin display data. This module never verifies payments."""
from __future__ import annotations

import io
import json
import re
import ssl
from dataclasses import dataclass
from urllib.error import URLError
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener

from PIL import Image, UnidentifiedImageError

ORIGIN = "https://license.cqtcshequ.com"
MAX_IMAGE_BYTES = 2 * 1024**2
SHORT_ID = re.compile(r"[A-HJ-NP-Z2-9]{10}")
PATHS = {"/v1/device-info", "/v1/sponsor", "/v1/sponsor/image"}


class SponsorError(ValueError):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise SponsorError("赞助服务地址发生变化，请联系管理员")


def request_bytes(path: str, payload: dict | None = None, limit: int = 8192) -> bytes:
    if path not in PATHS:
        raise SponsorError("赞助服务地址无效")
    try:
        opener = build_opener(HTTPSHandler(context=ssl.create_default_context()), NoRedirect())
        request = Request(ORIGIN + path,
                          data=None if payload is None else json.dumps(payload).encode(),
                          headers={"Content-Type": "application/json"})
        with opener.open(request, timeout=10) as response:
            data = response.read(limit + 1)
        if len(data) > limit:
            raise SponsorError("赞助服务返回内容过大")
        return data
    except (OSError, URLError) as error:
        raise SponsorError("无法安全连接赞助服务，请稍后刷新") from None


@dataclass(frozen=True)
class SponsorConfig:
    enabled: bool
    recipient: str
    contact: str
    month: int
    year: int
    permanent: int
    image_version: str


@dataclass(frozen=True)
class SponsorDisplay:
    config: SponsorConfig
    image: Image.Image | None


def parse_config(payload: object) -> SponsorConfig:
    try:
        if not isinstance(payload, dict) or type(payload["enabled"]) is not bool:
            raise ValueError
        for name in ("month", "year", "permanent"):
            if type(payload[name]) is not int or not 0 < payload[name] <= 100_000_000:
                raise ValueError
        for name, maximum in (("recipient", 100), ("contact", 500), ("image_version", 64)):
            if not isinstance(payload[name], str) or len(payload[name]) > maximum:
                raise ValueError
        if payload["enabled"] and (
            not payload["recipient"].strip() or not payload["contact"].strip()
            or not re.fullmatch(r"[a-f0-9]{64}", payload["image_version"])
        ):
            raise ValueError
        return SponsorConfig(**{name: payload[name] for name in SponsorConfig.__dataclass_fields__})
    except (KeyError, TypeError, ValueError):
        raise SponsorError("赞助设置不完整或无效，请联系管理员") from None


def parse_device_info(payload: object, device: str) -> str:
    if (not re.fullmatch(r"[a-f0-9]{64}", device)
        or not isinstance(payload, dict) or payload.get("device") != device
        or not isinstance(payload.get("short_id"), str)
        or not SHORT_ID.fullmatch(payload["short_id"])):
        raise SponsorError("设备识别号响应无效")
    return payload["short_id"]


def fetch_device_info(device: str, *, transport=request_bytes) -> str:
    try:
        return parse_device_info(json.loads(transport("/v1/device-info", {"device": device})), device)
    except (UnicodeError, json.JSONDecodeError):
        raise SponsorError("设备识别号响应无效") from None


def decode_image(data: bytes) -> Image.Image:
    if len(data) > MAX_IMAGE_BYTES:
        raise SponsorError("二维码图片过大")
    try:
        with Image.open(io.BytesIO(data)) as picture:
            if (picture.format not in {"PNG", "JPEG"} or picture.width > 2048
                or picture.height > 2048 or picture.width < 1 or picture.height < 1):
                raise SponsorError("二维码图片格式或尺寸不支持")
            picture.load()
            return picture.convert("RGB")
    except (OSError, ValueError, Image.DecompressionBombError, UnidentifiedImageError):
        raise SponsorError("二维码图片无效") from None


def fetch_sponsor(*, transport=request_bytes) -> SponsorDisplay:
    try:
        config = parse_config(json.loads(transport("/v1/sponsor")))
        picture = None
        if config.enabled:
            data = transport("/v1/sponsor/image", limit=MAX_IMAGE_BYTES)
            # A concurrent replacement must not combine a new QR with an old recipient.
            import hashlib
            if hashlib.sha256(data).hexdigest() != config.image_version:
                raise SponsorError("二维码已更新，请重新刷新后扫码")
            picture = decode_image(data)
        return SponsorDisplay(config, picture)
    except (UnicodeError, json.JSONDecodeError):
        raise SponsorError("赞助设置响应无效") from None
