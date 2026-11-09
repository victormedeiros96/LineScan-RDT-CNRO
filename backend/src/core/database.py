from __future__ import annotations

import logging

from src.core.config import get_settings

logger = logging.getLogger(__name__)


def check_database() -> bool:
    """Testa se o banco configurado aceita conexão (usado pelo endpoint de saúde)."""
    database_url = get_settings().database_url
    if not database_url:
        return False

    try:
        import psycopg
    except ImportError:
        logger.warning("psycopg não instalado; não foi possível checar o banco")
        return False

    try:
        with psycopg.connect(database_url, connect_timeout=3) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
        return True
    except Exception as exc:
        logger.warning("Falha ao conectar ao banco: %s", exc)
        return False
