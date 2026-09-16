from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config import ModelConfig, Settings, get_settings


def test_defaults():
    settings = Settings(_env_file=None)

    assert settings.data_root == Path("data")
    assert settings.ai_max_concurrency == 8
    assert settings.task_global_concurrency == 4
    assert settings.progress_write_interval_ms == 200
    assert settings.cpu_workers == 2
    assert settings.sse_heartbeat_seconds == 15.0
    assert settings.text_model.model_name == ""
    assert settings.text_model.context_limit == 120000
    assert settings.text_model.timeout_seconds == 300.0


def test_reads_nested_model_config_from_env(monkeypatch):
    monkeypatch.setenv("YIBIAO_TEXT_MODEL__BASE_URL", "https://api.example.com/v1")
    monkeypatch.setenv("YIBIAO_TEXT_MODEL__API_KEY", "sk-text")
    monkeypatch.setenv("YIBIAO_TEXT_MODEL__MODEL_NAME", "gpt-4o-mini")
    monkeypatch.setenv("YIBIAO_VISION_MODEL__MODEL_NAME", "qwen-vl-max")
    monkeypatch.setenv("YIBIAO_AI_MAX_CONCURRENCY", "3")

    settings = Settings(_env_file=None)

    assert settings.text_model.base_url == "https://api.example.com/v1"
    assert settings.text_model.api_key == "sk-text"
    assert settings.text_model.model_name == "gpt-4o-mini"
    assert settings.vision_model.model_name == "qwen-vl-max"
    assert settings.ai_max_concurrency == 3


def test_get_settings_is_cached(monkeypatch):
    monkeypatch.setenv("YIBIAO_DATA_ROOT", "./tmpdata")
    get_settings.cache_clear()

    assert get_settings() is get_settings()

    get_settings.cache_clear()


@pytest.mark.parametrize(
    ("env_key", "value"),
    [
        # 并发下限下界为 1：0 会让 Semaphore(0) 永久阻塞（裁定 F3）。
        ("YIBIAO_AI_MAX_CONCURRENCY", "0"),
        ("YIBIAO_TASK_GLOBAL_CONCURRENCY", "0"),
        ("YIBIAO_CPU_WORKERS", "0"),
        # SSE 心跳必须为正数。
        ("YIBIAO_SSE_HEARTBEAT_SECONDS", "0"),
        # 进度写间隔允许 0（每次都写），但不允许负数。
        ("YIBIAO_PROGRESS_WRITE_INTERVAL_MS", "-1"),
        # 模型超时必须为正数。
        ("YIBIAO_TEXT_MODEL__TIMEOUT_SECONDS", "0"),
        ("YIBIAO_VISION_MODEL__TIMEOUT_SECONDS", "0"),
    ],
)
def test_config_rejects_invalid_bounds(monkeypatch, env_key, value):
    monkeypatch.setenv(env_key, value)

    with pytest.raises(ValidationError):
        Settings(_env_file=None)
