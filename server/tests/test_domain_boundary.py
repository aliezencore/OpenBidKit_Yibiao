import ast
from pathlib import Path

import pytest

from app.domain.shared.protocols import (
    AiClientProtocol,
    EventPublisherProtocol,
    TaskContextProtocol,
)

DOMAIN_DIR = Path(__file__).resolve().parents[1] / "app" / "domain"
PROJECT_ROOT = DOMAIN_DIR.parents[1]
FORBIDDEN_ROOTS = {
    "fastapi",
    "starlette",
    "sqlalchemy",
    "httpx",
    "pydantic",
    "pydantic_settings",
    "sqlite3",
    "uvicorn",
}


def iter_domain_modules():
    return sorted(path for path in DOMAIN_DIR.rglob("*.py"))


def imported_roots(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
    return roots


def package_parts_for(path: Path) -> tuple[str, ...]:
    """由文件路径推出其所在包的分段。

    例如 `server/app/domain/shared/protocols.py` -> `("app", "domain", "shared")`。
    """
    relative = path.resolve().relative_to(PROJECT_ROOT)
    return relative.with_suffix("").parts[:-1]


def absolute_import_target(
    package_parts: tuple[str, ...], level: int, module: str | None
) -> str | None:
    """把一条 import-from 语句解析为绝对点分目标。

    `level` 为 `ast.ImportFrom.level`：0 为绝对导入，1 为 `.x`、2 为 `..x`、3 为 `...x`。
    `module` 可为 None（`from . import x`）。过深的相对导入是畸形语句，返回 None，
    而不是靠负索引拼出一个错误的目标。
    """
    if level == 0:
        return module
    keep = len(package_parts) - (level - 1)
    if keep <= 0:
        return None
    parts = list(package_parts[:keep])
    if module:
        parts.append(module)
    return ".".join(parts)


def is_infra_target(target: str) -> bool:
    return target == "app.infra" or target.startswith("app.infra.")


def infra_import_offenders(
    source: str, package_parts: tuple[str, ...], filename: str | None = None
) -> list[str]:
    """返回源码中依赖 `app.infra` 的导入目标（均已解析为绝对点分名）。

    `package_parts` 是包含这些语句的文件所在包的分段，仅供相对导入换算使用。
    `filename` 透传给 `ast.parse`，使语法错误的定位能命名文件（裁定 M26 恢复）。

    覆盖两种语句形式，且共用同一个 `is_infra_target` 判定 —— 不引入第二套
    「什么算 infra」的标准：

    - `ast.Import`（`import app.infra.db.engine`）：`alias.name` 本身已是绝对点分
      模块路径，无需层次换算，直接判定；
    - `ast.ImportFrom`（`from ...infra.db.engine import X`）：先由
      `absolute_import_target` 把相对目标换算成绝对点分名，再判定。
    """
    tree = ast.parse(source, filename=filename or "<unknown>")
    offenders: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if is_infra_target(alias.name):
                    offenders.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            target = absolute_import_target(package_parts, node.level, node.module)
            if target and is_infra_target(target):
                offenders.append(target)
    return offenders


def _is_foreign_app_target(target: str) -> bool:
    """命名空间规则（裁定 F1）：以 `app.` 开头、但不以 `app.domain.` 开头的
    点分目标，即「domain 依赖了另一个 app 子包」，是违规。"""
    return target.startswith("app.") and not target.startswith("app.domain.")


def foreign_app_import_offenders(
    source: str, package_parts: tuple[str, ...], filename: str | None = None
) -> list[str]:
    """命名空间规则：domain 不得 import 任何非 `app.domain.` 的 app 子包。

    取代原先只盯 `app.infra` 的黑名单思路（裁定 F1）。`from app.config import
    Settings`、`from app import infra`、`import app.infrastructure` 这类「兄弟包」依赖
    都会落网；而 `from app.domain...` 与域内的相对导入则一律放行。第三方框架仍由
    `test_domain_does_not_import_frameworks` 的黑名单负责，二者互补。

    - `ast.Import`（`import app.config`）：`alias.name` 已是绝对点分目标，直接判定；
    - `ast.ImportFrom` 绝对导入（`from app.config import Settings`）：若被导入的
      `module` 本身就是外来 app 子包（如 `app.config`），整段依赖已坐实，报 `module`
      即可；若 `module` 不违规（如顶层 `app`），则再逐个检查 `module.<name>` 候选
      （`from app import infra` 实际加载的是 `app.infra`，必须这样才拦得住）；
    - `ast.ImportFrom` 相对导入（`from ..shared.protocols import X`）：先由
      `absolute_import_target` 换算成绝对点分名再判定。
    """
    tree = ast.parse(source, filename=filename or "<unknown>")
    offenders: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if _is_foreign_app_target(alias.name):
                    offenders.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                if _is_foreign_app_target(node.module):
                    offenders.append(node.module)
                else:
                    for alias in node.names:
                        if alias.name != "*":
                            candidate = f"{node.module}.{alias.name}"
                            if _is_foreign_app_target(candidate):
                                offenders.append(candidate)
            elif node.level >= 1:
                target = absolute_import_target(package_parts, node.level, node.module)
                if target and _is_foreign_app_target(target):
                    offenders.append(target)
    return offenders


def test_domain_modules_exist():
    assert iter_domain_modules(), "domain 目录下应当存在模块"


def test_domain_does_not_import_frameworks():
    offenders = {}
    for path in iter_domain_modules():
        bad = imported_roots(path) & FORBIDDEN_ROOTS
        if bad:
            offenders[path.name] = sorted(bad)

    assert offenders == {}, f"domain 层不得依赖框架：{offenders}"


def test_domain_does_not_import_infra():
    offenders = []
    for path in iter_domain_modules():
        source = path.read_text(encoding="utf-8")
        targets = infra_import_offenders(source, package_parts_for(path), filename=str(path))
        offenders.extend(f"{path.name} -> {target}" for target in targets)

    assert offenders == [], f"domain 层不得依赖 infra：{offenders}"


def test_domain_does_not_import_other_app_packages():
    """命名空间规则（裁定 F1）：domain 不得 import 任何非 app.domain 的兄弟 app 子包。

    黑名单式的 `is_infra_target` 拦不住 `from app.config import Settings`、
    `from app import infra` 这类兄弟包依赖；这里用命名空间规则一网打尽。
    """
    offenders = []
    for path in iter_domain_modules():
        source = path.read_text(encoding="utf-8")
        targets = foreign_app_import_offenders(
            source, package_parts_for(path), filename=str(path)
        )
        offenders.extend(f"{path.name} -> {target}" for target in targets)

    assert offenders == [], f"domain 层不得依赖其他 app 子包：{offenders}"


def test_resolver_keeps_absolute_import_unchanged():
    target = absolute_import_target(("app", "domain", "shared"), 0, "app.infra.db.engine")

    assert target == "app.infra.db.engine"


def test_resolver_maps_level_2_to_the_enclosing_package():
    parts = package_parts_for(PROJECT_ROOT / "app" / "domain" / "shared" / "protocols.py")

    assert parts == ("app", "domain", "shared")
    assert absolute_import_target(parts, 2, "rejection") == "app.domain.rejection"


def test_resolver_maps_level_3_from_domain_shared_to_app_root():
    parts = package_parts_for(PROJECT_ROOT / "app" / "domain" / "shared" / "protocols.py")

    target = absolute_import_target(parts, 3, "infra.db.engine")

    assert target == "app.infra.db.engine"
    assert is_infra_target(target)


def test_resolver_keeps_within_domain_relative_import_inside_domain():
    parts = package_parts_for(PROJECT_ROOT / "app" / "domain" / "rejection" / "foo.py")

    target = absolute_import_target(parts, 2, "shared.protocols")

    assert target == "app.domain.shared.protocols"
    assert not is_infra_target(target)


def test_resolver_returns_none_for_over_deep_level():
    parts = ("app", "domain", "shared")

    assert absolute_import_target(parts, 4, "infra") is None
    assert absolute_import_target(parts, 99, "infra") is None


def test_resolver_handles_missing_module():
    assert absolute_import_target(("app", "domain", "shared"), 1, None) == "app.domain.shared"
    assert absolute_import_target(("app", "domain", "shared"), 0, None) is None


# `app/domain/shared/` 下文件的包分段，供下表中相对导入的用例换算。
DOMAIN_SHARED = ("app", "domain", "shared")


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        # --- ast.Import：裁定 R11 新增覆盖 ---
        ("import app.infra.db.engine", ["app.infra.db.engine"]),
        ("import app.infra", ["app.infra"]),
        ("import app.infra.db.engine as engine", ["app.infra.db.engine"]),
        ("import app\nimport app.infra\n", ["app.infra"]),
        ("import os", []),
        ("import json", []),
        ("import os, json", []),
        ("import app", []),
        # 同前缀不同包：`app.infrastructure` 不是 `app.infra`，不得误报
        ("import app.infrastructure", []),
        ("import app.infrastructure.db", []),
        # --- ast.ImportFrom：R10 已覆盖的路径，并入同一张表 ---
        ("from app.infra.db.engine import Database", ["app.infra.db.engine"]),
        ("from app.infra import db", ["app.infra"]),
        ("from ...infra.db.engine import Database", ["app.infra.db.engine"]),
        ("from ...infra import db", ["app.infra"]),
        # 域内相对导入解析到 app.domain 之下，不是 infra 违规
        ("from ..shared.protocols import TaskContextProtocol", []),
        # 越过顶层包的畸形相对导入：不崩，也不误报
        ("from ....infra import db", []),
        ("from app.infrastructure import db", []),
    ],
)
def test_infra_guard_flags_import_statements(source, expected):
    assert infra_import_offenders(source, DOMAIN_SHARED) == expected


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        # 兄弟包绝对导入：`module` 本身即外来 app 子包，整段依赖坐实，报 module 即可。
        ("from app.config import Settings", ["app.config"]),
        # `module` 不违规（顶层 app），须逐个检查 `module.<name>` 才能拦住 app.infra。
        ("from app import infra", ["app.infra"]),
        ("import app.infrastructure", ["app.infrastructure"]),
        ("import app.config.db", ["app.config.db"]),
        # 域外相对导入（越过 domain 顶层）换算后落到 app.infra，必须拦下。
        ("from ...infra.db.engine import Database", ["app.infra.db.engine"]),
        # 框架（第三方）不在本规则管辖范围——交给 FORBIDDEN_ROOTS 黑名单。
        ("import pydantic", []),
        ("from fastapi import FastAPI", []),
        # domain 内部依赖一律放行。
        ("from app.domain.shared.protocols import TaskContextProtocol", []),
        ("from app.domain.shared import protocols", []),
        ("from ..shared.protocols import TaskContextProtocol", []),
        ("from . import something", []),
        # 同前缀不同包：`app.infrastructure` 拦下，但 `app.domain` 绝不误报。
        ("from app.domain.rejection import X", []),
    ],
)
def test_foreign_app_guard_flags_sibling_app_imports(source, expected):
    result = foreign_app_import_offenders(source, DOMAIN_SHARED)
    assert sorted(set(result)) == sorted(set(expected)), result


class FakeContext:
    def __init__(self):
        self.progress_calls = []
        self.logs = []
        self.checkpoints = []
        self.cancelled = False

    def progress(self, percent, label=None):
        self.progress_calls.append((percent, label))

    def log(self, message):
        self.logs.append(message)

    def checkpoint(self, state):
        self.checkpoints.append(state)

    def raise_if_cancelled(self):
        if self.cancelled:
            raise RuntimeError("cancelled")


class FakeAi:
    async def chat(self, messages, *, use_vision=False):
        return "{}"

    async def collect_json_response(self, messages, *, use_vision=False):
        return {}


class FakePublisher:
    def publish(self, task_id, event):
        pass


def test_fakes_satisfy_protocols():
    assert isinstance(FakeContext(), TaskContextProtocol)
    assert isinstance(FakeAi(), AiClientProtocol)
    assert isinstance(FakePublisher(), EventPublisherProtocol)
