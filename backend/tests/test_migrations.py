from __future__ import annotations

from db_test_utils import BACKEND_DIR, create_database, drop_database
from sqlalchemy import create_engine, text

_TABELAS_DOMINIO = {
    "projeto",
    "viagem",
    "bloco",
    "faixa",
    "deteccao",
    "parametro_km",
    "job",
    "resultado_analise",
    "arquivo_upload",
}


def _tabelas(url: str) -> set[str]:
    eng = create_engine(url, future=True)
    try:
        with eng.connect() as conn:
            return set(
                conn.execute(
                    text(
                        "select tablename from pg_tables where schemaname = 'public'"
                    )
                ).scalars()
            )
    finally:
        eng.dispose()


def _funcao_existe(url: str, nome: str) -> bool:
    eng = create_engine(url, future=True)
    try:
        with eng.connect() as conn:
            total = conn.execute(
                text("select count(*) from pg_proc where proname = :nome"),
                {"nome": nome},
            ).scalar()
        return bool(total)
    finally:
        eng.dispose()


def test_migracao_aplica_e_reverte(database_server_url: str) -> None:
    from alembic import command
    from alembic.config import Config

    dbname, url = create_database(database_server_url, "rdt01_mig")
    url_str = str(url)
    try:
        cfg = Config(str(BACKEND_DIR / "alembic.ini"))
        cfg.attributes["sqlalchemy_url"] = url_str

        command.upgrade(cfg, "head")
        tabelas = _tabelas(url_str)
        assert _TABELAS_DOMINIO <= tabelas
        assert "alembic_version" in tabelas
        assert _funcao_existe(url_str, "set_updated_at")

        command.downgrade(cfg, "base")
        tabelas = _tabelas(url_str)
        assert not (_TABELAS_DOMINIO & tabelas)
        assert not _funcao_existe(url_str, "set_updated_at")
    finally:
        drop_database(database_server_url, dbname)
