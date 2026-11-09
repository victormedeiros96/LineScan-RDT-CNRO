from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from src.core.config import get_settings

logger = logging.getLogger(__name__)

_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def _database_url() -> str:
    url = get_settings().database_url
    if not url:
        raise RuntimeError(
            "DATABASE_URL não configurada. Defina a variável de ambiente "
            "(ex.: postgresql://usuario:senha@host:5432/banco) ou rode dentro do compose."
        )
    return url


def get_engine() -> Engine:
    """Engine compartilhada (lazy), reutilizada por API e worker."""
    global _engine
    if _engine is None:
        _engine = create_engine(_database_url(), pool_pre_ping=True, future=True)
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    global _session_factory
    if _session_factory is None:
        _session_factory = sessionmaker(bind=get_engine(), expire_on_commit=False, future=True)
    return _session_factory


@contextmanager
def session_scope() -> Iterator[Session]:
    """Uma transação por bloco: commit no sucesso, rollback na exceção.

    Repositórios não fazem commit — quem controla a transação é este contexto
    (ou a dependência do FastAPI em `get_session`).
    """
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_session() -> Iterator[Session]:
    """Dependência do FastAPI: uma sessão/transação por requisição."""
    with session_scope() as session:
        yield session


def dispose_engine() -> None:
    """Fecha a engine e limpa os singletons (útil em testes e shutdown)."""
    global _engine, _session_factory
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _session_factory = None
