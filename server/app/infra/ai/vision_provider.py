"""印章视觉的在线 VLM provider（P4c-2）。

逐字移植 `client/electron/services/subjectVisionProvider.cjs` 的 `inspect` 协议：
预检（不可读格式跳过且不连坐）→ 内容识别（page-scan 每批 1 张、独立小图每批 4 张、
批次失败降级逐张重试、同 contentHash 结果回填兄弟位置）→ 近名二次确认（2 票多数
表决，归一化+去数字噪声分组，平票取最长）→ 角色判断（纯文本、批 8、按
contentHash+nearbyContextHash 缓存）→ 结果组装（skipped/failed/inspected 三态）。

**平台适配（登记，非保真偏离）**：
- 图片以 OpenAI 视觉格式（data URL base64）进 messages——参考的 `local_image` 部件
  是 Electron IPC 桥的私有格式；`use_vision=True` 走服务端 vision 模型配置；
- 预检用 Pillow（参考用 Electron nativeImage），不可读格式跳过语义一致；
- 多模态开关由调用方经 `multimodal_enabled` 传入（参考读 aiService.getConfig）。
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from app.domain.rejection.subject_body import (
    is_near_match_subject_name,
    matches_allowed_subject,
)

STANDALONE_BATCH_SIZE = 4     # 参考 :10
ROLE_BATCH_SIZE = 8           # 参考 :11
# 边缘样本二次确认的额外请求次数：连同首次识别共 3 票多数表决（参考 :12-13）
SEAL_TEXT_CONFIRM_ROUNDS = 2

# 明确不属于盖章区域的图片类型：已识别成功，只是不该做盖章判断（参考 :99-101）
NON_SEAL_IMAGE_TYPES = ["product-screenshot", "diagram", "photo"]

_XML_RESIDUE_RE = re.compile(r"w:rsid|w:val|posOffset|effectExtent|paraId|textId|<wp:|</?w:", re.I)
_CJK_RE = re.compile(r"[\u4e00-\u9fa5]")


def hash_text(content: Any) -> str:
    """参考 :18-20：sha256 前 16 位。"""
    return hashlib.sha256(str(content or "").encode("utf-8")).hexdigest()[:16]


def chunk_items(items: Sequence[Any], batch_size: int) -> list[list[Any]]:
    result = []
    source = list(items)
    for index in range(0, len(source), batch_size):
        result.append(source[index:index + batch_size])
    return result


def sanitize_asset_context(text: Any) -> str:
    """参考 :87-98：XML 残渣与无 CJK 的「上下文」当作没有上下文（线上实测污染上下文
    会让模型退化输出率显著上升）。"""
    value = str(text or "").strip()
    if not value:
        return ""
    if _XML_RESIDUE_RE.search(value):
        return ""
    if not _CJK_RE.search(value):
        return ""
    return value


def is_seal_area_content(content: Mapping[str, Any] | None) -> bool:
    return not (content and content.get("imageType") in NON_SEAL_IMAGE_TYPES)


def safe_response_preview(payload: Any) -> str:
    try:
        return str(json.dumps(payload, ensure_ascii=False))[:500]
    except (TypeError, ValueError):
        return ""


def create_shape_mismatch_error(label: str, expected: int, actual: int,
                                payload: Any) -> RuntimeError:
    """参考 :113-121：思考型模型偶发把回答退化成 {} 或条目数不足——照单全收会把
    每张图判成「无印章」（假阴性伪装成成功）。"""
    error = RuntimeError(
        f"{label}结果与图片数量不匹配（期望 {expected} 条，实际 {actual} 条）")
    error.code = "VISION_RESPONSE_SHAPE_MISMATCH"  # type: ignore[attr-defined]
    error.raw_response = safe_response_preview(payload)  # type: ignore[attr-defined]
    return error


def pick_majority_seal_text(readings: Sequence[Any]) -> str:
    """参考 :122-140：按「归一化 + 去数字噪声」规范形分组投票；平票取组内最长原始转写。"""
    groups: dict[str, list[str]] = {}
    for text in readings:
        value = str(text or "").strip()
        if not value:
            continue
        key = re.sub(r"\d+", "", normalize_subject_name_text(value))
        groups.setdefault(key, []).append(value)
    best_group: list[str] | None = None
    for group in groups.values():
        if (best_group is None or len(group) > len(best_group)
                or (len(group) == len(best_group) and len(group[0]) > len(best_group[0]))):
            best_group = group
    return best_group[0] if best_group else ""


def normalize_subject_name_text(value: Any) -> str:
    from app.domain.rejection.normalize import normalize_subject_name

    return normalize_subject_name(value)


def build_content_prompt(batch: Sequence[Mapping[str, Any]]) -> str:
    """参考 :22-34（逐字）。"""
    descriptions = [
        {"image_index": index + 1,
         # 优先取干净的那个：章节标题与附近文本任一可能是历史遗留的 XML 残渣
         "context": (sanitize_asset_context(entry["asset"].get("sectionTitle"))
                     or sanitize_asset_context(entry["asset"].get("nearbyText")))}
        for index, entry in enumerate(batch)]
    indices = "、".join(str(index + 1) for index in range(len(batch)))
    return f"""【印章与内容识别】
