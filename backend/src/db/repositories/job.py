from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from src.db.models import Job
from src.db.repositories.base import BaseRepository

_ESTADOS_TERMINAIS = ("finished", "failed", "canceled")


class JobRepository(BaseRepository):
    def create(
        self,
        *,
        viagem_nome: str,
        tipo: str,
        rq_job_id: str | None = None,
        viagem_id: uuid.UUID | None = None,
        status: str = "queued",
        meta: dict[str, Any] | None = None,
        progresso: dict[str, Any] | None = None,
    ) -> Job:
        job = Job(
            viagem_nome=viagem_nome,
            tipo=tipo,
            rq_job_id=rq_job_id,
            viagem_id=viagem_id,
            status=status,
            meta=dict(meta or {}),
            progresso=dict(progresso or {}),
        )
        self.session.add(job)
        self._flush()
        return job

    def get(self, job_id: uuid.UUID) -> Job | None:
        return self.session.get(Job, job_id)

    def get_by_rq_id(self, rq_job_id: str) -> Job | None:
        stmt = select(Job).where(
            Job.rq_job_id == rq_job_id, Job.deleted_at.is_(None)
        )
        return self.session.scalars(stmt).first()

    def mark_started(self, job_id: uuid.UUID) -> Job | None:
        job = self.get(job_id)
        if job is not None:
            job.status = "started"
            job.iniciado_em = datetime.now(timezone.utc)
            self._flush()
        return job

    def update_progress(
        self, job_id: uuid.UUID, progresso: dict[str, Any], status: str | None = None
    ) -> Job | None:
        job = self.get(job_id)
        if job is not None:
            job.progresso = {**(job.progresso or {}), **progresso}
            if status is not None:
                job.status = status
            self._flush()
        return job

    def finish(
        self,
        job_id: uuid.UUID,
        resultado: dict[str, Any] | None = None,
        status: str = "finished",
    ) -> Job | None:
        job = self.get(job_id)
        if job is not None:
            job.status = status
            job.resultado = resultado
            job.finalizado_em = datetime.now(timezone.utc)
            self._flush()
        return job

    def fail(
        self,
        job_id: uuid.UUID,
        erro: str,
        resultado: dict[str, Any] | None = None,
    ) -> Job | None:
        job = self.get(job_id)
        if job is not None:
            job.status = "failed"
            job.erro = erro
            job.resultado = resultado
            job.finalizado_em = datetime.now(timezone.utc)
            self._flush()
        return job

    def list_recent(
        self,
        *,
        limit: int = 50,
        viagem_id: uuid.UUID | None = None,
        tipo: str | None = None,
    ) -> list[Job]:
        stmt = select(Job).where(Job.deleted_at.is_(None))
        if viagem_id is not None:
            stmt = stmt.where(Job.viagem_id == viagem_id)
        if tipo is not None:
            stmt = stmt.where(Job.tipo == tipo)
        stmt = stmt.order_by(Job.created_at.desc()).limit(limit)
        return list(self.session.scalars(stmt))
