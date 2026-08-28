from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = "RDT01 Backend"
    debug: bool = False

    cors_origins: list[str] = ["*"]

    modelos_dir: Path = Path("/app/modelos")
    dados_dir: Path = Path("/app/dados")
    sources_config: Path = Path("/app/config/sources.json")

    redis_url: str = "redis://redis:6379/0"

    host: str = "0.0.0.0"
    port: int = 8000


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
