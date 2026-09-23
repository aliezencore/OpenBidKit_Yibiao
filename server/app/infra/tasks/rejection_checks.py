"""四检查编排（P4b-2 起逐步替换占位）：真实错别字 + 其余占位。

编排结构（mark_running → 并发 → 进度公式）沿用 `rejection_stub.py`；
错别字分支替换为真实现（prompt → collect_json_response → 原文位置校验 → 落库），
废标项/逻辑/主体保持占位（P4b-2 后续 / P4b-3 交付）。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Mapping, Sequence

from app.infra.tasks.rejection_stub import INITIAL_RESULT_MESSAGES
from app.domain.rejection.rejection_check import (
    build_rejection_check_analysis_messages,
    build_rejection_check_final_messages,
    build_rejection_check_inspection_messages,
    normalize_rejection_check_findings,
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
    """参考 rejectionCheckTask.cjs:1679。"""
    if enabled <= 0:
        return PROGRESS_BASE
    return min(PROGRESS_CAP, round(PROGRESS_BASE + (completed / enabled) * PROGRESS_SPAN))


class RejectionCheckRunner:
    def __init__(self, repo: RejectionRepository, ai: Any, data_root: Path | str,
                 invalid_items: str = "", custom_items: str = "") -> None:
        self._repo = repo
        self._ai = ai
        self._data_root = Path(data_root)
        self._invalid_items = invalid_items
        self._custom_items = custom_items

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
            if kind == "rejection":
                findings = await self._run_rejection(project_id, bid_documents, ctx)
            elif kind == "typo":
                ctx.log(f"{TYPO_LABEL}：正在识别错别字候选。")
                findings = await self._run_typo(project_id, bid_documents, ctx)
            elif kind == "logic":
                findings = await self._run_logic(project_id, bid_documents, ctx)
            else:
                # 占位（废标项三轮/rolling 在 P4b-2 后续；主体正文属 P4b-3；印章属 P4c）
                ctx.log(f"{label}（占位）")
            async with lock:
                completed += 1
                progress = compute_progress(completed, len(checks))
                self._repo.save_result(project_id, kind, {
                    "status": "success", "findings": findings,
                    "progressMessage": f"{label}完成",
                })
            ctx.progress(progress, f"{label}完成")

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
        # 直连三轮（rolling 变体未搬，登记：超长投标包行为与参考不同）
        ctx.log("废标项检查：第一轮：正在分析检查范围。")
        analysis = await self._ai.chat(
            build_rejection_check_analysis_messages(input_params))
        ctx.log("废标项检查：第二轮：正在逐项检查投标文件。")
        draft_findings = await self._ai.chat(
            build_rejection_check_inspection_messages(input_params, analysis))
        ctx.log("废标项检查：第三轮：正在补充、去重并生成结果。")
        payload_result = await self._ai.collect_json_response(
            build_rejection_check_final_messages(input_params, analysis, draft_findings))
        findings = normalize_rejection_check_findings(payload_result, bid_documents)
        self._repo.clear_findings(project_id, "rejection")
        if findings:
            self._repo.save_findings(project_id, "rejection", findings)
        return findings

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
