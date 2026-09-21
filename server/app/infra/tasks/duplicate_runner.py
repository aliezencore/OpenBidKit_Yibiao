"""查重四段分析的编排骨架（P3a：算法用 stub 占位）。

对齐参考实现 `client/electron/services/duplicateCheckService.cjs` 的
`runAnalysisTask`（:2775-2866）与 `run`（:2720-2772）两处，本模块负责：

- **并行时序**：`content_extraction` 角色先 `create_task` 起飞（不 await），
  `metadata_extraction` 角色 await 完成，再把 outline/content/image 三段置
  `running, progress=1` 并广播「等待正文内容用于…」，最后 `asyncio.gather` 并行跑后三段。
  两个提取角色**并行**是本任务的时序核心（参考 :2733-2738）。
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

**⚠ 五角色 vs 四段状态机（fix-round 1 / T4-1，这是 fix 的核心）**：参考实现是
**四个不同的函数**——`runContentExtraction`（:2302，共享上游）与
`runOutlineAnalysis` / `runContentDuplicateAnalysis` / `runImageDuplicateAnalysis`
（:2412 / :2504 / :2632，一起进 `Promise.all`，:2745-2749）。`sections` 是**注入的
runner 映射**，它的 key 因此是**五个角色**：

| runner key | 状态行 | 说明 |
|---|---|---|
| `content_extraction` | **不写** | 产出 `outline`/`content`/`image` 三段共享的正文产物 |
| `metadata_extraction` | **不写** | 产出元数据行；其状态并入 `metadata` 段的 stats |
| `outline` / `content` / `image` | 各写一行 | 三段真正的分析状态机 |

而**四段状态机 `ANALYSIS_SECTIONS` 仍是四个**（`metadata`/`outline`/`content`/`image`）：
只有后三个角色写段状态行，`metadata` 段的状态由**主编排**依据「两个提取角色是否成功」
写终态，两个提取角色的统计并入它的 `stats`（对齐参考 :2337 / :2382 把
`contentExtraction` / `metadataExtraction` 都写进 `metadataAnalysis` 的 stats）。

首版把「正文提取」与「正文比对」压进同一个 `content` 键，后果是编排器把 content
runner **跑两次**（一次当提取、一次当比对），第二次还把自己的输出当上游。
**同一角色每轮必须恰好跑一次**由 `test_each_role_runner_runs_exactly_once_per_round` 钉住。

**异常安全（fix-round 1 / F1）**：参考实现在 `run` 外层有 `catch` 写错误态
（:2756-2760）。本实现的对应契约写在 `_guarded_call` 上——见该方法的 docstring。

**P3a 边界**：五个角色由 `_stub_sections()` 占位，只返回空的 `SectionOutput`，
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
from app.infra.tasks.context import TaskCancelled  # noqa: F401  (对外再导出，便于调用方一并捕获)

_UNSET: Any = object()
"""「未携带」哨兵：与 `DuplicateRepository.save_section` 用的是**同一种语义**
（那边是 `duplicate.py:29` 的私有 `_UNSET`）。这里只能自己定义一个——
仓储的哨兵是模块私有的，导入它会把两个模块的实现细节绑在一起。

