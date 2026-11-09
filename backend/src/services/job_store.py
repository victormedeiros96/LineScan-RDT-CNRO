"""Histórico de jobs no banco (RDT1-13).

Terceiro slice vertical da migração de persistência. O Redis/RQ continua sendo a
fila, mas o histórico (tipo, status, meta, progresso, resultado, erro e
timestamps) passa a viver em `job`, sobrevivendo a um flush do Redis.

Regras:

* O registro é criado na API no momento do enqueue, ligado ao `rq_job_id`. O
  worker atualiza o ciclo de vida (`started` -> `finished`/`failed`).
* Todas as operações são *best-effort*: sem banco configurado/indisponível a API
  e o worker não quebram, apenas registram aviso (fallback temporário).
* A leitura devolve ``None`` quando o banco não está disponível, para a API cair
  no histórico legado do Redis.
* Jobs antigos já finalizados são marcados com soft delete (``deleted_at``) para
  retenção; o histórico recente continua consultável.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any, TypeVar

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from src.core.config import get_settings
from src.db.models import Job
from src.db.repositories import JobRepository, ViagemRepository
from src.db.session import session_scope

logger = logging.getLogger(__name__)

T = TypeVar("T")


def _viagem_id(session: Session, viagem_nome: str) -> uuid.UUID | None:
    viagem = ViagemRepository(session).get_by_pasta(viagem_nome)
    return viagem.id if viagem is not None else None


def registrar_job_em_sessao(
    session: Session,
    *,
    rq_job_id: str | None,
    viagem_nome: str,
    tipo: str,
    meta: dict[str, Any] | None = None,
    progresso: dict[str, Any] | None = None,
    status: str = "queued",
) -> Job:
    """Cria (ou devolve) o registro do job na transação corrente."""
    repo = JobRepository(session)
    if rq_job_id:
        existente = repo.get_by_rq_id(rq_job_id)
        if existente is not None:
            return existente
    return repo.create(
        viagem_nome=viagem_nome,
        tipo=tipo,
        rq_job_id=rq_job_id,
        viagem_id=_viagem_id(session, viagem_nome),
        status=status,
        meta=meta,
        progresso=progresso,
    )


def _em_sessao(acao: Callable[[Session], T]) -> T | None:
    try:
        with session_scope() as session:
            return acao(session)
    except (RuntimeError, SQLAlchemyError) as exc:
        logger.warning("Banco indisponível para o histórico de jobs: %s", exc)
        return None


def registrar_job(
    *,
    rq_job_id: str | None,
    viagem_nome: str,
    tipo: str,
    meta: dict[str, Any] | None = None,
    progresso: dict[str, Any] | None = None,
) -> None:
    """Registra o job no banco (chamado pela API no enqueue)."""
    _em_sessao(
        lambda session: registrar_job_em_sessao(
            session,
            rq_job_id=rq_job_id,
            viagem_nome=viagem_nome,
            tipo=tipo,
            meta=meta,
            progresso=progresso,
        )
    )


def marcar_iniciado_em_sessao(session: Session, rq_job_id: str) -> Job | None:
    job = JobRepository(session).get_by_rq_id(rq_job_id)
    if job is None:
        return None
    return JobRepository(session).mark_started(job.id)


def marcar_iniciado(rq_job_id: str | None) -> None:
    if not rq_job_id:
        return
    _em_sessao(lambda session: marcar_iniciado_em_sessao(session, rq_job_id))


def atualizar_progresso_em_sessao(
    session: Session, rq_job_id: str, progresso: dict[str, Any]
) -> Job | None:
    repo = JobRepository(session)
    job = repo.get_by_rq_id(rq_job_id)
    if job is None:
        return None
    return repo.update_progress(job.id, progresso)


def atualizar_progresso(rq_job_id: str | None, progresso: dict[str, Any]) -> None:
    if not rq_job_id:
        return
    _em_sessao(lambda session: atualizar_progresso_em_sessao(session, rq_job_id, progresso))


def finalizar_em_sessao(
    session: Session, rq_job_id: str, resultado: dict[str, Any] | None
) -> Job | None:
    repo = JobRepository(session)
    job = repo.get_by_rq_id(rq_job_id)
    if job is None:
        return None
    return repo.finish(job.id, resultado=resultado)


def finalizar(rq_job_id: str | None, resultado: dict[str, Any] | None) -> None:
    if not rq_job_id:
        return
    _em_sessao(lambda session: finalizar_em_sessao(session, rq_job_id, resultado))


def falhar_em_sessao(
    session: Session, rq_job_id: str, erro: str
) -> Job | None:
    repo = JobRepository(session)
    job = repo.get_by_rq_id(rq_job_id)
    if job is None:
        return None
    return repo.fail(job.id, erro)


def falhar(rq_job_id: str | None, erro: str) -> None:
    if not rq_job_id:
        return
    _em_sessao(lambda session: falhar_em_sessao(session, rq_job_id, erro))


def remover_job(rq_job_id: str) -> bool:
    """Soft delete do registro do job (usado ao remover um processamento)."""
    resultado = _em_sessao(
        lambda session: JobRepository(session).soft_delete_by_rq_id(rq_job_id)
    )
    return bool(resultado)


def _job_dict(job: Job) -> dict[str, Any]:
    progresso = job.progresso or {}
    entrada: dict[str, Any] = {
        "job_id": job.rq_job_id or str(job.id),
        "status": job.status,
        "tipo": job.tipo,
        "meta": job.meta or {},
        "progress": {
            "current_lote": progresso.get("current_lote"),
            "total_lotes": progresso.get("total_lotes"),
            "progress_msg": progresso.get("progress_msg"),
        }
        if progresso
        else None,
        "criado_em": job.created_at.isoformat() if job.created_at else None,
        "iniciado_em": job.iniciado_em.isoformat() if job.iniciado_em else None,
        "finalizado_em": job.finalizado_em.isoformat() if job.finalizado_em else None,
        "tempo_total": job.finalizado_em.isoformat() if job.finalizado_em else None,
    }
    if job.resultado is not None:
        entrada["resultado"] = job.resultado
    if job.erro:
        entrada["error"] = job.erro
    return entrada


def _ler_jobs(session: Session, limit: int) -> list[dict[str, Any]]:
    return [_job_dict(job) for job in JobRepository(session).list_recent(limit=limit)]


def listar_jobs(
    *, limit: int = 50, session: Session | None = None
) -> list[dict[str, Any]] | None:
    """Histórico recente vindo do banco, ou ``None`` para o fallback Redis."""
    if session is not None:
        return _ler_jobs(session, limit)
    try:
        with session_scope() as own_session:
            return _ler_jobs(own_session, limit)
    except (RuntimeError, SQLAlchemyError) as exc:
        logger.warning("Banco indisponível para listar jobs: %s", exc)
        return None


def _ler_job(session: Session, rq_job_id: str) -> dict[str, Any] | None:
    job = JobRepository(session).get_by_rq_id(rq_job_id)
    return _job_dict(job) if job is not None else None


def obter_job(
    rq_job_id: str, session: Session | None = None
) -> dict[str, Any] | None:
    """Job do banco pelo id do RQ, ou ``None`` para o fallback Redis."""
    if session is not None:
        return _ler_job(session, rq_job_id)
    try:
        with session_scope() as own_session:
            return _ler_job(own_session, rq_job_id)
    except (RuntimeError, SQLAlchemyError) as exc:
        logger.warning("Banco indisponível para ler o job %s: %s", rq_job_id, exc)
        return None


def limpar_jobs_antigos_em_sessao(
    session: Session, *, dias: int | None = None, agora: datetime | None = None
) -> int:
    dias = get_settings().job_retention_days if dias is None else dias
    limite = (agora or datetime.now(timezone.utc)) - timedelta(days=dias)
    return JobRepository(session).soft_delete_older_than(limite)


def limpar_jobs_antigos(
    *, dias: int | None = None, session: Session | None = None
) -> int | None:
    """Aplica a retenção: soft delete em jobs criados antes da janela."""
    if session is not None:
        return limpar_jobs_antigos_em_sessao(session, dias=dias)
    resultado = _em_sessao(lambda own: limpar_jobs_antigos_em_sessao(own, dias=dias))
    return resultado if resultado is not None else None
