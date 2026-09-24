"""四检查编排（P4b-2 起逐步替换占位）：真实错别字 + 其余占位。

编排结构（mark_running → 并发 → 进度公式）沿用 `rejection_stub.py`；
错别字分支替换为真实现（prompt → collect_json_response → 原文位置校验 → 落库），
废标项/逻辑/主体保持占位（P4b-2 后续 / P4b-3 交付）。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Mapping, Sequence

from app.domain.rejection.normalize import normalize_subject_name
from app.infra.tasks.rejection_stub import INITIAL_RESULT_MESSAGES
from app.domain.rejection.rolling_rejection import (
    FINAL_CANDIDATE_BATCH_SIZE,
    ROLLING_SEGMENT_LIMIT_RATIO,
    apply_rolling_rejection_patch,
    build_rejection_final_batch_messages,
    build_rejection_global_merge_messages,
    build_rolling_rejection_segment_messages,
    create_bid_package_segments,
    create_empty_rolling_rejection_state,
    create_final_rejection_state_summary,
    create_rejection_final_candidates,
    create_rolling_rejection_state_summary,
    dedupe_rejection_findings,
    normalize_rolling_rejection_patch,
    should_use_segmented_rejection_flow,
)
from app.domain.rejection.rejection_check import (
    build_rejection_check_analysis_messages,
    build_rejection_check_final_messages,
    build_rejection_check_inspection_messages,
    normalize_rejection_check_findings,
)
from app.domain.rejection.subject_body import (
    ROLLING_SEGMENT_CHARS,
    FINAL_CANDIDATE_BATCH_SIZE,
    apply_subject_body_patch,
    build_subject_body_final_messages,
    build_subject_body_segment_messages,
    create_empty_subject_body_state,
    create_subject_body_final_candidates,
    create_subject_body_state_summary,
    normalize_subject_body_findings,
)
from app.domain.rejection.logic_check import (
    build_logic_check_messages,
    normalize_logic_check_findings,
)
from app.domain.rejection.typo_check import (
    build_typo_check_messages,
    normalize_typo_check_findings,
)
from app.infra.repositories.rejection import RejectionRepository

PROGRESS_BASE = 5
PROGRESS_SPAN = 90
PROGRESS_CAP = 95

TYPO_LABEL = "错别字检查"


def compute_progress(completed: int, enabled: int) -> int:
    """参考 rejectionCheckTask.cjs:1679。Math.round 是半数向上（不能用 Python round）。"""
    import math

    if enabled <= 0:
        return PROGRESS_BASE
    return min(PROGRESS_CAP, math.floor(PROGRESS_BASE + (completed / enabled) * PROGRESS_SPAN + 0.5))


class RejectionCheckRunner:
    def __init__(self, repo: RejectionRepository, ai: Any, data_root: Path | str,
                 invalid_items: str = "", custom_items: str = "",
                 official_name: str = "", aliases: list[str] | None = None,
                 config: Mapping[str, Any] | None = None) -> None:
        self._repo = repo
        self._ai = ai
        self._data_root = Path(data_root)
        self._invalid_items = invalid_items
        self._custom_items = custom_items
        self._official_name = official_name
        self._aliases = list(aliases or [])
        self._config = dict(config or {})

    async def __call__(self, ctx: Any, payload: Mapping[str, Any],
                       previous_state: dict | None) -> None:
        project_id = str(payload.get("projectId") or "")
        checks: Sequence[str] = list(payload.get("enabledChecks") or [])
        signatures = payload.get("signatures") or {}
        bid_documents = await asyncio.to_thread(
            self._load_bid_documents, project_id, payload.get("bidDocuments") or [])

        ctx.progress(PROGRESS_BASE, "开始检查投标文件。")
        completed = 0
        lock = asyncio.Lock()

        async def run_one(kind: str) -> None:
            nonlocal completed
            label = {"rejection": "废标项检查", "typo": TYPO_LABEL,
                     "logic": "逻辑谬误检查", "subject": "主体一致性检查"}[kind]
            self._repo.save_result(project_id, kind, {
                "status": "running", "inputSignature": signatures.get(kind),
                "activeFindingId": None,
                "progressMessage": _initial_message(kind),
                "error": None, "findings": [],
                **({"stats": {"visionStatus": "pending" if payload.get("subjectSealCheck")
                              else "disabled"}} if kind == "subject" else {})})
            findings: list[dict[str, Any]] = []
            try:
                if kind == "rejection":
                    findings = await self._run_rejection(project_id, bid_documents, ctx)
                elif kind == "typo":
                    ctx.log(f"{TYPO_LABEL}：正在识别错别字候选。")
                    findings = await self._run_typo(project_id, bid_documents, ctx)
                elif kind == "logic":
                    findings = await self._run_logic(project_id, bid_documents, ctx)
                elif kind == "subject":
                    findings = await self._run_subject_body(project_id, bid_documents, ctx)
                else:
                    # 占位（rolling 变体在 P4b 后续；印章视觉属 P4c）
                    ctx.log(f"{label}（占位）")
            except Exception as error:
                # 参考 runOne 的 try/catch（:1714-1726）：**单检查失败只作废自己**，
                # 其余检查照常成功——评审 B 的 P1（原先异常沿 gather 上抛，整个任务失败）。
                async with lock:
                    completed += 1
                    progress = compute_progress(completed, len(checks))
                    self._repo.save_result(project_id, kind, {
                        "status": "error", "findings": [], "error": str(error)})
                ctx.progress(progress, f"{label}失败")
                ctx.log(f"{label}失败：{error}")
                return
            async with lock:
                completed += 1
                progress = compute_progress(completed, len(checks))
                self._repo.save_result(project_id, kind, {
                    "status": "success", "findings": findings,
                    "activeFindingId": findings[0]["id"] if findings else None,
                    # 参考 :1708：有发现→「{label}发现 N 项」，无→「{label}未发现问题」
                    "progressMessage": (f"{label}发现 {len(findings)} 项" if findings
                                        else f"{label}未发现问题"),
                })
            ctx.progress(progress, f"{label}完成。")

        await asyncio.gather(*(run_one(kind) for kind in checks))
        ctx.progress(100, "检查完成")

    # ------------------------------------------------------------------ 错别字

    async def _run_typo(self, project_id: str, bid_documents: list[dict[str, Any]],
                        ctx: Any) -> list[dict[str, Any]]:
        payload_result = await self._ai.collect_json_response(
            build_typo_check_messages(bid_documents))
        ctx.log(f"{TYPO_LABEL}：正在校验错别字原文位置。")
        findings = normalize_typo_check_findings(payload_result, bid_documents)
        self._repo.clear_findings(project_id, "typo")
        if findings:
            self._repo.save_findings(project_id, "typo", findings)
        return findings

    # ------------------------------------------------- 废标项（三轮，P4b-2）

    async def _run_rejection(self, project_id: str, bid_documents: list[dict[str, Any]],
                             ctx: Any) -> list[dict[str, Any]]:
        input_params = {
            "bidDocuments": bid_documents,
            "invalidBidAndRejectionItems": self._invalid_items or "",
            "customCheckItems": self._custom_items or "",
        }
        # 参考 :1443-1445：超长投标包走 rolling，否则直连三轮
        if should_use_segmented_rejection_flow(input_params, self._config):
            findings = await self._run_rejection_rolling(input_params, bid_documents,
                                                         ctx)
        else:
            findings = await self._run_rejection_direct(input_params, bid_documents,
                                                        ctx)
        self._save_rejection_findings(project_id, findings)
        return findings

    async def _run_rejection_direct(self, input_params: Mapping[str, Any],
                                    bid_documents: list[dict[str, Any]],
                                    ctx: Any) -> list[dict[str, Any]]:
        ctx.log("废标项检查：第一轮：正在分析检查范围。")
        analysis = await self._ai.chat(
            build_rejection_check_analysis_messages(input_params))
        ctx.log("废标项检查：第二轮：正在逐项检查投标文件。")
        draft_findings = await self._ai.chat(
            build_rejection_check_inspection_messages(input_params, analysis))
        ctx.log("废标项检查：第三轮：正在补充、去重并生成结果。")
        payload_result = await self._ai.collect_json_response(
            build_rejection_check_final_messages(input_params, analysis, draft_findings))
        return normalize_rejection_check_findings(payload_result, bid_documents)

    async def _run_rejection_rolling(self, input_params: Mapping[str, Any],
                                     bid_documents: list[dict[str, Any]],
                                     ctx: Any) -> list[dict[str, Any]]:
        """逐段滚动审阅 → 定稿分批 → 全局合稿（参考 :1323-1400 逐字）。"""
        segments = create_bid_package_segments(
            bid_documents, self._config, ROLLING_SEGMENT_LIMIT_RATIO)
        state = create_empty_rolling_rejection_state()
        ctx.log("正在按上下文长度滚动审阅投标包。")
        for segment in segments:
            ctx.log(f"{segment['documentLabel']}：正在滚动审阅投标包第 "
                    f"{segment['segmentIndex']}/{segment['totalSegments']} 段。")
            state_summary = create_rolling_rejection_state_summary(state)
            patch_payload = await self._ai.collect_json_response(
                build_rolling_rejection_segment_messages(input_params, segment,
                                                         state_summary))
            state = apply_rolling_rejection_patch(
                state, normalize_rolling_rejection_patch(
                    patch_payload, bid_documents, segment["documentId"]))

        ctx.log("正在基于全投标包状态定稿废标项风险。")
        candidates = create_rejection_final_candidates(state)
        if not candidates:
            return []
        findings: list[dict[str, Any]] = []
        final_summary = create_final_rejection_state_summary(state)
        batches = [candidates[i:i + FINAL_CANDIDATE_BATCH_SIZE]
                   for i in range(0, len(candidates), FINAL_CANDIDATE_BATCH_SIZE)]
        for batch_index, batch in enumerate(batches):
            ctx.log(f"正在定稿废标项风险第 {batch_index + 1}/{len(batches)} 批。")
            final_payload = await self._ai.collect_json_response(
                build_rejection_final_batch_messages(
                    input_params, batch, final_summary,
                    batch_index + 1, len(batches)))
            findings.extend(normalize_rejection_check_findings(
                final_payload, bid_documents))
        merged_findings = dedupe_rejection_findings(findings)
        if not merged_findings:
            return []
        ctx.log("正在全局合并废标项风险。")
        merged_payload = await self._ai.collect_json_response(
            build_rejection_global_merge_messages(input_params, merged_findings,
                                                  final_summary))
        return dedupe_rejection_findings(
            normalize_rejection_check_findings(merged_payload, bid_documents))

    def _save_rejection_findings(self, project_id: str,
                                 findings: list[dict[str, Any]]) -> None:
        self._repo.clear_findings(project_id, "rejection")
        if findings:
            self._repo.save_findings(project_id, "rejection", findings)

    # ------------------------------------------------------------------ 逻辑谬误

    async def _run_logic(self, project_id: str, bid_documents: list[dict[str, Any]],
                         ctx: Any) -> list[dict[str, Any]]:
        payload_result = await self._ai.collect_json_response(
            build_logic_check_messages(bid_documents))
        findings = normalize_logic_check_findings(payload_result, bid_documents)
        self._repo.clear_findings(project_id, "logic")
        if findings:
            self._repo.save_findings(project_id, "logic", findings)
        return findings

    # --------------------------------------------- 主体正文（滚动状态机，P4b-3）

    async def _run_subject_body(self, project_id: str,
                                bid_documents: list[dict[str, Any]],
                                ctx: Any) -> list[dict[str, Any]]:
        """逐文档分块滚动审阅 → 定稿分批 → 归一化与跨批去重（参考 :351-412）。

        印章分支（runSubjectSealCheck）属 P4c；本实现只做正文（source='text'）。
        """
        state = create_empty_subject_body_state()
        for document in bid_documents:
            text = document["content"]
            total = max(1, -(-len(text) // ROLLING_SEGMENT_CHARS))
            for index in range(total):
                segment = {"documentId": document["id"],
                           "documentLabel": document.get("fileName") or document["id"],
                           "segmentIndex": index + 1, "totalSegments": total,
                           "text": text[index * ROLLING_SEGMENT_CHARS:
                                        (index + 1) * ROLLING_SEGMENT_CHARS]}
                ctx.log(f"{document['fileName']}：正在审阅第 {index + 1}/{total} 段。")
                patch = await self._ai.collect_json_response(
                    build_subject_body_segment_messages(
                        bid_documents=bid_documents, segment=segment,
                        state_summary=create_subject_body_state_summary(state),
                        official_name=self._official_name, aliases=self._aliases))
                state = apply_subject_body_patch(state, patch)

        candidates = create_subject_body_final_candidates(state)
        if not candidates:
            return []

        ctx.log("正在定稿投标主体一致性问题。")
        findings: list[dict[str, Any]] = []
        batches = [candidates[i:i + FINAL_CANDIDATE_BATCH_SIZE]
                   for i in range(0, len(candidates), FINAL_CANDIDATE_BATCH_SIZE)]
        for batch_index, batch in enumerate(batches):
            ctx.log(f"正在定稿第 {batch_index + 1}/{len(batches)} 批。")
            patch = await self._ai.collect_json_response(
                build_subject_body_final_messages(
                    official_name=self._official_name, aliases=self._aliases,
                    candidates=batch,
                    state_summary=create_subject_body_state_summary(state)))
            findings.extend(normalize_subject_body_findings(
                patch, bid_documents=bid_documents,
                official_name=self._official_name, aliases=self._aliases))
        seen: set[str] = set()
        deduped = []
        import re as _re

        for finding in findings:
            key = "\u0000".join([finding["bidDocumentId"], finding["locationKind"],
                                 _re.sub(r"\s+", "",
                                         normalize_subject_name(finding["detectedSubject"])),
                                 _re.sub(r"\s+", "", finding["originalText"])])
            if key in seen:
                continue
            seen.add(key)
            deduped.append(finding)
        self._repo.clear_findings(project_id, "subject")
        if deduped:
            self._repo.save_findings(project_id, "subject", deduped)
        return deduped

    # ------------------------------------------------------------------ 读盘

    def _load_bid_documents(self, project_id: str,
                            metas: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
        """按端点给的元数据读回投标 markdown（磁盘 I/O 走 to_thread 的调用方保证）。"""
        documents: list[dict[str, Any]] = []
        for meta in metas:
            document_id = str(meta.get("id") or "")
            row = self._repo.get_document(project_id, "bid", document_id)
            if row is None:
                continue
            content = self._read_markdown(project_id, str(row["markdown_path"] or ""))
            if content.strip():
                documents.append({"id": document_id,
                                  "fileName": row["file_name"], "content": content})
        return documents

    def _read_markdown(self, project_id: str, relative_path: str) -> str:
        if not relative_path:
            return ""
        target = self._data_root / "projects" / project_id / Path(*relative_path.split("/"))
        try:
            return target.read_text(encoding="utf-8")
        except OSError:
            return ""


def _initial_message(kind: str) -> str:
    return INITIAL_RESULT_MESSAGES.get(kind, "正在检查。")