请逐张识别图片：是否包含印章/公章、印章上的机构名称文字（尽量完整转写）、图片内容类型（document=公文/函件/证书、product-screenshot=产品截图、diagram=拓扑/图表、photo=照片、unknown）。
只有印章足够清晰、文字可辨认时才填写 seal_text；没有印章时 has_seal 为 false。
第 {indices} 张图片对应的上下文：{json.dumps(descriptions, ensure_ascii=False)}

JSON 格式：{{"images":[{{"has_seal":true,"seal_text":"印章上的机构名称","image_type":"document"}}]}}"""


def build_role_prompt(*, official_name: Any, aliases: Sequence[Any],
                      positions: Sequence[Mapping[str, Any]]) -> str:
    """参考 :36-60（逐字）。"""
    position_text = [
        {"position": index + 1,
         "seal_text": entry["content"].get("sealText") or "",
         "image_type": entry["content"].get("imageType") or "unknown",
         "context": sanitize_asset_context(entry["asset"].get("nearbyText")),
         "section": sanitize_asset_context(entry["asset"].get("sectionTitle"))}
        for index, entry in enumerate(positions)]
    return f"""【盖章方角色判断】
正式投标主体：{official_name}
允许简称：{"、".join(aliases) or "（无）"}

以下是若干盖章/图片位置。请判断每个位置的页面角色 page_role 与盖章方角色 seal_role：
- bidder：该位置语义上应由投标方盖章/署名（如投标函、承诺函、报价表、授权委托书的投标方落款）；
- third-party：该位置合法属于第三方（如制造商授权函的制造商章、认证证书的认证机构章、客户证明的客户章）；
- unclear：无法确定。

同时给出 detected_subject（印章识别出的机构名称）、name_confidence 与 role_confidence（0~1）。

待判断位置：
{json.dumps(position_text, ensure_ascii=False, indent=2)}

