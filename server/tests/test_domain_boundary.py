import ast
from pathlib import Path

from app.domain.shared.protocols import (
    AiClientProtocol,
    EventPublisherProtocol,
    TaskContextProtocol,
)

DOMAIN_DIR = Path(__file__).resolve().parents[1] / "app" / "domain"
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
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                if node.module.startswith("app.infra") or "/infra" in node.module:
                    offenders.append(f"{path.name} -> {node.module}")

    assert offenders == [], f"domain 层不得依赖 infra：{offenders}"


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
