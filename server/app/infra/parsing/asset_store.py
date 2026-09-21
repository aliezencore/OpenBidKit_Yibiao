"""资产落盘与按 content_hash 去重（spec §8.3、§8.4.1）。

去重语义：同一 hash 的媒体**只落盘一份、只准备一次**，所有出现位置挂在同一份上
（现有 applyContentOutcomeToSiblings 的同一约定）——P4 的印章检查据此让同一张图
的所有位置共享跳过/失败结果，避免同一张图被反复上传。

落盘布局：`<data_root>/projects/<project_id>/assets/<content_hash>.<ext>`。
`project_id` 复用 `path_safety.validate_project_id` 的同一道净化守卫（P2a 终审 F1，
不在这里另写一份。

R24 — 出现位置的 media_name 与 word/media 条目**不保证一一对应**：
正文/页眉的嵌套路径、`mc:AlternateContent` 让同一 rId 产生两条 occurrence、
关系表指向 zip 里不存在的目标，都会让某条 occurrence 找不到对应的媒体文件。
**决定**：匹配不到的出现位置**不**挂到任何资产上，也**不**抛异常——一份能解析的
docx 不该因为一条悬空引用就整体失败。这不是丢信息：`extract_docx_visual_assets()`
本来就同时返回 `occurrences` 与 `media_files`，需要发现未匹配项的下游可以从那里取。
"""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

from app.infra.parsing.docx_assets import AssetOccurrence, MediaFile, extract_docx_visual_assets
from app.infra.parsing.image_codec import DEFAULT_MAX_EDGE, PreparedImage, prepare_image
from app.infra.parsing.path_safety import validate_project_id

# target 前缀：markdown 里图片占位的相对路径形态（P2a↔P2b 接线，裁定 R-DW-1）。
ASSET_TARGET_PREFIX = "assets/"
_ASSET_HASH_RE = re.compile(r"^[0-9a-f]{64}$")


def asset_target(data: bytes) -> str:
    """图片字节 → markdown 占位 target：`assets/<sha256>`。

    与 `list_media_files` 的 `content_hash` **同一算法、同一输入**（都是
    `sha256` over `word/media/*` 的字节），所以这个 target 与 `save_docx_assets`
    落盘的文件名 `<hash>.<ext>` 是**按构造对齐**的，不需要任何 rId→hash 映射。

    **刻意不带扩展名**（裁定 R-DW-1）：落盘扩展名取自 prep 结果
    （`_asset_extension`：`compress`/`transcode` 可能把容器从 PNG 换成 JPEG），
    markdown 生成侧无法在不重复跑一遍 Pillow 的前提下预知它。target 的职责只是
    「标识同一份资产」，哈希即身份；解析交给 `resolve_asset_path`。

    空字节不特判：伪资产（`data=b""`）由上游 `list_media_files` 的布局校验拦掉
    （P2b 的 F1），这里再加一层会让两处规则漂移。
    """
    return f"{ASSET_TARGET_PREFIX}{hashlib.sha256(data).hexdigest()}"


def resolve_asset_path(*, data_root: Path, project_id: str,
                       target: str) -> Path | None:
    """把 markdown 里的图片 target 解析回磁盘文件；解析不了返回 `None`。

    **返回 None 而不是抛**：调用方（P3c 的逐行图片抽取）遇到外来 scheme
    （`yibiao-asset://` / `http(s)://` / `data:`）或不存在资产时应该**跳过那一行**，
    而不是让整个文件的图片段失败。

    接受范围**只有** `assets/<64 位小写十六进制>`——哈希正则本身就是目录穿越的
    屏障（文件名里不可能有分隔符），无需再做包含判定。扩展名按
    `assets_dir.glob("<hash>.*")` 解析（落盘时才确定），**唯一命中**才返回。
    """
    validate_project_id(project_id)
    if not isinstance(target, str) or not target.startswith(ASSET_TARGET_PREFIX):
        return None
    digest = target[len(ASSET_TARGET_PREFIX):]
    if not _ASSET_HASH_RE.match(digest):
        return None
    assets_dir = Path(data_root) / "projects" / project_id / "assets"
    matches = sorted(assets_dir.glob(f"{digest}.*"))
    return matches[0] if len(matches) == 1 else None