语义（见 `save_section` 的 docstring）：字段为 `_UNSET` → **不传**该 kwarg，
仓储保留旧值；显式传 `None` → 写 NULL。
"""

# 五个 **runner 角色**（`sections` 的 key）。注意与四段**状态机** `ANALYSIS_SECTIONS`
# 的区别，见模块 docstring 的表格。
CONTENT_EXTRACTION = "content_extraction"
METADATA_EXTRACTION = "metadata_extraction"

# ⚠ 这两个常量**不是同类**：`ANALYSIS_SECTIONS` 是**状态行**的键（4 个，
# 其中 `metadata` 由主编排写终态，**没有 runner**）；`ANALYSIS_RUNNER_ROLES` 是
# **runner 角色**的键（5 个，其中两个提取角色**不写状态行**）。
#
# 首版把角色写成 `(CONTENT_EXTRACTION, METADATA_EXTRACTION, *ANALYSIS_SECTIONS)`，
# 于是多出一个 `"metadata"` 角色：注入 stub 时 `sections` 里会有一个永远不会被调用的
# `metadata` runner（`_stub_sections` 照它造了 6 个 stub）。这是**键集合的错**，
# 不是计数断言太严——已由 `test_runner_roles_are_exactly_five_without_metadata`
# 钉住。两者必须各自显式列出。
ANALYSIS_RUNNER_ROLES: tuple[str, ...] = (
    CONTENT_EXTRACTION, METADATA_EXTRACTION, "outline", "content", "image")

# 由 runner 角色产出的**依赖产物字段名**（`SectionRunContext.with_dependency` 的名字）。
# 对齐参考 :2741 的 `const contentFiles = await contentPromise;` ——
# 后三段的共同上游就是 `content_extraction` 的返回产物。
CONTENT_FILES_DEPENDENCY = "content_files"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class SectionOutput:
    """单段（或单个提取角色）分析的产出。

    - `stats`：落进该段的 `stats_json`（P3a 为空 dict）；提取角色的 `stats` 由编排层
      并入 `metadata` 段的 stats。
    - `rows`：**按表名分组**的结果行（P3a 为空 dict），由 P3b–P3d 的段实现填充后
      交给编排层落库。P3a 只检查「有没有行」来决定是否落库——空的 `rows` 即「不写结果表」。
    - `failed`：段实现**自己声明失败**的钩子。

    `failed` 的地位（fix-round 1 / F7 订正）：它**不在** Task 4 的冻结接口里
    （冻结的是 `stats`/`rows` 两个字段与默认值）。`_output_failed` 用
    `getattr(output, "failed", False)` 读取它，故旧构造（只给 `stats`/`rows`）不受影响；
    这里把它显式声明为第三个字段，是为了让「P3a 的失败分支不可达」这件事
    **在接口上可见**，而不是一个藏在 `getattr` 默认值里的幽灵。
    P3a 的 stub 不设它，故四段终态当前**只可能是 `success`**（见 `_output_failed`）。

    - `message`：段终态文案（P3b 冻结的第 4 字段，默认 `None`）。`None` 时主编排
      用 P3a 默认文案（"分析完成" / "分析失败"）。本任务（T4）只在
      `content_extraction` 产出上预留该字段，不设置它——具体文案由 T5 的
      outline/content 段产出。向后兼容：P3a 既有构造不传，恒 `None`。
    - `extraction`：段内 extraction 子统计（P3b 冻结的第 5 字段，默认 `None`），
      形状 `{status, completed, total}`（对齐参考 `:2485` / `:2616` 的
      `extraction` 子对象）。本任务在 `content_extraction` 产出上设置它；T5 的
      `_commit_terminal_states` 把它并进该段 stats 的 `extraction` 键。向后兼容：
      P3a 既有构造不传，恒 `None`。
    """

    stats: dict[str, Any] = field(default_factory=dict)
    rows: dict[str, list] = field(default_factory=dict)
    failed: bool = False
    message: str | None = None
    extraction: dict[str, Any] | None = None


def content_output_of(content_files: Any) -> "SectionOutput | None":
    """从「后三段的 `content_files` 上游」还原出 `content_extraction` 角色的产出。

    这是一个**刻意的形状约定**（fix-round 1 / F2）：参考实现里 `contentFiles` 就是
    `runContentExtraction(...)` 的返回值，即提取角色的产出本身（:2741）。
    但本实现的 `SectionRunContext` 是**为 P3b–P3d 设计的形状**，
    它的 `content_files` 字段将来要装的是「可被三个分析角色消费的正文产物」
    （markdown 路径 / 文本），**不一定等于**提取角色的 `SectionOutput`。

    故这里把「上游 → 提取角色产出」的解读收进一个函数：P3b 真正定义
    `content_files` 的形状时，只改这一处即可。P3a 的 stub 让两者恒等
    （`SectionOutput` 原样透传），所以现状是精确的。

    **T4 的形状（向后兼容 P3a 测试）**：`content_extraction` 角色现在返回真实的
    `SectionOutput`——`stats` 携带 `{status, completed, total}` 子对象（经
    `_merge_metadata_stats` 落到 `metadata` 段 stats 的 `content_extraction` 键）、
    `rows` 携带 `{"content_files": [...]}`、`extraction` 携带同一子对象、
    `failed` 在任一文件 error 时为 `True`。P3a 既有 stub 仍返回空 `SectionOutput`
    （`failed=False`），故 `content_output_of` 的恒等收口与 `_output_failed` /
    `_merge_metadata_stats` 行为完全不变——本函数体无需更改，形状兼容由
    `SectionOutput` 的字段默认值（`message=None` / `extraction=None`）保证。

    返回 `None` 表示正文不可用（提取失败或上游不是产出对象）——
    此时不产生任何「提取失败」的误判（失败判定由 `_guarded_call` 转出的
    `failed` 产出负责）。
    """
    return content_files if isinstance(content_files, SectionOutput) else None


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
    # 段内进度回调（P3b Task 5 注入）：`section_progress(percent, message)` 由主编排
    # 转成「写该段的 running 行（不带签名，P3a 裁定 P12）」。提取角色
    # （content_extraction / metadata_extraction）不写自己的状态行，故注入 None。
    section_progress: Callable[[int, str], None] | None = None

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
    """dict 包成属性视图；其余（带属性的对象，或非 Mapping 的意外值）原样透传。

    **已知局限（fix-round 1 / F8）**：非 Mapping 也非「带属性对象」的项（如 `42`）
    在这里被原样透传，签名函数对它的属性全取 `None` —— 不同的错误项会**折叠成同一个
    签名**。上游保证文件项是 dict，故按已知局限登记，见 `_bid_files` 的 docstring。
    """
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
    """取投标文件：非数组按空列表处理（对齐 `Array.isArray(payload.bidFiles) ? … : []`）。

    **已知局限（fix-round 1 / F8）**：这里保留了 `file` 的真值（`if file`），却没有
    检查 `file` 是不是 `Mapping`。列表里放非 dict 项（如 `bidFiles=[42]`）时，
    `_file_ref` 原样透传 `42`，签名函数对它的三个属性全取到 `None`
    —— 于是 `[42]` 与 `[43]` 会算出**同一个签名**，幂等会把「文件已换」误判成「没变」。
    上游（HTTP 入口 / 任务入队）保证文件项是 dict，故本阶段按**已知局限**登记而不是
    加一层形状校验；折叠点就在 `_file_ref` 的 `return item`（那里有对应注释）。
    """
    raw_bid = payload.get("bidFiles")
    if not isinstance(raw_bid, list):
        return []
    return [file for file in raw_bid if file]


def _payload_files(payload: Mapping[str, Any] | None) -> list[Any]:
    """参与分析的全部文件：`[...tenderFiles, ...bidFiles].filter(Boolean)`。

    对齐 :2721-2724。`filter(Boolean)` 去掉 falsy 项（`None` / 空串 / `0`）。

    **非 `Mapping` 的载荷按空处理（fix-round 1 / F8）**：原实现写的是 `payload or {}`，
    它对 `[1, 2]` 这类**非空**列表毫无作用——`data.get` 会直接抛 `AttributeError`。
    现在显式判类型：不是 `Mapping` 就当作空载荷（`_tender_files` / `_bid_files` 都容错），
    与「缺失文件键」的行为一致。**不抛错**是有意的：本层的职责是编排，
    载荷形状的校验属于 HTTP/任务入口；编排出错会把四个角色卡在 running（见 F1）。
    """
    data = payload if isinstance(payload, Mapping) else {}
    return [*_tender_files(data), *_bid_files(data)]


def _stub_sections() -> dict[str, SectionRunner]:
    """五个角色的占位实现（P3a）：做完即返回空的 `SectionOutput`。

    它**不写库**——段状态一律由主编排统一落库，stub 只负责「跑完并交回产出」。
    段内进度不在这里报：段状态由主编排统一写入后按 `overall_progress` 折算，
    两处各写一套会互相覆盖、把进度推成非单调。

    返回的五个键是**角色**，不是四段：`content_extraction` 与 `metadata_extraction`
    是提取阶段，后三个才是四段状态机里的段（见模块 docstring 的表格）。
    """

    def make(section: str) -> SectionRunner:
        async def run_section(section_ctx: SectionRunContext) -> SectionOutput:
            # 交还一次控制权：create_task 起的 content_extraction 角色要有机会被调度，
            # 「并行」才在事件循环里真实发生（串行化后时序用例就该变红）。
            await asyncio.sleep(0)
            return SectionOutput()

        return run_section

    return {role: make(role) for role in ANALYSIS_RUNNER_ROLES}


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
        # 本编排器**自己起的**任务（目前只有 `content_extraction`）。F6 的收口需要它：
        # `_drain_pending_tasks` 只能动自己的任务，绝不能碰调用方的任务。
        self._own_tasks: list["asyncio.Task[Any]"] = []
        # 注入 reader 的**唯一目的**是让「过期签名丢弃」可测：测试无需 monkeypatch
        # 任何内部方法，只要给一个受控的取值函数即可。开跑前的判据与跑完后的校验
        # **都**走这一个入口，否则丢弃用例会退化成对 repo 实现的测试。
        self._signature_reader = signature_reader or self._read_signature

    # ------------------------------------------------------------ Runner 入口

    async def __call__(self, ctx, payload: Mapping[str, Any] | None,
                       previous_state: dict | None) -> None:
        # `previous_state` 刻意不参与任何判定（幂等/作废都读仓储，见模块 docstring）。
        project_id = ctx.project_id
        data = payload if isinstance(payload, Mapping) else {}
        all_files = _payload_files(data)
        signature = compute_analysis_signature([_file_ref(item) for item in all_files])

        if self._is_up_to_date(project_id, signature, data):
            # 幂等命中：报 100 并返回。**不碰任何已有结果**（尤其不调 delete_sections）。
            ctx.log("标书查重分析已完成，无需重复分析。")
            ctx.progress(100)
            return

        # 空文件清单的兜底（fix-round 1 / Task 5 I-1 的连带修复）。
        #
        # `compute_analysis_signature([])` 是 sha1("") = `da39a3ee…`，一个**常量**。
        # 若库里 `current_signature` 恰好也是它、且四段已是 `success` + 同一签名，
        # 幂等判据 `_is_up_to_date` 会为真 → 直接报 100 返回，**四段带着上一轮的
        # 状态与文案原样返回**：本轮一个文件都没算，却报「查重已完成」。
        #
        # 修法落在 `_is_up_to_date` 里而不是这里：幂等短路发生在本行**之前**，
        # 放在这里根本拦不住（我第一版就是这么写错的，被新用例当场抓住）。见那里。
        signature_changed = self._signature_reader(project_id) != signature
        if data.get("force") or signature_changed:
            # 签名变了（或用户强制）→ 旧结果整体作废后再重跑。
            self._discard_previous(project_id, ctx)

        self._repo.save_meta(project_id, step="analysis", current_signature=signature)

        tender_files = _tender_files(data)
        bid_files = _bid_files(data)

        try:
            await self._orchestrate(ctx, project_id, signature, all_files,
                                    tender_files, bid_files)
        except (asyncio.CancelledError, TaskCancelled):
            # 协作式取消：不是错误，必须原样传出（吞掉会让 TaskEngine 以为任务正常结束）。
            # 但**段的定位**不能因此丢掉：先把还没到终态的段收成 error，再抛出。
            # 这里**不**调 `ctx.raise_if_cancelled()`（收尾路径要尽力做完，见
            # `_fail_pending_sections` 的说明）。
            self._fail_pending_sections(ctx, project_id, "任务已取消")
            raise
        except Exception as error:  # noqa: BLE001 —— 顶层兜底，见 F1
            # 顶层兜底：参考实现在 `run` 外层有 `catch` 写错误态（:2756-2760）。
            # 走到这里说明异常发生在编排体**之外**的契约之外的位置（编排体自己的异常
            # 已在 `_orchestrate` 内被逐角色收成 error 终态，不会再冒到这里）。
            # 无论如何都不能让四段停在 `running`：把还没到终态的段标成 error。
            message = str(error) or type(error).__name__
            ctx.log(f"标书查重分析异常中断：{message}")
            self._fail_pending_sections(ctx, project_id, message)
        finally:
            # F6：无论走哪条路径，都不留下「异常从未被取回」的任务。
            await self._drain_pending_tasks()

    async def _orchestrate(self, ctx, project_id: str, signature: str,
                           all_files: Sequence[Any], tender_files: Sequence[Any],
                           bid_files: Sequence[Any]) -> None:
        """编排主体：起两个提取角色 → 置三段 running → 并行跑后三段 → 写终态。

        本方法内的任何异常都已被逐角色/逐段收成**该角色或该段的 error 终态**
        （见 `_guarded_call`），因此外层不需要再为「某个段炸了」兜底。
        """
        # `content_extraction` 与 `metadata_extraction` **并行**：content 先起飞、
        # 不 await。参考实现 :2733-2738 是
        #   const contentPromise = runContentExtraction(...);   // 不 await
        #   const metadataFiles  = await runMetadataExtraction(...);
        # 注意顺序：后三段的 running 写在提取角色收尾**之前**（Node 里 content 也早已
        # settled —— 实测 Node 的 `await metadata` 会跑完 content 的微任务链）。
        # 也就是说「等待正文内容」是一条**元数据阶段结束**的广播，并不等于「正文还没提取完」；
        # 第 4 条行为要求的正是这条文案本身，而不是 content 的未完成态。
        #
        # `ensure_future` 而非裸 await：两者共用 `_guarded_call` 的
        # 「异常 → 角色 error 终态」契约，串行/并行只是调度方式不同（见 F4 的并发用例）。
        ctx.raise_if_cancelled()
        content_task = asyncio.ensure_future(self._guarded_call(
            ctx, project_id, CONTENT_EXTRACTION, signature, all_files,
            tender_files, bid_files))
        self._own_tasks.append(content_task)  # 供 F6 的收口取回
        metadata_output = await self._guarded_call(
            ctx, project_id, METADATA_EXTRACTION, signature, all_files,
            tender_files, bid_files)

        # 元数据落定后，后三段进入 running 并广播「等待正文内容用于…」。
        # 这次写**刻意不带签名**——这是与参考实现的一处**有意偏离**，理由如下。
        #
        # 参考实现里这三段的签名来自 `createInitialOutlineAnalysis` /
        # `createInitialContentAnalysis` / `createInitialImageAnalysis`
        # （:2140-2186，三个函数都把 `signature` 写进**初始值**），随后
        # `run` 里那三次 `updateOutlineAnalysis(...)` / `updateContentAnalysis(...)` /
        # `updateImageAnalysis(...)`（:2735-2737）只是把 `status/progress/message`
        # 盖上去，**并没有在这里补签名**（签名早在建初始值时就在对象里了）。
        # fix-round 1 / F5 订正：原报告把来源写成 :2735-2737 的 update 调用，**那是错的**。
        #
        # 于是参考实现的语义是「后三段在**还没跑完**时就已经带上本轮签名」。
        # 这会与幂等判据形成真实的危险组合：一旦某段把 status 写成 success
        # （半成品也算 success），下一次运行就会因「签名匹配 + status=success」而跳过重跑。
        #
        # **F5 订正的第二点**：这个危害在当前 P3a 里**尚不可达**——`_stub_sections()`
        # 不写任何段状态（段状态一律由主编排统一落库），而幂等判据要求
        # `status == 'success'`。也就是说危险是在 **P3b 的段实现自己把状态写成 success**
        # 之后才成立的，本阶段只是**提前拆掉**了这个组合条件，属于防御性偏离；
        # 原报告说「P3a 的 stub 会把它写成 success」是**过早的**，同样订正。
        #
        # 不带签名后，这些行必然命中幂等判据的 `signature != signature` 分支而重跑。
        # 运行中段的进度/文案照旧落库，只是不冒充「本轮结果」。
        ctx.raise_if_cancelled()
        for section, purpose in (("outline", "目录分析"), ("content", "正文比对"),
                                 ("image", "图片比对")):
            self._save_section(ctx, project_id, section, status="running", progress=1,
                               message=f"元数据提取完成，等待正文内容用于{purpose}")

        # 正文提取的产出（或它的异常）在这里收口。**即使提取炸了也要 await 它**：
        # 不 await 就取不回产出，后三段的 `content_files` 上游也会是空的。
        content_files = await self._resolve_content_files(
            ctx, project_id, content_task)

        # 后三段并行跑（对齐 :2745-2749 的 Promise.all）。正文产物是它们的共同上游。
        # 注意**先跑完、再落库**：写段状态一律排在签名校验之后（见下），
        # 否则「过期签名丢弃」这条路径会先写出去一批段行再回滚不掉。
        #
        # `return_exceptions=True` 是 F1 的核心：默认的 `gather` 会把第一个异常向上抛、
        # **同时取消兄弟任务**——兄弟的异常连同终态一起丢掉，四段就永远停在 running。
        # 逐角色收异常后，每个段都能拿到自己的 error 终态（见 `_guarded_call`）。
        gather_outputs = await asyncio.gather(*[
            self._guarded_call(ctx, project_id, section, signature, all_files,
                               tender_files, bid_files,
                               extra_ctx={CONTENT_FILES_DEPENDENCY: content_files})
            for section in ("outline", "content", "image")
        ], return_exceptions=True)

        # 签名校验（对齐 :2856-2859 的 isCurrentDuplicateCheckSignature）：用户若已改动
        # 文件，这一轮结果整体作废、**一个段行都不写**（连 metadata 的终态也不写）。
        ctx.raise_if_cancelled()
        if self._signature_reader(project_id) != signature:
            ctx.log("文件已变更，本次分析结果已丢弃。")
            return

        self._commit_terminal_states(
            ctx, project_id, signature, metadata_output, content_files, gather_outputs)

    def _commit_terminal_states(self, ctx, project_id: str, signature: str,
                               metadata_output: SectionOutput, content_files: Any,
                               gather_outputs: Sequence[Any]) -> None:
        """写四段终态（签名已确认是本轮的）。"""
        ctx.raise_if_cancelled()
        metadata_failed = (self._output_failed(metadata_output)
                           or self._output_failed(content_output_of(content_files)))
        metadata_stats = self._merge_metadata_stats(
            metadata_output, content_files)
        self._save_section(
            ctx, project_id, "metadata",
            status="error" if metadata_failed else "success", progress=100,
            message="部分文件提取失败" if metadata_failed else "元数据分析完成",
            signature=signature, stats=metadata_stats)
        self._persist_rows(project_id, "metadata", metadata_output)

        failed = metadata_failed
        for section, output in zip(("outline", "content", "image"), gather_outputs):
            output = self._as_output(output)
            section_failed = self._output_failed(output)
            failed = failed or section_failed
            # P3b Task 5：终态文案用段产出的 `message`（逐字文案；None 时回退统一文案），
            # 并把 `extraction` 子统计并入段 stats（对齐参考 :2485 / :2617 的 `extraction`）。
            stats = dict(output.stats or {})
            if output.extraction:
                stats["extraction"] = output.extraction
            message = output.message if output.message else (
                "分析失败" if section_failed else "分析完成")
            self._save_section(
                ctx, project_id, section,
                status="error" if section_failed else "success", progress=100,
                message=message, signature=signature, stats=stats)
            self._persist_rows(project_id, section, output)

        ctx.progress(overall_progress(self._section_states(project_id)))
        ctx.log("标书查重分析完成，部分结果失败。" if failed else "标书查重分析完成。")

    def _merge_metadata_stats(self, metadata_output: SectionOutput,
                              content_files: Any) -> dict[str, Any]:
        """把**两个提取角色**的状态并入 `metadata` 段的 stats。

        对齐参考 `:2337` / `:2382` —— `contentExtraction` 与 `metadataExtraction`
        都写进 `metadataAnalysis` 的 stats，而这两个提取角色**不写自己的状态行**
        （见模块 docstring 的表格）。参考写的是完整对象
        （`{status, completed, total}` + `files`/结果行）；P3b 的提取角色把结果行填进
        `rows` 后，这里应改为把 `status`/`completed`/`total` 一并塞进子统计。

        **P3a 的确切行为**：`stats` 是浅合并——`metadata_extraction` 的 stats 原样作基底
        （P3a 为空 dict），`content_extraction` 的产出挂到 `content_extraction` 键下
        （P3a 为 `{}`）。合并结果**恒为 dict**：`_save_section` 的 stats 语义是
        字典更新（`dict.update`），塞子统计前必须先有基底。
        """
        merged: dict[str, Any] = dict(metadata_output.stats or {})
        content_output = content_output_of(content_files)
        if content_output is not None:
            # P3b Task 5：按 `extraction` 字段填实 content_extraction 子统计
            # （T4 的 content_extraction 同时设了 `stats` 与 `extraction`，二者同形；
            # 优先用 `extraction` 字段，与计划 Task 5 :357 对齐）。
            #
            # **键名 camelCase**（Task 7 修正）：参考 :2347 与仓储读取侧
            # （`_load_metadata_analysis` 的 `stats.get("contentExtraction")`）都是
            # camelCase；用 snake_case 会让 `/state` 的
            # `metadataAnalysis.stats.contentExtraction` 恒为空。
            merged["contentExtraction"] = dict(
                content_output.extraction or content_output.stats or {})
        return merged

    # ------------------------------------------------------------ 异常安全

    async def _guarded_call(self, ctx, project_id: str, role: str, signature: str,
                            all_files: Sequence[Any], tender_files: Sequence[Any],
                            bid_files: Sequence[Any],
                            extra_ctx: Mapping[str, Any] | None = None) -> Any:
        """跑一个角色；**异常不向上抛**，而是转成该角色产出的 `SectionOutput`。

        这是 F1 的契约：`content_extraction` / `metadata_extraction` 两个提取角色
        **不写自己的状态行**，所以它们的异常在此被就地转成
        「`failed` 为真的 `SectionOutput`」，由 `_commit_terminal_states` 写进
        `metadata` 段的**终态**——这正是参考实现 `catch`（:2756-2760）写
        `status: 'error'` 的对应物。后三个段角色本来就各自有状态行，
        它们的异常同样在 `_guarded_call` 内转成 error 产出，再由编排层写成
        该段的 `error` 终态。

        返回值是 `SectionOutput`（失败时是 `failed=True` 的产出）；唯一会**抛出**的是
        取消（`asyncio.CancelledError` / `TaskCancelled`），那是控制流而非段失败，
        由调用链的 `finally` 取回。

        `ctx.raise_if_cancelled()` **在 try 之内**：它抛的 `TaskCancelled` 与段实现抛的
        取消是同一类控制流。若把它放在 try 外，`content_extraction` 任务里的取消
        就会从一个**没人 await 的任务**里逃出去 —— 那正是 `Task exception was never
        retrieved` 的来源（实测踩到过）。放进 try 后，取消被原样 `raise` 出协程，
        由 `_orchestrate` / `__call__` 的 `finally`（`_drain_pending_tasks`）取回。
        """
        try:
            ctx.raise_if_cancelled()
            output = await self._run_section(
                ctx, project_id, role, signature, all_files, tender_files, bid_files,
                extra_ctx=extra_ctx)
        except (asyncio.CancelledError, TaskCancelled):
            # 取消是控制流，不是段失败：必须原样传出（吞掉会让 TaskEngine 以为任务正常结束）。
            # 取回由 `_drain_pending_tasks` 负责（F6）。
            raise
        except Exception as error:  # noqa: BLE001 —— 段级兜底
            # 段异常 → 该角色的失败产出（终态由 `_commit_terminal_states` 写）。
            return self._failure_output(error)
        return output

    @staticmethod
    def _failure_output(error: BaseException) -> SectionOutput:
        """把段异常转成 `failed` 的产出（异常原文进 `stats.error`，供终态文案与排障）。"""
        message = str(error) or type(error).__name__
        return SectionOutput(stats={"error": message}, failed=True)

    @staticmethod
    def _as_output(output: Any) -> SectionOutput:
        """把 gather 结果（`SectionOutput` 或异常对象）统一成产出对象。"""
        if isinstance(output, BaseException):
            return DuplicateAnalysisRunner._failure_output(output)
        return output if output is not None else SectionOutput()

    async def _resolve_content_files(self, ctx, project_id: str,
                                     content_task: "asyncio.Task[Any]") -> Any:
        """收口 `content_extraction` 角色：产出或异常，二者必居其一。

        `content_task` 上是 `_guarded_call` 的结果，所以：
        - 正常 → 产出对象；
        - 段内抛错 → 已转成 `failed` 产出（不是异常）；
        - 取消 → `CancelledError`，原样抛出（由外层收成 error 终态）；
        - `BaseException` 子类 → 原样抛出。

        返回 `None` 表示**正文不可用**：后三段的 `content_files` 上游按 `[]` 注入。
        """
        try:
            return await content_task
        except BaseException as error:  # noqa: BLE001 —— 见 docstring 的分支说明
            # 这里**只**兜 BaseException 子类；普通 Exception 已在 `_guarded_call` 内
            # 转成 failed 产出，不会走到这里。`CancelledError` 一并收成段 error 终态：
            # 这一轮已经写不完整了，但段不能停在 running。
            ctx.log(f"正文内容提取失败：{str(error) or type(error).__name__}")
            return None

    def _fail_pending_sections(self, ctx, project_id: str, message: str) -> None:
        """把仍处于非终态的段写成 `error`（顶层兜底用，F1）。

        **不在这里调 `raise_if_cancelled()`**：本方法是「收尾」路径，已经在异常处理
        之中，再抛一次取消会把「把段收成终态」这件唯一要做的事也打断，
        四段停留在 running —— 这正是 F1 要根除的状态。收尾路径要尽力做完。
        """
        states = self._section_states(project_id)
        for section in ANALYSIS_SECTIONS:
            state = states.get(section)
            if state is not None and state.status in ("success", "error"):
                continue  # 已有终态，别覆盖
            self._save_section(ctx, project_id, section, status="error", progress=100,
                               message=message, stats=_UNSET)
        ctx.progress(overall_progress(self._section_states(project_id)))

    async def _drain_pending_tasks(self) -> None:
        """取消并取回本编排器自己起的任务，杜绝 `Task exception was never retrieved`（F6）。

        `asyncio.gather(..., return_exceptions=True)` 只覆盖它自己那批；早退路径
        （取消传播、过期签名丢弃、顶层异常）下 `content_extraction` 可能还在飞，
        此时没人取它的结果 —— 它一旦带着 `TaskCancelled` / 别的异常结束，
        asyncio 就会在 GC 时报「never retrieved」。这里把它**取消 + 取回**：

        - `cancel()`：把它从挂起态推进到结束（不取消的话，一个阻塞在真实 I/O 上的
          提取任务会一直挂着，本轮早已判定为不完整）。
        - `gather(..., return_exceptions=True)`：取回一次即「已检索」。
          **取消也算取回**：`CancelledError` 不会因为 `return_exceptions=True`
          变成结果值（asyncio 会把它继续向外传），所以额外用 `task.exception()`
          兜一次 —— 对未取消的任务它返回异常对象（即「已检索」）。

        **只处理 `self._own_tasks`**：首版写成「当前任务之外的所有存活任务」，
        那会把**调用方的任务**（集成时就是 TaskEngine 跑的 runner 任务）一起取消、
        连自己的关闭流程都打断。这条边界是本 fix 的第二处修正。
        """
        pending = [task for task in self._own_tasks if not task.done()]
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        # 取回即「已检索」。已取消的任务其异常已被 gather 消费；
        # 对未取消但已结束的，`exception()` 再兜一次（返回 None 表示正常结束）。
        for task in self._own_tasks:
            if task.done() and not task.cancelled():
                task.exception()
        self._own_tasks = [task for task in self._own_tasks if not task.done()]

    # ---------------------------------------------------------------- 判定

    def _is_up_to_date(self, project_id: str, signature: str,
                       payload: Mapping[str, Any]) -> bool:
        """幂等判据：`force` 非真 + 四段全 success + 四段签名都等于本次签名。

        逐条对齐 :2799-2803 的合取式（参考实现在一个 `if` 里写完四段）。
        **状态从仓储读**，不读 `previous_state`（见模块 docstring）。

        **空清单额外判据（fix-round 1 / Task 5 I-1 的连带修复）**：
        `compute_analysis_signature([])` 是常量 `sha1("")`。若库里
        `current_signature` 与四段签名恰好都是它，上式会**在没有任何文件可分析时
        命中幂等** → 直接报 100 返回、四段保留上一轮的状态与文案 → 用户看到
        「查重已完成」，而实际上本轮一个文件都没算。

        故先判「本次载荷是否为空」：为空时**永远不算 up-to-date**，必须走真实路径
        （真实路径会 `_discard_previous` 清掉上一轮状态，再由终态写入覆盖）。
        这与 `POST /analysis` 的接口层校验（拒绝全失败行）是**两道独立的防线**：
        接口层管 HTTP 入口，这里管**绕过接口层直接 submit** 的调用方。

        参考实现 `isCurrentDuplicateCheckSignature`（:2856-2859）没有这个特例，
        属于本实现的有意收紧；代价是空载荷会多跑一轮 stub，收益是不会把
        「什么都没算」报成「已完成」。
        """
        if payload.get("force"):
            return False
        if not [item for item in _payload_files(payload) if item]:
            # 空载荷：不可能有「本轮结果」，不得命中幂等。
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
        P3a 只有段实现自己声明的 `SectionOutput.failed` 与 `_guarded_call` 转出的
        失败产出（段抛异常）。

        **P3a 的失败分支不可达（fix-round 1 / F7，这是已知且刻意的边界）**：
        `_stub_sections()` 恒返回 `SectionOutput()`（`failed=False`），
        所以只要**没有段抛异常**，四段终态就一定是 `success`。
        异常路径现在是可达的（F1 修好之后），但「逐文件失败」这条路要等 P3b–P3d
        把逐文件状态填进产出、并把 `failed` 接上「任一文件 error」才可达。
        用例 `test_failure_branch_is_unreachable_in_p3a` 显式钉住这个事实——
        将来谁把 `failed` 接上了，那条用例会变红并提醒他去更新这里的说明与调用方契约。
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
        """把段产出的结果行落库（P3b Task 5 实现）。

        按 `output.rows` 里**出现过的键**分派到对应仓储 `replace_*` 方法；缺键即不写
        该表（P3a 的 stub 仍返回空 `rows` → 这里直接 return，行为不变）。

        分派映射（计划 :347-354）：

        | rows 键 | 落库调用 |
        |---|---|
        | `content_files` | `replace_content_files(project_id, rows)` |
        | `outline_items` | `replace_outline_items(project_id, rows)` |
        | `outline_groups` | `replace_outline_groups(project_id, rows)` |
        | `outline_pairwise` | `replace_outline_pairwise(project_id, rows)` |
        | `content_duplicates` + `content_occurrences` | `replace_content_duplicates(project_id, rows, occurrences)` |

        落库失败（任一 replace 抛）→ 上抛 → `__call__` 顶层兜底把未终态段收 error
        （P3a F1 语义）——本方法不自己吞异常。
        """
        rows = output.rows
        if not rows:
            return
        content_files = rows.get("content_files")
        if content_files is not None:
            self._repo.replace_content_files(project_id, content_files)
        outline_items = rows.get("outline_items")
        if outline_items is not None:
            self._repo.replace_outline_items(project_id, outline_items)
        outline_groups = rows.get("outline_groups")
        if outline_groups is not None:
            self._repo.replace_outline_groups(project_id, outline_groups)
        outline_pairwise = rows.get("outline_pairwise")
        if outline_pairwise is not None:
            self._repo.replace_outline_pairwise(project_id, outline_pairwise)
        content_duplicates = rows.get("content_duplicates")
        if content_duplicates is not None:
            self._repo.replace_content_duplicates(
                project_id, content_duplicates,
                rows.get("content_occurrences") or [])

    # ---------------------------------------------------------------- 段执行

    async def _run_section(self, ctx, project_id: str, role: str, signature: str,
                           all_files: Sequence[Any], tender_files: Sequence[Any],
                           bid_files: Sequence[Any],
                           extra_ctx: Mapping[str, Any] | None = None) -> SectionOutput:
        """跑一个角色并把产出交回主编排；状态落库由主编排统一负责。

        `role` 是**五角色**之一（`ANALYSIS_RUNNER_ROLES`），同时作为
        `SectionRunContext.section` 传下去 —— 提取角色因此也能从上下文里认出自己
        （它们不写状态行，但需要知道自己是谁、该产出什么）。
        """
        runner = self._sections.get(role)
        if runner is None:
            raise ValueError(f"未知的分析角色：{role}")
        # 段内进度回调（P3b Task 5）：提取角色不写状态行 → None；outline/content/image
        # 三段 → 写对应段的 running 行（不带签名，P3a 裁定 P12）。
        if role in ("outline", "content", "image"):
            progress_cb: Callable[[int, str], None] | None = (
                lambda percent, message, _role=role: self._save_section(
                    ctx, project_id, _role, status="running",
                    progress=percent, message=message))
        else:
            progress_cb = None
        section_ctx = SectionRunContext(
            project_id=project_id, section=role, ctx=ctx,
            files=list(all_files), bid_files=list(bid_files),
            tender_files=list(tender_files), signature=signature,
            section_progress=progress_cb)
        for name, value in (extra_ctx or {}).items():
            section_ctx = section_ctx.with_dependency(name, value)
        output = await runner(section_ctx)
        return output if output is not None else SectionOutput()

    def _save_section(self, ctx, project_id: str, section: str, *, status: str,
                      progress: int, message: str, signature: Any = _UNSET,
                      stats: Any = _UNSET) -> None:
        """写一段状态并立刻按四段整体重算进度（对齐 `notifyTask` :2831-2851）。

        签名作为「本轮结果」的标记**每段都要写**（对齐 `updateAnalysisField`
        :2264-2280 每段都带 `signature`），幂等判据依赖它。

        **逐字段的「未携带 vs 显式值」语义（fix-round 1 / F3）**：
        `stats` 与 `signature` 在本方法里是**哨兵默认值**，只有调用方真的传了才转发给
        `repo.save_section`——不传即「保留库里的旧值」。这与仓储的
        `signature`/`stats`/`started_at` 三分语义严格对齐（`duplicate.py:291-313`）。

        首版写的是 `stats=stats if stats is not None else {}`，那等于**每次写段状态
        都把 stats 砸成 `{}`**：P3b 的段实现若在中途写了进度类 stats、终态不写，
        那些 stats 会被静默抹掉。现在改成：
        - 不传 `stats` → 仓储保留旧值（`_UNSET`）；
        - 显式传 `stats=None` → 写 NULL（本编排器不这么做，但保留该能力）；
        - 传字典 → 落库。

        `signature` 同理：不传即保留旧值。**注意**：本编排器的三个调用点全部显式传
        `signature`（显式 `None` 也是显式），因为那三行 running 的语义正是
        「把签名写成 NULL」而不是「保留上一次的签名」——见 `_orchestrate` 的注释。
        `status`/`progress`/`message` 在仓储上没有哨兵语义（必填），故本方法也不给哨兵。

        `stats` 的本阶段形状仍是 dict 更新语义（浅合并，key 覆盖），`_UNSET`
        只影响「写不写」，不改变写入的值。
        """
        extra: dict[str, Any] = {}
        if stats is not _UNSET:
            extra["stats"] = stats
        if signature is not _UNSET:
            extra["signature"] = signature
        self._repo.save_section(
            project_id, section, status=status, progress=progress, message=message,
            updated_at=self._now(), **extra)
        states = self._section_states(project_id)
        ctx.progress(overall_progress(states), latest_analysis_message(states))

    # ---------------------------------------------------------------- 默认读法

    def _read_signature(self, project_id: str) -> str | None:
        """默认签名读取：库里的 `current_signature`。"""
        return (self._repo.get_meta(project_id) or {}).get("current_signature")
