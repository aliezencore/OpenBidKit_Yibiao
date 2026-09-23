"""废标项检查任务的**零算法 stub** runner（P4a T5，照搬 P3a 的做法）。

目的是先把**编排与状态机**钉死（任务能提交、四项结果状态/进度正确、前置约束拦得住、
结果落库形状对），算法在 P4b（提取与四项检查）与 P4c（印章视觉）逐项替换，**编排不动**。

真值来源 `client/electron/services/rejectionCheckTask.cjs`：
- 提取前置与入口 :1515-1521（`runRejectionItemsExtractionTask`）
- 检查前置链 :1615-1666（四条，**顺序固定**）与进度 `:1679`
- 各检查的初始 running 文案 :1649-1670（「第一轮：正在分析检查范围。」等）
"""

from __future__ import annotations

import asyncio
from typing import Any, Mapping, Sequence

from app.infra.repositories.rejection import RejectionRepository

# 参考 :1679：进度 = min(95, round(5 + completed/enabled × 90))，起点 5
PROGRESS_BASE = 5
PROGRESS_SPAN = 90
PROGRESS_CAP = 95

# 参考 :1655-1670 的初始文案（逐字）
INITIAL_RESULT_MESSAGES = {
    # 参考 :1649-1653 逐字
    "rejection": "第一轮：正在分析检查范围。",
    "typo": "正在识别错别字候选。",
    "logic": "正在检查逻辑谬误。",
    "subject": "正在检查投标主体一致性。",
}
CHECK_LABELS = {
    "rejection": "废标项检查",
    "typo": "错别字检查",
    "logic": "逻辑谬误检查",
    "subject": "主体一致性检查",
}
# 提取 stub 的占位正文（明确标注，避免被误当真实解析结果）
STUB_EXTRACTION_CONTENT = "[P4a 占位] 无效与废标项解析尚未接入模型（P4b 交付）"
STUB_SUBJECT_CANDIDATE = "[P4a 占位] 待识别的投标主体"


def compute_progress(completed: int, enabled: int) -> int:
    """参考 :1679 的进度口径（`enabled == 0` 时按 0 计，避免除零）。"""
    if enabled <= 0:
        return PROGRESS_BASE
    return min(PROGRESS_CAP, round(PROGRESS_BASE + (completed / enabled) * PROGRESS_SPAN))


class StubExtractionRunner:
    """`rejection-items-extraction` 的占位实现：立刻成功并写入占位正文。"""

    def __init__(self, repo: RejectionRepository) -> None:
        self._repo = repo

    async def __call__(self, ctx: Any, payload: Mapping[str, Any],
                       previous_state: dict | None) -> None:
        project_id = str(payload.get("projectId") or "")
        ctx.progress(5, "开始解析无效与废标项。")
        ctx.log("P4a 占位：提取算法属 P4b")
        self._repo.save_extraction(project_id, {
            "status": "success",
            "content": STUB_EXTRACTION_CONTENT,
            "source": "tender",
            "tenderSignature": payload.get("tenderSignature"),
        })
        ctx.progress(100, "解析完成（占位）")


class StubSubjectIdentificationRunner:
    """`bid-subject-identification` 的占位实现：写入一个待确认候选。"""

    def __init__(self, repo: RejectionRepository) -> None:
        self._repo = repo

    async def __call__(self, ctx: Any, payload: Mapping[str, Any],
                       previous_state: dict | None) -> None:
        project_id = str(payload.get("projectId") or "")
        ctx.progress(10, "开始识别投标主体。")
        self._repo.save_subject_profile(project_id, {
            "status": "pending-confirmation",
            "candidates": [{"officialName": STUB_SUBJECT_CANDIDATE, "aliases": []}],
            "preselectIndex": 0,
        })
        ctx.log("P4a 占位：主体识别属 P4b")
        ctx.progress(100, "识别完成（占位），请确认投标主体")


class StubRejectionCheckRunner:
    """`rejection-check-run` 的占位实现：四检查并发、主体内部串行、结果落库、进度按公式。

    前置约束**不在这里**校验——端点已前置校验并返回 400（有意偏离：参考在任务内抛错，
    失败会落到 `task.error`；本实现用 HTTP 400 承载同一句话，见计划 T5 登记）。
    """

    def __init__(self, repo: RejectionRepository) -> None:
        self._repo = repo

    async def __call__(self, ctx: Any, payload: Mapping[str, Any],
                       previous_state: dict | None) -> None:
        project_id = str(payload.get("projectId") or "")
        checks: Sequence[str] = list(payload.get("enabledChecks") or [])
        signatures = payload.get("signatures") or {}
        ctx.progress(PROGRESS_BASE, "开始检查投标文件。")

        completed = 0
        lock = asyncio.Lock()

        async def mark_running(kind: str) -> None:
            partial: dict[str, Any] = {
                "status": "running",
                "inputSignature": signatures.get(kind),
                "activeFindingId": None,
                "progressMessage": INITIAL_RESULT_MESSAGES.get(kind, "正在检查。"),
                "error": None,
                "findings": [],
            }
            if kind == "subject":
                # 参考 :1670：印章开关决定 visionStatus 的初始值
                partial["stats"] = {
                    "visionStatus": "pending" if payload.get("subjectSealCheck") else "disabled",
                }
            self._repo.save_result(project_id, kind, partial)

        async def run_one(kind: str) -> None:
            nonlocal completed
            await mark_running(kind)
            label = CHECK_LABELS[kind]
            if kind == "subject":
                # 主体检查**内部串行**：先正文、再印章（R-P4-2）
                ctx.log(f"{label}：正文检查（占位）")
                await asyncio.sleep(0)
                ctx.log(f"{label}：印章检查（占位）")
                await asyncio.sleep(0)
            else:
                ctx.log(f"{label}（占位）")
                await asyncio.sleep(0)
            async with lock:
                completed += 1
                progress = compute_progress(completed, len(checks))
                self._repo.save_result(project_id, kind, {
                    "status": "success",
                    "progressMessage": f"{label}完成（占位）",
                })
            ctx.progress(progress, f"{label}完成")

        for kind in checks:
            await mark_running(kind)
        await asyncio.gather(*(run_one(kind) for kind in checks))
        ctx.progress(100, "检查完成（占位）")