@dataclass(frozen=True)
class StoredAsset:
    content_hash: str
    path: str
    mime: str
    extension: str
    size_bytes: int
    occurrences: list[AssetOccurrence]
    prepared: PreparedImage


def _asset_extension(media: MediaFile, prepared: PreparedImage) -> str:
    """落盘扩展名取自**准备结果**：转码后的容器才是文件的真实类型。

    `compress`/`transcode` 后容器可能变了（PNG→JPEG），扩展名必须跟着
    `prepared.mime` 走；只有 `skip` 才退回原图的 `media.extension`（原图要留在磁盘上，
    P4 的「未能识别的图片」清单才能给用户看）。
    """
    if prepared.action in ("compress", "transcode") and prepared.mime:
        return ".png" if prepared.mime == "image/png" else ".jpg"
    return media.extension or ".bin"


def save_docx_assets(
    *,
    data_root: Path,
    project_id: str,
    docx_path: Path,
    max_edge: int = DEFAULT_MAX_EDGE,
    max_passthrough_bytes: int | None = None,
) -> list[StoredAsset]:
    validate_project_id(project_id)
    data_root = Path(data_root)
    docx_path = Path(docx_path)

    extracted = extract_docx_visual_assets(docx_path)

    # 去重：按 content_hash 归组，保留媒体本体与全部出现位置。
    # R23 — 计划 Step 3 写的是 O(出现数 × 媒体数) 的嵌套扫描；`media_name` 在媒体清单里
    # 唯一，这里先建 `{media_name: content_hash}` 的查找字典、再单趟遍历出现位置，
    # 代价降到 O(出现数 + 媒体数)。行为等价（见自审）。
    grouped: dict[str, tuple[MediaFile, list[AssetOccurrence]]] = {}
    media_name_to_hash: dict[str, str] = {}
    for media in extracted.media_files:
        if media.content_hash not in grouped:
            grouped[media.content_hash] = (media, [])
        media_name_to_hash[media.media_name] = media.content_hash

    # R24 — 匹配不到的出现位置（见模块 docstring）不挂资产、不抛异常。
    for occurrence in extracted.occurrences:
        content_hash = media_name_to_hash.get(occurrence.media_name)
        if content_hash is not None:
            grouped[content_hash][1].append(occurrence)

    assets_dir = data_root / "projects" / project_id / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)

    saved: list[StoredAsset] = []
    for content_hash, (media, occurrences) in grouped.items():
        prepared = prepare_image(
            media, max_edge=max_edge, max_passthrough_bytes=max_passthrough_bytes
        )
        payload = prepared.data if prepared.action != "skip" else media.data
        extension = _asset_extension(media, prepared)
        target = assets_dir / f"{content_hash}{extension}"
        # 原子落盘：先写同目录临时文件，再 os.replace 覆盖（同卷移动在 Windows 上原子）。
        # 中途失败不会在盘上留下**截断**的资产，P4 才不会把半张图喂给视觉模型（F3）。
        _atomic_write_bytes(target, payload)
        saved.append(StoredAsset(
            content_hash=content_hash,
            path=str(target),
            mime=prepared.mime or media.mime_type,
            extension=extension,
            size_bytes=len(payload),
            occurrences=occurrences,
            prepared=prepared,
        ))
    return saved


def _atomic_write_bytes(target: Path, payload: bytes) -> None:
    """写临时文件后 `os.replace` 到最终路径；失败时清理临时文件，绝不留下残骸。"""
    target = Path(target)
    fd, tmp_name = tempfile.mkstemp(
        dir=str(target.parent), prefix=f".{target.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
        os.replace(tmp_name, target)
    except BaseException:
        try:
            os.remove(tmp_name)
        except OSError:
            pass
        raise
