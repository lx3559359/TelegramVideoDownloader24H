import io
import json
import hashlib

import pytest
from PIL import Image

from tg_video_downloader import sponsorship as sponsor


def config(**changes):
    return dict(enabled=False, recipient="", contact="", month=1590, year=5990,
                permanent=9990, image_version="", **changes)


def test_disabled_config_has_no_payment_entry():
    assert not sponsor.parse_config(config()).enabled


@pytest.mark.parametrize("field,value", [("month", True), ("year", 0),
                                         ("permanent", 1.5), ("enabled", "yes")])
def test_config_rejects_invalid_types(field, value):
    payload = config()
    payload[field] = value
    with pytest.raises(sponsor.SponsorError):
        sponsor.parse_config(payload)


def test_enabled_config_requires_recipient_contact_and_image():
    payload = config()
    payload["enabled"] = True
    with pytest.raises(sponsor.SponsorError):
        sponsor.parse_config(payload)


@pytest.mark.parametrize("short", ["", "ABC", "A" * 7, "A" * 8, "A" * 9, "A" * 11, "OOOOO00000", "<script>!!"])
def test_device_info_rejects_invalid_short_id(short):
    with pytest.raises(sponsor.SponsorError):
        sponsor.parse_device_info({"device": "a" * 64, "short_id": short}, "a" * 64)


@pytest.mark.parametrize("short", ["ABC234", "ABCDE23456"])
def test_device_info_accepts_new_six_and_legacy_ten(short):
    assert sponsor.parse_device_info({"device": "a" * 64, "short_id": short}, "a" * 64) == short


def test_device_info_is_bound_to_requested_hash():
    payload = {"device": "a" * 64, "short_id": "ABCDE23456"}
    assert sponsor.parse_device_info(payload, "a" * 64) == "ABCDE23456"
    with pytest.raises(sponsor.SponsorError):
        sponsor.parse_device_info(payload, "b" * 64)


def picture(size=(40, 40), kind="PNG"):
    stream = io.BytesIO()
    Image.new("RGB", size, "white").save(stream, format=kind)
    return stream.getvalue()


@pytest.mark.parametrize("data", [b"<svg></svg>", b"x" * (2 * 1024**2 + 1),
                                   picture((2049, 1)), picture(kind="GIF")],
                         ids=["svg", "bytes-limit", "pixels-limit", "gif"])
def test_image_limits_and_actual_format(data):
    with pytest.raises(sponsor.SponsorError):
        sponsor.decode_image(data)


@pytest.mark.parametrize("kind", ["PNG", "JPEG"])
def test_image_is_decoded_and_detached(kind):
    assert sponsor.decode_image(picture(kind=kind)).size == (40, 40)


def test_redirect_is_rejected_before_following():
    with pytest.raises(sponsor.SponsorError):
        sponsor.NoRedirect().redirect_request(None, None, 302, "redirect", {},
                                               "https://attacker.invalid")


def test_fetch_disabled_does_not_download_image():
    paths = []
    def transport(path, payload=None, limit=8192):
        paths.append(path)
        return json.dumps(config()).encode()
    result = sponsor.fetch_sponsor(transport=transport)
    assert result.image is None
    assert paths == ["/v1/sponsor"]


def test_transport_bounds_reads_and_uses_https(monkeypatch):
    calls = []
    class Response(io.BytesIO):
        pass
    class Opener:
        def open(self, request, timeout):
            calls.append((request.full_url, timeout))
            return Response(b"x" * 9000)
    monkeypatch.setattr(sponsor, "build_opener", lambda *args: Opener())
    with pytest.raises(sponsor.SponsorError):
        sponsor.request_bytes("/v1/sponsor")
    assert calls == [("https://license.cqtcshequ.com/v1/sponsor", 10)]


def test_transport_rejects_arbitrary_path():
    with pytest.raises(sponsor.SponsorError):
        sponsor.request_bytes("https://attacker.invalid")


def test_changed_image_cannot_use_stale_recipient():
    payload = config()
    payload.update(enabled=True, recipient="测试", contact="测试联系",
                   image_version="a" * 64)
    def transport(path, payload_arg=None, limit=8192):
        return json.dumps(payload).encode() if path == "/v1/sponsor" else picture()
    with pytest.raises(sponsor.SponsorError, match="已更新"):
        sponsor.fetch_sponsor(transport=transport)


def test_enabled_display_uses_matching_image():
    data = picture()
    payload = config()
    payload.update(enabled=True, recipient="测试", contact="测试联系",
                   image_version=hashlib.sha256(data).hexdigest())
    def transport(path, payload_arg=None, limit=8192):
        return json.dumps(payload).encode() if path == "/v1/sponsor" else data
    result = sponsor.fetch_sponsor(transport=transport)
    assert result.image.size == (40, 40)
    assert result.config.month == 1590
