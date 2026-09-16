from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict


class ModelConfig(BaseModel):
    base_url: str = ""
    api_key: str = ""
    model_name: str = ""
    context_limit: int = 120000
    timeout_seconds: float = 300.0


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="YIBIAO_",
        env_nested_delimiter="__",
        extra="ignore",
    )

    data_root: Path = Path("data")
    text_model: ModelConfig = ModelConfig()
    vision_model: ModelConfig = ModelConfig()
    ai_max_concurrency: int = 8
    task_global_concurrency: int = 4
    progress_write_interval_ms: int = 200
    cpu_workers: int = 2
    sse_heartbeat_seconds: float = 15.0

    @property
    def db_path(self) -> Path:
        return self.data_root / "yibiao_check.sqlite"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