JSON 格式：{{"positions":[{{"page_role":"bidder","seal_role":"bidder","detected_subject":"","name_confidence":0.9,"role_confidence":0.9,"reason":"判断原因"}}]}}"""


def normalize_role_item(item: Any) -> dict[str, Any]:
    """参考 :62-85：snake/camel 兼容 + 枚举兜底 + 置信度夹取。"""
    source = item or {}
    page_role = source.get("page_role", source.get("pageRole"))
    seal_role = source.get("seal_role", source.get("sealRole"))
    detected = source.get("detected_subject", source.get("detectedSubject"))
    name_confidence = source.get("name_confidence", source.get("nameConfidence"))
    role_confidence = source.get("role_confidence", source.get("roleConfidence"))
    roles = ("bidder", "third-party", "unclear")

    def _clamp(value: Any) -> float:
        try:
            number = float(value or 0)
        except (TypeError, ValueError):
            return 0.0
        if number != number:
            return 0.0
        return max(0.0, min(1.0, number))

    return {
        "pageRole": page_role if page_role in roles else "unclear",
        "sealRole": seal_role if seal_role in roles else "unclear",
        "detectedSubject": str(detected or ""),
        "nameConfidence": _clamp(name_confidence),
        "roleConfidence": _clamp(role_confidence),
        "reason": str(source.get("reason") or ""),
    }


class OnlineVlmProvider:
    """参考 `createOnlineVlmProvider` :142-516。

    `image_reader(absolute_path)` → `{"readable": bool, "format": str, "label": str}`
    （服务端用 Pillow 实现）；`image_loader(absolute_path, mime)` → data URL（base64）。
    """

    def __init__(self, ai: Any, *, multimodal_enabled: bool = True,
                 image_reader: Callable[[str], Mapping[str, Any]] | None = None,
                 image_loader: Callable[[str, str], str] | None = None) -> None:
        self._ai = ai
        self._multimodal_enabled = multimodal_enabled
        self._image_reader = image_reader or _default_image_reader
        self._image_loader = image_loader or _default_image_loader

    async def _run_json(self, messages: list[dict[str, Any]], label: str,
                        failure_message: str, *, use_vision: bool = False) -> dict[str, Any]:
        # 参考 runJson :143-153：走 collectJsonResponse（含两轮修复）；
        # 服务端签名只收 messages（progressLabel/failureMessage 走日志，暂未接）。
        return await self._ai.collect_json_response(messages, use_vision=use_vision)

    async def inspect(self, request: Mapping[str, Any],
                      hooks: Mapping[str, Any] | None = None) -> dict[str, Any]:
        hooks = hooks or {}
        emit_progress = hooks.get("onProgress") if isinstance(hooks.get("onProgress"), Callable) else None
        emit_log = hooks.get("onLog") if isinstance(hooks.get("onLog"), Callable) else None
        emit_progress = emit_progress or (lambda event: None)
        emit_log = emit_log or (lambda event: None)

        if not self._multimodal_enabled:
            return {"providerStatus": "unavailable",
                    "message": "当前文本模型未开启多模态支持，请到设置页的文本模型中开启",
                    "assets": []}

        assets = request.get("assets") if isinstance(request.get("assets"), list) else []
        by_asset_id: dict[str, dict[str, Any]] = {
            str(asset["assetId"]): {"asset": asset, "content": None,
                                    "error": None, "skipReason": None,
                                    "format": "", "label": "", "skipKind": ""}
            for asset in assets}

        # 同一 contentHash 的所有出现位置（代表项 + 兄弟项），用于结果对称回传（spec §4.3(a1)）
        entries_by_hash: dict[str, list[dict[str, Any]]] = {}
        for entry in by_asset_id.values():
            entries_by_hash.setdefault(str(entry["asset"].get("contentHash")), []).append(entry)

        def apply_content_outcome_to_siblings(source_entry: dict[str, Any]) -> None:
            for sibling in entries_by_hash.get(str(source_entry["asset"].get("contentHash")), []):
                if sibling is source_entry:
                    continue
                if sibling.get("skipReason") or sibling.get("content"):
                    continue
                if source_entry.get("error"):
                    sibling["error"] = source_entry["error"]
                    sibling["format"] = source_entry["format"]
                    sibling["label"] = source_entry["label"]
                elif source_entry.get("skipReason"):
                    sibling["skipReason"] = source_entry["skipReason"]
                    sibling["format"] = source_entry["format"]
                    sibling["label"] = source_entry["label"]
                    sibling["skipKind"] = source_entry["skipKind"]

        # 预检：解码不了的格式跳过，且不连坐同批其它图片（参考 :208-232）
        pending_entries: list[dict[str, Any]] = []
        seen_hashes: set[str] = set()
        skipped_count = 0
        for entry in by_asset_id.values():
            content_hash = str(entry["asset"].get("contentHash"))
            if content_hash in seen_hashes:
                continue
            seen_hashes.add(content_hash)
            verdict = await asyncio.to_thread(self._image_reader,
                                              entry["asset"]["absolutePath"])
            if verdict and verdict.get("readable") is False:
                entry["skipReason"] = f"{verdict.get('reason') or '图片不可读'}，已跳过"
                entry["format"] = verdict.get("format") or "unknown"
                entry["label"] = verdict.get("label") or "未知格式"
                entry["skipKind"] = "unsupported-format"
                apply_content_outcome_to_siblings(entry)
                skipped_count += 1
                emit_log({"type": "asset.skipped", "assetId": entry["asset"]["assetId"],
                          "format": verdict.get("format"), "reason": entry["skipReason"]})
                continue
            entry["format"] = (verdict or {}).get("format") or "unknown"
            entry["label"] = (verdict or {}).get("label") or "未知格式"
            pending_entries.append(entry)

        content_total = len(pending_entries)
        content_processed = 0
        seal_count = 0

        def emit_content_progress() -> None:
            emit_progress({"phase": "content", "processed": content_processed,
                           "total": content_total, "sealCount": seal_count,
                           "skipped": skipped_count})

        emit_content_progress()   # 全部被跳过时上层也能看见 total === 0

        # 分批策略（spec §9.3）：整页扫描图每批 1 张；独立小图每批最多 4 张，两类不混批
        content_batches = [
            *[ [entry] for entry in pending_entries
               if entry["asset"].get("assetKind") == "page-scan" ],
            *chunk_items([entry for entry in pending_entries
                          if entry["asset"].get("assetKind") != "page-scan"],
                         STANDALONE_BATCH_SIZE),
        ]

        content_cache: dict[str, dict[str, Any]] = {}

        async def submit_content_batch(entries: list[dict[str, Any]]) -> None:
            messages = [{
                "role": "user",
                "content": [
                    {"type": "text", "text": build_content_prompt(entries)},
                    *[{"type": "image_url",
                       "image_url": {"url": await asyncio.to_thread(
                           self._image_loader, entry["asset"]["absolutePath"],
                           entry["asset"].get("mimeType") or "image/png")}}
                      for entry in entries],
                ],
            }]
            payload = await self._run_json(messages, "主体印章图片识别",
                                           "印章图片识别结果格式无效", use_vision=True)
            images = payload.get("images") if isinstance(payload, Mapping) else None
            images = images if isinstance(images, list) else None
            if not images or len(images) != len(entries):
                raise create_shape_mismatch_error(
                    "印章识别", len(entries), len(images) if images else 0, payload)
            for entry, image in zip(entries, images):
                image = image if isinstance(image, Mapping) else {}
                has_seal = image.get("has_seal", image.get("hasSeal"))
                seal_text = image.get("seal_text", image.get("sealText"))
                image_type = image.get("image_type", image.get("imageType"))
                entry["content"] = {
                    "hasSeal": bool(has_seal),
                    "sealText": str(seal_text or ""),
                    "imageType": str(image_type or "unknown"),
                }
                content_cache[str(entry["asset"].get("contentHash"))] = entry["content"]

        for batch_index, entries in enumerate(content_batches):
            batch_label = f"{batch_index + 1}/{len(content_batches)}"
            try:
                await submit_content_batch(entries)
            except Exception as error:
                import traceback

                traceback.print_exc()
                emit_log({"type": "batch.failed", "phase": "content",
                          "batch": batch_label,
                          "assetIds": [entry["asset"]["assetId"] for entry in entries],
                          "message": str(error)})
                if len(entries) > 1:
                    # 降级：逐张重试，只有仍然失败的那张记为失败（参考 :282-299）
                    for entry in entries:
                        try:
                            await submit_content_batch([entry])
                        except Exception as single_error:
                            import traceback; traceback.print_exc()
                            entry["error"] = str(single_error) or "图片识别失败"
                            apply_content_outcome_to_siblings(entry)
                else:
                    entries[0]["error"] = str(error) or "图片识别失败"
                    apply_content_outcome_to_siblings(entries[0])
            for entry in entries:
                content_processed += 1
                if (entry.get("content") or {}).get("hasSeal"):
                    seal_count += 1
            emit_content_progress()

        # 同 hash 被去重跳过的出现位置回填内容识别结果（参考 :306-311）
        for entry in by_asset_id.values():
            content_hash = str(entry["asset"].get("contentHash"))
            if (not entry.get("content") and not entry.get("error")
                    and not entry.get("skipReason") and content_hash in content_cache):
                entry["content"] = content_cache[content_hash]

        # 边缘样本二次确认（参考 :318-366）：近名章文 2 票多数表决
        official_name_for_confirm = request.get("officialName")
        aliases_for_confirm = (request.get("aliases")
                               if isinstance(request.get("aliases"), list) else [])
        pending_confirm_entries: list[dict[str, Any]] = []
        seen_confirm_hashes: set[str] = set()
        for entry in by_asset_id.values():
            content_hash = str(entry["asset"].get("contentHash"))
            if not entry.get("content") or entry.get("error") or content_hash in seen_confirm_hashes:
                continue
            seen_confirm_hashes.add(content_hash)
            if not entry["content"].get("hasSeal"):
                continue
            seal_text = str(entry["content"].get("sealText") or "").strip()
            if not seal_text:
                continue
            if matches_allowed_subject(seal_text, official_name_for_confirm,
                                       aliases_for_confirm):
                continue                                  # 已精确命中主体，无歧义
            if is_near_match_subject_name(seal_text, official_name_for_confirm,
                                          aliases_for_confirm):
                pending_confirm_entries.append(entry)

        for entry in pending_confirm_entries:
            original_content = entry["content"]
            readings = [str(original_content.get("sealText") or "").strip()]
            probe_entry = {"asset": entry["asset"], "content": None,
                           "error": None, "skipReason": None,
                           "format": entry.get("format", ""),
                           "label": entry.get("label", ""), "skipKind": ""}
            for _round in range(1, SEAL_TEXT_CONFIRM_ROUNDS + 1):
                try:
                    await submit_content_batch([probe_entry])
                    text = str((probe_entry.get("content") or {}).get("sealText") or "").strip()
                    if (probe_entry.get("content") or {}).get("hasSeal") and text:
                        readings.append(text)
                    else:
                        break   # 复核判无章/读不出：停止投票，保留原读，交给下游分级
                except Exception:
                    break
            chosen = pick_majority_seal_text(readings)
            if chosen and chosen != original_content.get("sealText"):
                original_content["sealText"] = chosen   # 原地更新（与同 hash 兄弟共享）
            content_cache[str(entry["asset"].get("contentHash"))] = original_content

        # 阶段二：角色判断（纯文本，按 contentHash + nearbyContextHash 缓存，参考 :369-432）
        role_cache: dict[str, list[dict[str, Any]]] = {}
        pending_roles = [entry for entry in by_asset_id.values()
                         if entry.get("content") and not entry.get("error")
                         and is_seal_area_content(entry["content"])]
        role_batches = chunk_items(pending_roles, ROLE_BATCH_SIZE)

        async def submit_role_batch(entries: list[dict[str, Any]]) -> None:
            positions = [{"asset": entry["asset"], "content": entry["content"]}
                         for entry in entries]
            cache_key = "|".join(
                f"{entry['asset'].get('contentHash')}:"
                f"{hash_text(entry['asset'].get('nearbyText'))}" for entry in positions)
            if cache_key in role_cache:
                for entry, cached in zip(entries, role_cache[cache_key]):
                    entry["role"] = cached
                return
            payload = await self._run_json(
                [{"role": "user",
                  "content": build_role_prompt(
                      official_name=request.get("officialName"),
                      aliases=request.get("aliases") or [],
                      positions=positions)}],
                "主体盖章角色判断", "盖章角色判断结果格式无效")
            results = payload.get("positions") if isinstance(payload, Mapping) else None
            results = results if isinstance(results, list) else None
            if not results or len(results) != len(entries):
                raise create_shape_mismatch_error(
                    "盖章角色判断", len(entries), len(results) if results else 0, payload)
            normalized = [normalize_role_item(result) for result in results]
            role_cache[cache_key] = normalized
            for entry, role in zip(entries, normalized):
                entry["role"] = role

        for entries in role_batches:
            try:
                await submit_role_batch(entries)
            except Exception as error:
                if len(entries) > 1:
                    for entry in entries:
                        try:
                            await submit_role_batch([entry])
                        except Exception as single_error:
                            import traceback; traceback.print_exc()
                            entry["error"] = str(single_error) or "角色判断失败"
                else:
                    import traceback; traceback.print_exc()
                    entries[0]["error"] = str(error) or "角色判断失败"

        # 组装结果（参考 :435-499）：skipped / failed / inspected 三态
        result_assets: list[dict[str, Any]] = []
        for entry in by_asset_id.values():
            if entry.get("skipReason"):
                result_assets.append({
                    "assetId": entry["asset"]["assetId"], "status": "skipped",
                    "skipKind": entry.get("skipKind") or "",
                    "skipReason": entry["skipReason"],
                    "format": entry.get("format") or "",
                    "label": entry.get("label") or "", "hasSeal": False,
                })
                continue
            if entry.get("error"):
                result_assets.append({
                    "assetId": entry["asset"]["assetId"], "status": "failed",
                    "skipReason": entry["error"],
                    "format": entry.get("format") or "",
                    "label": entry.get("label") or "", "hasSeal": False,
                })
                continue
            content = content_cache.get(
                str(entry["asset"].get("contentHash")),
                {"hasSeal": False, "sealText": "", "imageType": "unknown"})
            if not is_seal_area_content(content):
                result_assets.append({
                    "assetId": entry["asset"]["assetId"], "status": "skipped",
                    "skipKind": "not-seal-area",
                    "skipReason": f"识别为{content.get('imageType')}，非盖章区域",
                    "hasSeal": False,
                })
                continue
            if not content.get("hasSeal"):
                result_assets.append({
                    "assetId": entry["asset"]["assetId"], "status": "inspected",
                    "hasSeal": False, "imageType": content.get("imageType"),
                })
                continue
            role = entry.get("role") or {}
            result_assets.append({
                "assetId": entry["asset"]["assetId"], "status": "inspected",
                "hasSeal": True,
                "sealText": content.get("sealText") or None,
                "detectedSubject": role.get("detectedSubject") or content.get("sealText") or "",
                "imageType": content.get("imageType"),
                "pageRole": role.get("pageRole") or "unclear",
                "sealRole": role.get("sealRole") or "unclear",
                "nameConfidence": role.get("nameConfidence", 0),
                "roleConfidence": role.get("roleConfidence", 0),
                "reason": role.get("reason") or "",
            })

        failed_count = sum(1 for item in result_assets if item["status"] == "failed")
        return {
            "providerStatus": ("error" if failed_count == len(result_assets)
                               and result_assets else "success"),
            "message": f"{failed_count} 张图片识别失败" if failed_count else None,
            "assets": result_assets,
        }


def _default_image_reader(absolute_path: str) -> dict[str, Any]:
    """Pillow 预检（服务端适配，替代参考的 inspectLocalImage）。"""
    try:
        from PIL import Image

        with Image.open(absolute_path) as image:
            fmt = (image.format or "unknown").lower()
        return {"readable": True, "format": fmt, "label": fmt}
    except Exception:
        return {"readable": False, "format": "unknown", "label": "未知格式",
                "reason": "图片不可读"}


def _default_image_loader(absolute_path: str, mime: str) -> str:
    payload = Path(absolute_path).read_bytes()
    return f"data:{mime};base64,{base64.b64encode(payload).decode('ascii')}"
