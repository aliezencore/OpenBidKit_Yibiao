import base64
import io

import pytest
from PIL import Image

from app.infra.parsing.docx_assets import MediaFile
from app.infra.parsing.image_codec import prepare_image
from tests.parsing_fixtures import make_image


def _media(path, *, media_name="media/a.png"):
    data = path.read_bytes()
    import hashlib

    return MediaFile(
        media_name=media_name, data=data,
        content_hash=hashlib.sha256(data).hexdigest(),
        mime_type="application/octet-stream", extension=path.suffix.lower(),
    )


def test_png_is_recompressed_to_a_data_url(tmp_path):
    media = _media(make_image(tmp_path / "a.png", size=(100, 80), fmt="PNG"))

    prepared = prepare_image(media)

    assert prepared.action == "compress"
    assert prepared.data_url.startswith("data:")
    assert ";base64," in prepared.data_url
    assert prepared.width == 100 and prepared.height == 80


def test_transcode_turns_bmp_into_a_model_readable_format(tmp_path):
    media = _media(make_image(tmp_path / "a.bmp", size=(60, 40), fmt="BMP"), media_name="media/a.bmp")

    prepared = prepare_image(media)

    assert prepared.action == "transcode"
    assert prepared.mime in ("image/png", "image/jpeg")
    # 解码回来看得出来，说明确实是可读位图而不是原字节透传
    decoded = Image.open(io.BytesIO(base64.b64decode(prepared.data_url.split(",", 1)[1])))
    assert decoded.size == (60, 40)


def test_gif_is_passed_through_byte_for_byte(tmp_path):
    media = _media(make_image(tmp_path / "a.gif", size=(20, 20), fmt="GIF"), media_name="media/a.gif")

    prepared = prepare_image(media)

    assert prepared.action == "passthrough"
    assert base64.b64decode(prepared.data_url.split(",", 1)[1]) == media.data
    assert prepared.mime == "image/gif"


def test_tiff_is_skipped_with_the_unsupported_code(tmp_path):
    media = _media(make_image(tmp_path / "a.tiff", size=(20, 20), fmt="TIFF"), media_name="media/a.tiff")

    prepared = prepare_image(media)

    assert prepared.action == "skip"
    assert prepared.skip_code == "UNSUPPORTED_IMAGE_FORMAT"
    assert prepared.data_url == ""


def test_oversized_gif_is_skipped_by_the_limit(tmp_path):
    media = _media(make_image(tmp_path / "a.gif", size=(20, 20), fmt="GIF"), media_name="media/a.gif")

    prepared = prepare_image(media, max_passthrough_bytes=8)

    assert prepared.action == "skip"
    assert prepared.skip_code == "PASSTHROUGH_TOO_LARGE"


def test_large_image_is_scaled_down_to_max_edge(tmp_path):
    media = _media(make_image(tmp_path / "big.png", size=(800, 400), fmt="PNG"))

    prepared = prepare_image(media, max_edge=200)

    assert (prepared.width, prepared.height) == (200, 100)


def test_undecodable_png_is_skipped_not_raised(tmp_path):
    """魔数像 PNG 但内容坏掉：必须降级为 skip，不能让单张失败拖垮整批（spec §8.4.3）。"""
    broken = tmp_path / "broken.png"
    broken.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 40)
    media = _media(broken)

    prepared = prepare_image(media)

    assert prepared.action == "skip"
    assert prepared.skip_code == "IMAGE_DECODE_FAILED"
    assert prepared.skip_reason
    assert prepared.data_url == ""


def test_alpha_png_stays_png(tmp_path):
    """带透明通道的图存 JPEG 会丢掉 alpha，必须存 PNG。"""
    path = tmp_path / "alpha.png"
    Image.new("RGBA", (30, 30), (10, 20, 30, 128)).save(path, format="PNG")

    prepared = prepare_image(_media(path))

    assert prepared.mime == "image/png"
