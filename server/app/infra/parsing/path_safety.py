"""project_id 安全净化守卫（P2a 终审 F1）——**叶子模块**。

从 `service.py` 下沉而来：原本 `validate_project_id` 定义于 `service.py` 末尾，而
`asset_store.py` 在模块顶层 `from ...service import validate_project_id`。一旦 P4 在
`service.py` 顶部加 `from ...asset_store import save_docx_assets`，就会形成
`service → asset_store → service` 的循环导入（名字尚未定义即被引用）。

把守卫挪到**不 import 任何 `parsing` 子模块**的叶子模块，两边都从这儿取，循环即断开。
行为必须字节级不变——它已经拒绝 `""`/`"."`/`".."` 与任何超出安全字符模式的 id，
既有测试钉住了这一点。
"""

from __future__ import annotations

import re

from app.domain.shared.errors import ParsingError

# 安全单段路径：大小写字母、数字、`. _ -`，非空。`.`/`..` 为特殊路径分量，
# 会触发目录穿越，单独排除（裁定 P2a 终审 F1）。
_SAFE_PROJECT_ID = re.compile(r"^[A-Za-z0-9._-]+$")
_SPECIAL_PATH_COMPONENTS = frozenset({"", ".", ".."})


def validate_project_id(project_id: str) -> None:
    """project_id 必须是一个安全的单段路径，不得触发目录穿越（裁定 P2a 终审 F1）。

    P3/P4 的上传端点会把 URL 路径参数喂进来，这里进行一次性的净化，而不是指望
    每个未来端点都记得。拒绝：空串、含 `..`/分隔符/绝对路径的非法字符、以及 `.`/`..`
    这类特殊路径分量。不静默改写 id。
    """
    if not isinstance(project_id, str) or project_id in _SPECIAL_PATH_COMPONENTS:
        raise ParsingError(
            f"非法的 project_id：{project_id!r}",
            code="invalid-project-id",
        )
    if not _SAFE_PROJECT_ID.match(project_id):
        raise ParsingError(
            f"非法的 project_id：{project_id!r}（只允许字母、数字、. _ -）",
            code="invalid-project-id",
        )
