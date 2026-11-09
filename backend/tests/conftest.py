"""Fixtures de banco para os testes de integração.

Banco efêmero: usa `DATABASE_TEST_URL` quando definido (ex.: serviço postgres do
CI/compose); caso contrário, sobe um PostgreSQL temporário com `pgserver`, se
disponível. Sem nenhum dos dois, os testes de banco são pulados.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Iterator

import pytest
from db_test_utils import BACKEND_DIR, create_database, drop_database
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker


@pytest.fixture(scope="session")
def database_server_url() -> Iterator[str]:
    url = os.environ.get("DATABASE_TEST_URL")
    if url:
        yield url
        return

    try:
        import pgserver
    except ImportError:
        pytest.skip(
            "Sem banco de teste: defina DATABASE_TEST_URL ou instale `pgserver`."
        )

    data_dir = tempfile.mkdtemp(prefix="rdt01_pg_")
    server = pgserver.get_server(data_dir)
    try:
        yield server.get_uri()
    finally:
        try:
            server.cleanup()
        except Exception:  # pragma: no cover - limpeza best-effort
            pass


@pytest.fixture(scope="session")
def database_url(database_server_url: str) -> Iterator[str]:
    dbname, url = create_database(database_server_url, "rdt01_test")
    try:
        yield str(url)
    finally:
        drop_database(database_server_url, dbname)


@pytest.fixture(scope="session")
def engine(database_url: str) -> Iterator[Engine]:
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.attributes["sqlalchemy_url"] = database_url
    command.upgrade(cfg, "head")

    eng = create_engine(database_url, future=True)
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture()
def session(engine: Engine) -> Iterator[Session]:
    """Sessão dentro de uma transação externa que é revertida ao fim do teste."""
    connection = engine.connect()
    trans = connection.begin()
    factory = sessionmaker(bind=connection, expire_on_commit=False)
    sess = factory()
    try:
        yield sess
    finally:
        sess.close()
        trans.rollback()
        connection.close()
