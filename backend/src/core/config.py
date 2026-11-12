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
    database_url: str = ""
    job_retention_days: int = 30
    # Tempo ocioso (segundos) antes de descarregar modelos e liberar VRAM.
    # 0 desabilita a descarga automática. Padrão: 5 min.
    model_idle_ttl_seconds: int = 300

    # Juntas do pavimento rígido (ICP): modelo externo trinca-detector (GPL-3.0).
    # Podem ser sobrescritos pelo bloco "juntas" de modelos/icp/config.json.
    icp_nuclio_dir: Path = Path("/opt/trinca-detector/nuclio")
    icp_modelo_pt: Path = Path(
        "/opt/trinca-detector/nuclio/modelos/trinca_junta_limpo_ts.pt"
    )
    icp_limiar_juntas: float = 0.8
    icp_ordem_invertida: bool = True
    icp_mm_por_px_y: float = 2000.0 / 1024.0

    host: str = "0.0.0.0"
    port: int = 8000


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
