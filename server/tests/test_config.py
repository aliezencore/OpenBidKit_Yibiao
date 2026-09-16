from pathlib import Path

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
