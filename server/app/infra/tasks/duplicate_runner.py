"""查重四段分析的编排骨架（P3a：算法用 stub 占位）。

对齐参考实现 `client/electron/services/duplicateCheckService.cjs` 的
`runAnalysisTask`（:2775-2866）与 `run`（:2720-2772）两处，本模块负责：

- **并行时序**：content 段先 `create_task` 起飞（不 await），metadata 段 await 完成，
  再把 outline/content/image 三段置 `running, progress=1` 并广播「等待正文内容用于…」，
  最后 `asyncio.gather` 并行跑后三段。metadata 与 content **并行**是本任务的时序核心
  （参考 :2733-2738）。
- **幂等**：四段全 `success` 且四段签名都等于本次签名、且 `payload.force` 非真 →
  直接报 100 并返回，**不重跑**（参考 :2799-2807）。
- **签名作废**：开跑前签名与库里的不一致（或 `force`）→ 清空四段状态与全部结果表。
- **过期签名丢弃**：写终态**之前**重读当前签名，若已变 → 结果整体丢弃，一个段行都不写
  （参考 :2856-2859 的 `isCurrentDuplicateCheckSignature`）。
- **进度推进**：每次状态变化都按 `overall_progress` / `latest_analysis_message` 广播。

**状态来源（本任务的关键裁定）**：幂等与作废判据读的是**仓储里的持久化状态**
（`repo.get_sections` + `signature_reader`），**不是**任务检查点里的 `previous_state`。
这与参考实现一致——`runAnalysisTask` 读的是 `taskWorkspaceStore.loadDuplicateCheck()`
（:2798），即磁盘上的工作区快照。`previous_state` 本阶段**不参与**任何判定
（它留给将来的断点续跑）。

**P3a 边界**：四段算法由 `_stub_sections()` 占位，只返回空的 `SectionOutput`，
不产结果行、也不写四张结果表；`SectionRunContext` 的形状是给 P3b–P3d 往里填的。
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any

from app.domain.duplicate.progress import latest_analysis_message, overall_progress
from app.domain.duplicate.signature import compute_analysis_signature
from app.domain.duplicate.state import ANALYSIS_SECTIONS, SectionState
from app.infra.repositories.duplicate import DuplicateRepository


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class SectionOutput:
    """单段分析的产出。

    `stats` 写进该段的 `stats_json`（P3a 为空 dict）；`rows` 是**按表名分组**的结果行
    （P3a 为空 dict），由 P3b–P3d 的段实现填充后交给编排层落库。P3a 只检查「有没有行」
    来决定是否落库——空的 `rows` 即「不写结果表」。
    """

    stats: dict[str, Any] = field(default_factory=dict)
    rows: dict[str, list] = field(default_factory=dict)


@dataclass(frozen=True)
class SectionRunContext:
    """单段分析的入参形状。

    形状按「P3b–P3d 真正需要什么」设计，而不是按 P3a 的 stub 需要什么：

    - `project_id` / `section`：段身份；
    - `ctx`：任务上下文（进度 / 日志 / 取消检查）——段实现在 P3b–P3d 里要报进度；
    - `files`：本次参与分析的全部文件（tender + bid，已 `filter(Boolean)`），
      沿用 payload 里的原始项（dict 或带属性的对象），签名即由它算出；
    - `bid_files`：后三段的主对象（对齐参考实现的 `bidFiles`）；
    - `tender_files`：目录分析要用（对齐 `runOutlineAnalysis` 的入参）；
    - `signature`：本次签名，段实现透传以便回写时携带；
    - `content_files`：正文提取产物，是 outline/content/image 三段的**共同上游**
      （对齐参考 :2745-2749 三个分析都吃 `contentFiles`），由 `with_dependency`
      按依赖顺序追加，保持本对象不可变、不被跨段意外复用。
    """

    project_id: str
    section: str
    ctx: Any = None
    files: Sequence[Any] = ()
    bid_files: Sequence[Any] = ()
    tender_files: Sequence[Any] = ()
    signature: str | None = None
    content_files: Sequence[Any] = ()

    def with_dependency(self, name: str, value: Any) -> "SectionRunContext":
        """返回带上了依赖产物的新实例（`name` 必须是本类的字段名）。"""
        if name not in SectionRunContext.__dataclass_fields__:
            raise ValueError(f"未知的段依赖字段：{name}")
        return replace(self, **{name: value})


SectionRunner = Callable[[SectionRunContext], Any]


def _mapping_file_ref(item: Mapping[str, Any]) -> "_MappingFileRef":
    return _MappingFileRef(item)


@dataclass(frozen=True)
class _MappingFileRef:
    """dict 文件项的只读属性视图（只为 `compute_analysis_signature` 而存在）。

    `FileLike` 协议要求 `file_path` / `size` / `modified_at` 三个**属性**，而 payload
    里是 dict。若直接把 dict 交给签名函数，`getattr(file, "file_path", None)` 会全取到
    `None` —— 四份不同的文件清单会算出同一个签名，幂等就会把「文件已变」误判成「没变」。
    """

    _item: Mapping[str, Any]

    @property
    def file_path(self) -> Any:
        return self._item.get("file_path")

    @property
    def size(self) -> Any:
        return self._item.get("size")

    @property
    def modified_at(self) -> Any:
        return self._item.get("modified_at")


def _file_ref(item: Any) -> Any:
    """dict 包成属性视图；已是带属性的对象（如 P3b 的 dataclass）原样透传。"""
    if isinstance(item, Mapping):
        return _mapping_file_ref(item)
    return item


def _tender_files(payload: Mapping[str, Any]) -> list[Any]:
    """取招标文件：`tenderFiles` 是数组就用它，否则回落**单数** `tenderFile`。

    对齐 `getTenderFilesFromPayload`（:122-124）：

    ```js
    Array.isArray(payload.tenderFiles) ? payload.tenderFiles : [payload.tenderFile].filter(Boolean)
    ```

    `.filter(Boolean)` 的对应物是「单数键存在且为 truthy 才取」。
    """
    raw_tender = payload.get("tenderFiles")
    if isinstance(raw_tender, list):
        return [file for file in raw_tender if file]
    single = payload.get("tenderFile")
    return [single] if single else []


def _bid_files(payload: Mapping[str, Any]) -> list[Any]:
    """取投标文件：非数组按空列表处理（对齐 `Array.isArray(payload.bidFiles) ? … : []`）。"""
    raw_bid = payload.get("bidFiles")
    if not isinstance(raw_bid, list):
        return []
    return [file for file in raw_bid if file]


def _payload_files(payload: Mapping[str, Any] | None) -> list[Any]:
    """参与分析的全部文件：`[...tenderFiles, ...bidFiles].filter(Boolean)`。

    对齐 :2721-2724。`filter(Boolean)` 去掉 falsy 项（`None` / 空串 / `0`）。
    """
    data = payload or {}
    return [*_tender_files(data), *_bid_files(data)]


def _stub_sections() -> dict[str, SectionRunner]:
    """四段占位实现（P3a）：做完即返回空的 stats/rows。

    它**不写库**——段状态一律由主编排统一落库，stub 只负责「跑完并交回产出」。
    段内进度不在这里报：段状态由主编排统一写入后按 `overall_progress` 折算，
    两处各写一套会互相覆盖、把进度推成非单调。
    """

    def make(section: str) -> SectionRunner:
        async def run_section(section_ctx: SectionRunContext) -> SectionOutput:
            # 交还一次控制权：create_task 起的 content 任务要有机会被调度，
            # 「并行」才在事件循环里真实发生（串行化后时序用例就该变红）。
            await asyncio.sleep(0)
            return SectionOutput()

        return run_section

    return {section: make(section) for section in ANALYSIS_SECTIONS}


class DuplicateAnalysisRunner:
    """查重分析的四段编排器。

    实现 `Runner` 协议（`Callable[[TaskContext, dict, dict | None], Awaitable[None]]`），
    可由 `TaskEngine` 直接调用。
    """

    def __init__(
        self,
        *,
        repo: DuplicateRepository,
        sections: Mapping[str, SectionRunner] | None = None,
        now_fn: Callable[[], str] = _utc_now,
        signature_reader: Callable[[str], str | None] | None = None,
    ) -> None:
        self._repo = repo
        self._sections = dict(sections) if sections is not None else _stub_sections()
        self._now = now_fn
        # 注入 reader 的**唯一目的**是让「过期签名丢弃」可测：测试无需 monkeypatch
        # 任何内部方法，只要给一个受控的取值函数即可。开跑前的判据与跑完后的校验
        # **都**走这一个入口，否则丢弃用例会退化成对 repo 实现的测试。
        self._signature_reader = signature_reader or self._read_signature

    # ------------------------------------------------------------ Runner 入口

    async def __call__(self, ctx, payload: Mapping[str, Any] | None,
                       previous_state: dict | None) -> None:
        # `previous_state` 刻意不参与任何判定（幂等/作废都读仓储，见模块 docstring）。
        project_id = ctx.project_id
        data = payload or {}
        all_files = _payload_files(data)
        signature = compute_analysis_signature([_file_ref(item) for item in all_files])

        if self._is_up_to_date(project_id, signature, data):
            # 幂等命中：报 100 并返回。**不碰任何已有结果**（尤其不调 delete_sections）。
            ctx.log("标书查重分析已完成，无需重复分析。")
            ctx.progress(100)
            return

        if data.get("force") or self._signature_reader(project_id) != signature:
            # 签名变了（或用户强制）→ 旧结果整体作废后再重跑。
            self._discard_previous(project_id, ctx)

        self._repo.save_meta(project_id, step="analysis", current_signature=signature)

        tender_files = _tender_files(data)
        bid_files = _bid_files(data)

        # metadata 与 content **并行**：content 先起飞、不 await。
        # 参考实现 :2733-2738 是
        #   const contentPromise = runContentExtraction(...);   // 不 await
        #   const metadataFiles  = await runMetadataExtraction(...);
        #   updateOutlineAnalysis({... '等待正文内容用于目录分析' ...});   // ← 后三段的 running
        #   const contentFiles   = await contentPromise;
        # 注意顺序：后三段的 running 写在 content 收尾**之前**（Node 里 content 也早已
        # settled —— 实测 Node 的 `await metadata` 会跑完 content 的微任务链）。
        # 也就是说「等待正文内容」是一条**元数据阶段结束**的广播，并不等于「正文还没提取完」；
        # 第 4 条行为要求的正是这条文案本身，而不是 content 的未完成态。
        content_task = asyncio.ensure_future(
            self._run_section(ctx, project_id, "content", signature, all_files,
                              tender_files, bid_files))
        metadata_output = await self._run_section(
            ctx, project_id, "metadata", signature, all_files, tender_files, bid_files)

        # 元数据落定后，后三段进入 running 并广播「等待正文内容用于…」。
        # 这次写**刻意不带签名**——参考实现在这里把签名一起写死，但那样会让后三段在
        # 「还没跑完」时就已经带上本轮签名；一旦用户恶意卡在 content 之后改文件，
        # 下一次运行会把这些半成品行误判成「已完成」而跳过重跑（幂等判据比对签名）。
        # 不带签名后，这些行必然命中幂等判据的 `signature != signature` 分支而重跑。
        # 运行中段的进度/文案照旧落库，只是不冒充「本轮结果」。
        for section, purpose in (("outline", "目录分析"), ("content", "正文比对"),
                                 ("image", "图片比对")):
            self._save_section(ctx, project_id, section, status="running", progress=1,
                               message=f"元数据提取完成，等待正文内容用于{purpose}")

        content_output = await content_task
        metadata_failed = (self._output_failed(metadata_output)
                           or self._output_failed(content_output))

        # 后三段并行跑（对齐 :2745-2749 的 Promise.all）。正文产物是它们的共同上游。
        # 注意**先跑完、再落库**：写段状态一律排在签名校验之后（见下），
        # 否则「过期签名丢弃」这条路径会先写出去一批段行再回滚不掉。
        extra = {"content_files": [content_output]}
        gather_outputs = await asyncio.gather(*[
            self._run_section(ctx, project_id, section, signature, all_files,
                              tender_files, bid_files, extra_ctx=extra)
            for section in ("outline", "content", "image")
        ])

        # 签名校验（对齐 :2856-2859 的 isCurrentDuplicateCheckSignature）：用户若已改动
        # 文件，这一轮结果整体作废、**一个段行都不写**（连 metadata 的终态也不写）。
        if self._signature_reader(project_id) != signature:
            ctx.log("文件已变更，本次分析结果已丢弃。")
            return

        self._save_section(
            ctx, project_id, "metadata",
            status="error" if metadata_failed else "success", progress=100,
            message="部分文件提取失败" if metadata_failed else "元数据分析完成",
            signature=signature, stats=metadata_output.stats)
        self._persist_rows(project_id, "metadata", metadata_output)

        failed = metadata_failed
        for section, output in zip(("outline", "content", "image"), gather_outputs):
            section_failed = self._output_failed(output)
            failed = failed or section_failed
            self._save_section(
                ctx, project_id, section,
                status="error" if section_failed else "success", progress=100,
                message="分析失败" if section_failed else "分析完成",
                signature=signature, stats=output.stats)
            self._persist_rows(project_id, section, output)

        ctx.progress(overall_progress(self._section_states(project_id)))
        ctx.log("标书查重分析完成，部分结果失败。" if failed else "标书查重分析完成。")

    # ---------------------------------------------------------------- 判定

    def _is_up_to_date(self, project_id: str, signature: str,
                       payload: Mapping[str, Any]) -> bool:
        """幂等判据：`force` 非真 + 四段全 success + 四段签名都等于本次签名。

        逐条对齐 :2799-2803 的合取式（参考实现在一个 `if` 里写完四段）。
        **状态从仓储读**，不读 `previous_state`（见模块 docstring）。
        """
        if payload.get("force"):
            return False
        sections = self._repo.get_sections(project_id)
        for section in ANALYSIS_SECTIONS:
            row = sections.get(section)
            if row is None or row.get("status") != "success":
                return False
            if row.get("signature") != signature:
                return False
        return True

    def _section_states(self, project_id: str) -> dict[str, SectionState | None]:
        """把仓储行读成 domain 的 `SectionState`，供 Task 3 的进度函数使用。"""
        rows = self._repo.get_sections(project_id)
        states: dict[str, SectionState | None] = {}
        for section in ANALYSIS_SECTIONS:
            row = rows.get(section)
            states[section] = None if row is None else SectionState(
                status=row.get("status") or "pending",
                progress=row.get("progress") or 0,
                message=row.get("message") or "",
                signature=row.get("signature"),
            )
        return states

    @staticmethod
    def _output_failed(output: SectionOutput | None) -> bool:
        """段失败判定。

        参考实现按「逐文件结果的 `status === 'error'`」判失败（:2739 / :2751）；
        P3a 的 `SectionOutput` 契约里只有 `stats`/`rows`，没有逐文件状态，
        故本阶段的判据是「段实现自己声明失败」（`SectionOutput` 上没有该字段时为假）。
        P3b–P3d 把逐文件状态填进产出后，这里应扩展为「任一文件 error」。
        """
        return bool(getattr(output, "failed", False))

    # ---------------------------------------------------------------- 作废

    def _discard_previous(self, project_id: str, ctx) -> None:
        """清空四段状态与全部结果表。

        `delete_sections` 已含四段 detail 表的级联清理；这里再显式逐表传空，
        一是与参考实现 `clearSection` 的「逐段清空」语义对齐（:386-407），
        二是保证即使某张表不在级联集合里也不会残留旧结果。
        """
        self._repo.delete_sections(project_id)
        self._repo.replace_content_files(project_id, [])
        self._repo.replace_metadata_items(project_id, [])
        self._repo.replace_outline_items(project_id, [])
        self._repo.replace_outline_groups(project_id, [])
        self._repo.replace_outline_pairwise(project_id, [])
        self._repo.replace_content_duplicates(project_id, [], [])
        self._repo.replace_image_files(project_id, [])
        self._repo.replace_duplicate_images(project_id, [], [])
        ctx.log("文件签名变化，已作废旧结果。")

    def _persist_rows(self, project_id: str, section: str,
                      output: SectionOutput) -> None:
        """把段产出的结果行落库。

        P3a 的 stub 返回空 `rows`，四张核心结果表因此保持为空——这正是本阶段的边界。
        真实的落库映射（哪张表吃哪个键）属于 P3b–P3d：那些阶段的结果形状还没定，
        在 P3a 先写一套猜测的映射只会变成后续阶段要拆掉的假接口。
        """
        rows = output.rows
        if not rows:
            return
        raise NotImplementedError(
            f"{section} 段的结果行落库由 P3b–P3d 接入（P3a 的 stub 不产出结果行）")

    # ---------------------------------------------------------------- 段执行

    async def _run_section(self, ctx, project_id: str, section: str, signature: str,
                           all_files: Sequence[Any], tender_files: Sequence[Any],
                           bid_files: Sequence[Any],
                           extra_ctx: Mapping[str, Any] | None = None) -> SectionOutput:
        """跑一段并把产出交回主编排；段状态落库由主编排统一负责。"""
        runner = self._sections.get(section)
        if runner is None:
            raise ValueError(f"未知的分析段：{section}")
        section_ctx = SectionRunContext(
            project_id=project_id, section=section, ctx=ctx,
            files=list(all_files), bid_files=list(bid_files),
            tender_files=list(tender_files), signature=signature)
        for name, value in (extra_ctx or {}).items():
            section_ctx = section_ctx.with_dependency(name, value)
        output = await runner(section_ctx)
        return output if output is not None else SectionOutput()

    def _save_section(self, ctx, project_id: str, section: str, *, status: str,
                      progress: int, message: str, signature: str | None = None,
                      stats: Any = None) -> None:
        """写一段状态并立刻按四段整体重算进度（对齐 `notifyTask` :2831-2851）。

        签名作为「本轮结果」的标记**每段都要写**（对齐 `updateAnalysisField`
        :2264-2280 每段都带 `signature`），幂等判据依赖它。
        """
        self._repo.save_section(
            project_id, section, status=status, progress=progress, message=message,
            signature=signature, stats=stats if stats is not None else {},
            updated_at=self._now())
        states = self._section_states(project_id)
        ctx.progress(overall_progress(states), latest_analysis_message(states))

    # ---------------------------------------------------------------- 默认读法

    def _read_signature(self, project_id: str) -> str | None:
        """默认签名读取：库里的 `current_signature`。"""
        return (self._repo.get_meta(project_id) or {}).get("current_signature")
