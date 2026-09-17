import pytest

from app.infra.parsing.image_plan import compute_target_size, detect_image_format, resolve_image_plan

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 32
GIF = b"GIF89a" + b"\x00" * 32
WEBP = b"RIFF\x00\x00\x00\x00WEBP" + b"\x00" * 32
BMP = b"BM" + b"\x00" * 32
TIFF_LE = b"II\x2a\x00" + b"\x00" * 32
TIFF_BE = b"MM\x00\x2a" + b"\x00" * 32
EMF = b"\x00" * 40 + b" EMF" + b"\x00" * 8
WMF = b"\xd7\xcd\xc6\x9a" + b"\x00" * 32


@pytest.mark.parametrize(
    ("data", "expected"),
    [(PNG, "png"), (JPEG, "jpeg"), (GIF, "gif"), (WEBP, "webp"),
     (BMP, "bmp"), (TIFF_LE, "tiff"), (TIFF_BE, "tiff"), (EMF, "emf"), (WMF, "wmf"),
     (b"\x00\x01\x02\x03", "unknown")],
)
def test_detect_image_format(data, expected):
    assert detect_image_format(data).format == expected


def test_detect_reports_label_and_mime():
    detected = detect_image_format(PNG)
    assert detected.label == "PNG"
    assert detected.mime == "image/png"
    assert detect_image_format(b"\x00").mime == ""


@pytest.mark.parametrize(
    ("data", "action", "code"),
    [
        (PNG, "compress", None),
        (JPEG, "compress", None),
        (GIF, "passthrough", None),
        (BMP, "transcode", None),
        (WEBP, "transcode", None),
        (TIFF_LE, "skip", "UNSUPPORTED_IMAGE_FORMAT"),
        (EMF, "skip", "UNSUPPORTED_IMAGE_FORMAT"),
        (WMF, "skip", "UNSUPPORTED_IMAGE_FORMAT"),
        (b"\x00\x01\x02\x03", "skip", "UNSUPPORTED_IMAGE_FORMAT"),
    ],
)
def test_resolve_image_plan_actions(data, action, code):
    plan = resolve_image_plan(data)
    assert plan.action == action
    assert plan.code == code


def test_gif_over_the_limit_is_skipped():
    big = GIF + b"\x00" * 100
    plan = resolve_image_plan(big, max_passthrough_bytes=50)
    assert plan.action == "skip"
    assert plan.code == "PASSTHROUGH_TOO_LARGE"


def test_gif_without_a_limit_is_passed_through():
    assert resolve_image_plan(GIF + b"\x00" * 100).action == "passthrough"


def test_unsupported_skip_carries_a_reason_with_the_label():
    plan = resolve_image_plan(TIFF_LE)
    assert "TIFF" in (plan.reason or "")


def test_compute_target_size_shrinks_to_the_longest_edge():
    assert compute_target_size(1000, 500, 400) == (400, 200)
    assert compute_target_size(500, 1000, 400) == (200, 400)


def test_compute_target_size_never_upscales():
    assert compute_target_size(100, 80, 400) == (100, 80)


def test_compute_target_size_handles_bad_input():
    assert compute_target_size(0, 0, 400) == (1, 1)
    assert compute_target_size(100, 100, 0) == (100, 100)


# --- 以下为 brief 之外的补充用例，钉住 brief 正文（"Decisions I need from me"）
# 与自审清单中点名、但上面用例未覆盖的分支。 ---


def test_plan_carries_the_detected_label_and_mime():
    # 动作表只断言 action/code；这里钉住 detected 的 label/mime 必须随动作一起透出。
    plan = resolve_image_plan(WEBP)
    assert (plan.format, plan.label, plan.mime) == ("webp", "WebP", "image/webp")
    assert resolve_image_plan(TIFF_BE).label == "TIFF"


@pytest.mark.parametrize("limit", [None, 0, -5])
def test_gif_passthrough_limit_only_applies_when_positive(limit):
    # 未传或传 0/负数 = 无上限（对齐 Number.isFinite(limit) && limit > 0）。
    plan = resolve_image_plan(GIF + b"\x00" * 100, max_passthrough_bytes=limit)
    assert plan.action == "passthrough"
    assert plan.code is None


def test_compute_target_size_scales_both_axes():
    assert compute_target_size(1000, 800, 400) == (400, 320)


def test_compute_target_size_keeps_an_exact_fit_unchanged():
    assert compute_target_size(1000, 400, 1000) == (1000, 400)


def test_compute_target_size_rounds_half_up_like_the_reference():
    """裁定 R17：`.5` 临界必须 half-up——这是对齐参考 `Math.round` 的钉子。"""
    # 8000 → 4000 的缩放系数恰为 0.5：6001×0.5 = 3000.5 → 3001（half-up）
    assert compute_target_size(6001, 8000, 4000) == (3001, 4000)


def test_compute_target_size_at_the_limit_does_not_shrink():
    """裁定 R18：最长边恰好等于上限时不缩（`longest <= edge` 的含等号语义）。"""
    assert compute_target_size(400, 200, 400) == (400, 200)
