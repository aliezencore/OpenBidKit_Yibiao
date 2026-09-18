"""Pillow 编解码。**不做策略决策**——动作由 image_plan 决定，这里只执行。

现有 Electron 侧用 nativeImage 压缩、离屏 Chromium 转码；Python 侧一律用 Pillow，
但**动作分类与跳过码保持一致**（spec §8.3），否则印章 VLM 的输入形态会漂移。
"""

from __future__ import annotations

import base64
import io
from dataclasses import dataclass

from PIL import Image

from app.infra.parsing.docx_assets import MediaFile
from app.infra.parsing.image_plan import compute_target_size, resolve_image_plan

JPEG_QUALITY = 85
DEFAULT_MAX_EDGE = 1600


@dataclass(frozen=True)
class PreparedImage:
    data: bytes
    mime: str
    data_url: str
    action: str
    width: int
    height: int
    skip_code: str | None = None
    skip_reason: str | None = None
    detail: str | None = None


def _to_data_url(data: bytes, mime: str) -> str:
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


def _encode(image: Image.Image, target_size: tuple[int, int]) -> tuple[bytes, str, str]:
    """按是否带 alpha 选容器：带 alpha 存 PNG，否则存 JPEG。

    存 JPEG 时丢掉 alpha 会得到黑底，所以必须先判断 `has_alpha` 或调色板透明。
    """
    resized = image if image.size == target_size else image.resize(target_size, Image.LANCZOS)
    has_alpha = resized.mode in ("RGBA", "LA") or (
        resized.mode == "P" and "transparency" in resized.info
    )
    buffer = io.BytesIO()
    if has_alpha:
        resized.convert("RGBA").save(buffer, format="PNG")
        return buffer.getvalue(), "image/png", "PNG"
    resized.convert("RGB").save(buffer, format="JPEG", quality=JPEG_QUALITY)
    return buffer.getvalue(), "image/jpeg", "JPEG"


def prepare_image(
    media: MediaFile,
    *,
    max_edge: int = DEFAULT_MAX_EDGE,
    max_passthrough_bytes: int | None = None,
) -> PreparedImage:
    plan = resolve_image_plan(media.data, max_passthrough_bytes=max_passthrough_bytes)

    if plan.action == "skip":
        return PreparedImage(
            data=b"", mime=plan.mime, data_url="", action="skip",
            width=0, height=0, skip_code=plan.code, skip_reason=plan.reason,
        )

    if plan.action == "passthrough":
        # 原字节直接交给模型（现有实现发现模型能识别静态 GIF），不解码
        return PreparedImage(
            data=media.data, mime=plan.mime,
            data_url=_to_data_url(media.data, plan.mime),
            action="passthrough", width=0, height=0,
        )

    try:
        with Image.open(io.BytesIO(media.data)) as image:
            source_size = image.size
            target = compute_target_size(source_size[0], source_size[1], max_edge)
            data, mime, detail = _encode(image, target)
    except Exception as error:
        # spec §8.4.3：单张失败不连坐。降级为 skip，不抛异常。
        return PreparedImage(
            data=b"", mime=plan.mime, data_url="", action="skip",
            width=0, height=0, skip_code="IMAGE_DECODE_FAILED",
            skip_reason=f"{plan.label} 无法解码",
            detail=f"{type(error).__name__}: {error}",
        )

    return PreparedImage(
        data=data, mime=mime, data_url=_to_data_url(data, mime),
        action=plan.action, width=target[0], height=target[1], detail=detail,
    )
