import os

import pytest


@pytest.fixture(autouse=True)
def _scrub_ambient_yibiao_env(monkeypatch):
    """清除所有 YIBIAO_* 环境变量（裁定 F5）。

    `Settings(_env_file=None)` 仍会读取真实环境变量；若本机或 CI 导出了
    例如 `YIBIAO_AI_MAX_CONCURRENCY`，`test_config` 的默认断言会误伤。
    任何用例需要某个值时自行用 monkeypatch.setenv 设定。
    """
    for key in list(os.environ):
        if key.startswith("YIBIAO_"):
            monkeypatch.delenv(key, raising=False)
