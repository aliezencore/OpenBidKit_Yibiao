from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ModelConfig(BaseModel):
    base_url: str = ""
    api_key: str = ""
    model_name: str = ""
    context_limit: int = 120000
    # 超时必须为正数：0 会让每次模型请求瞬时失败（裁定 F3）。
    timeout_seconds: float = Field(default=300.0, gt=0)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="YIBIAO_",
        env_nested_delimiter="__",
        extra="ignore",
    )

    data_root: Path = Path("data")
    text_model: ModelConfig = ModelConfig()
    vision_model: ModelConfig = ModelConfig()
    # 并发下限必须为正整数：0 会让 Semaphore(0) 永久阻塞（AI 调用挂死、
    # 任务永远 queued、drain 不收敛）——配置期就应当直接失败（裁定 F3）。
    ai_max_concurrency: int = Field(default=8, ge=1)
    task_global_concurrency: int = Field(default=4, ge=1)
    # 0 表示「每次进度都落库」，是合法契约，故用 ge=0。
    progress_write_interval_ms: int = Field(default=200, ge=0)
    cpu_workers: int = Field(default=2, ge=1)
    sse_heartbeat_seconds: float = Field(default=15.0, gt=0)
    # 静态前端目录（方案 A「路由 + 静态前端」）；None = 缺省定位 `server/web/dist`。
    web_dist: Path | None = None
    # P2c：解析器选择（对齐参考 `config.components.file_parser.provider`）与 MinerU token。
    # 默认 `local` = 既有行为不变；选 MinerU 后仍会在「该 provider 不支持该扩展名」时
    # 回退本地（`resolve_parser`）。Literal 让拼错的 provider 在启动期就失败。
    file_parser_provider: Literal["local", "mineru-agent-api", "mineru-accurate-api"] = "local"
    mineru_token: str = ""
    # P4b-1：招标解析分段用的上下文长度上限（对齐参考 aiService.getConfig().context_length_limit）
    ai_context_length_limit: int = 400000

    @property
    def db_path(self) -> Path:
        return self.data_root / "yibiao_check.sqlite"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
