"""图像格式决策。**纯函数：不读文件、不 import Pillow、无副作用。**

把「压缩 / 透传 / 转码 / 跳过」的策略集中在一处（对齐 utils/imageFormat.cjs:52 的
resolveImagePlan），这样策略可以脱离真实图片单测，也不会与编解码实现纠缠。

为什么 Python 侧明明用 Pillow（能解 PNG/JPEG/GIF/BMP/WebP/TIFF）却仍沿用
Electron 那套四分动作与跳过码：这套分类**不是**绕开 nativeImage 限制的临时办法，
而是下游行为的契约——印章校验 VLM 的输入形态（哪些图进模型、进的是原字节还是
压缩后的字节）与「未能识别的图片」清单都直接依赖这些动作与跳过码。分类对齐后，
P4 可以照搬参考实现的判定与文案。所以**不要**因为「Pillow 都能解」就把它简化掉。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

# 按魔数识别。EMF/WMF 是矢量格式，Pillow 与浏览器都无法解码，但仍要识别出来，
# 好让跳过原因里带上具体格式名而不是含糊的「未知格式」。
_FORMAT_SIGNATURES: tuple[tuple[str, str, str, Callable[[bytes], bool]], ...] = (
    ("png", "PNG", "image/png", lambda b: len(b) >= 8 and b[:4] == b"\x89PNG"),
    ("jpeg", "JPEG", "image/jpeg", lambda b: len(b) >= 3 and b[0] == 0xFF and b[1] == 0xD8 and b[2] == 0xFF),
    ("gif", "GIF", "image/gif", lambda b: len(b) >= 6 and b[:4] == b"GIF8"),
    ("webp", "WebP", "image/webp",
     lambda b: len(b) >= 12 and b[:4] == b"RIFF" and b[8:12] == b"WEBP"),
    ("bmp", "BMP", "image/bmp", lambda b: len(b) >= 2 and b[:2] == b"BM"),
    ("tiff", "TIFF", "image/tiff",
     lambda b: len(b) >= 4 and ((b[:3] == b"II\x2a") or (b[:4] == b"MM\x00\x2a"))),
    ("emf", "EMF", "image/emf", lambda b: len(b) >= 44 and b[40:44] == b" EMF"),
    ("wmf", "WMF", "image/wmf", lambda b: len(b) >= 4 and b[:4] == b"\xd7\xcd\xc6\x9a"),
)

# Electron 的 nativeImage 只稳定支持 PNG/JPEG；Python 侧用 Pillow，能力更强，
# 但仍保留同一套动作分类（spec §8.3）——印章 VLM 的输入形态与「未能识别的图片」
# 清单都依赖这些动作与跳过码。
NATIVE_DECODABLE_FORMATS = frozenset({"png", "jpeg"})
PASSTHROUGH_FORMATS = frozenset({"gif"})
TRANSCODE_FORMATS = frozenset({"bmp", "webp"})


@dataclass(frozen=True)
class ImageFormat:
    format: str
    label: str
    mime: str


@dataclass(frozen=True)
class ImagePlan:
    action: str                     # compress | passthrough | transcode | skip
    format: str
    label: str
    mime: str
    code: str | None = None         # 仅 skip 时有值
    reason: str | None = None


UNKNOWN_FORMAT = ImageFormat(format="unknown", label="未知格式", mime="")


def detect_image_format(data: bytes) -> ImageFormat:
    payload = data or b""
    for name, label, mime, matches in _FORMAT_SIGNATURES:
        if matches(payload):
            return ImageFormat(format=name, label=label, mime=mime)
    return UNKNOWN_FORMAT


def resolve_image_plan(data: bytes, *, max_passthrough_bytes: int | None = None) -> ImagePlan:
    """单一决策入口（对齐 resolveImagePlan）。"""
    detected = detect_image_format(data)
    limit = max_passthrough_bytes if isinstance(max_passthrough_bytes, int) and max_passthrough_bytes > 0 else None

    if detected.format in NATIVE_DECODABLE_FORMATS:
        return ImagePlan("compress", detected.format, detected.label, detected.mime)
    if detected.format in PASSTHROUGH_FORMATS:
        if limit is not None and len(data or b"") > limit:
            return ImagePlan("skip", detected.format, detected.label, detected.mime,
                             code="PASSTHROUGH_TOO_LARGE",
                             reason=f"{detected.label} 体积超出透传上限")
        return ImagePlan("passthrough", detected.format, detected.label, detected.mime)
    if detected.format in TRANSCODE_FORMATS:
        return ImagePlan("transcode", detected.format, detected.label, detected.mime)
    return ImagePlan("skip", detected.format, detected.label, detected.mime,
                     code="UNSUPPORTED_IMAGE_FORMAT",
                     reason=f"图片格式不支持（{detected.label}）")


def compute_target_size(width: int, height: int, max_edge: int) -> tuple[int, int]:
    """等比缩放到最长边不超过 max_edge；**不放大**（对齐 computeTargetSize）。"""
    safe_width = max(1, int(width or 0))
    safe_height = max(1, int(height or 0))
    edge = int(max_edge or 0)
    longest = max(safe_width, safe_height)
    if edge <= 0 or longest <= edge:
        return safe_width, safe_height
    scale = edge / longest
    return max(1, round(safe_width * scale)), max(1, round(safe_height * scale))
