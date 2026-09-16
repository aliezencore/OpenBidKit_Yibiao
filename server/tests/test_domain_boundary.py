import ast
from pathlib import Path

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
        package_parts = package_parts_for(path)
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                target = absolute_import_target(package_parts, node.level, node.module)
                if target and is_infra_target(target):
                    offenders.append(f"{path.name} -> {target}")

    assert offenders == [], f"domain 层不得依赖 infra：{offenders}"


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
