from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import select

from src.db.models import ResultadoAnalise
from src.db.repositories.base import BaseRepository


class ResultadoAnaliseRepository(BaseRepository):
    def get(
        self, viagem_id: uuid.UUID, km: int, tipo_analise: str
    ) -> ResultadoAnalise | None:
        stmt = select(ResultadoAnalise).where(
            ResultadoAnalise.viagem_id == viagem_id,
            ResultadoAnalise.km == km,
            ResultadoAnalise.tipo_analise == tipo_analise,
            ResultadoAnalise.deleted_at.is_(None),
        )
        return self.session.scalars(stmt).first()

    def upsert(
        self,
        *,
        viagem_id: uuid.UUID,
        km: int,
        tipo_analise: str,
        valor: float | Decimal,
        norma: str | None = None,
        conceito: str | None = None,
        n_estacoes: int | None = None,
        detalhe: dict[str, Any] | None = None,
    ) -> ResultadoAnalise:
        resultado = self.get(viagem_id, km, tipo_analise)
        if resultado is None:
            resultado = ResultadoAnalise(
                viagem_id=viagem_id, km=km, tipo_analise=tipo_analise, valor=valor
            )
            self.session.add(resultado)
        resultado.valor = valor
        resultado.norma = norma
        resultado.conceito = conceito
        resultado.n_estacoes = n_estacoes
        resultado.detalhe = dict(detalhe or {})
        self._flush()
        return resultado

    def list_by_viagem(
        self, viagem_id: uuid.UUID, tipo_analise: str | None = None
    ) -> list[ResultadoAnalise]:
        stmt = select(ResultadoAnalise).where(
            ResultadoAnalise.viagem_id == viagem_id,
            ResultadoAnalise.deleted_at.is_(None),
        )
        if tipo_analise is not None:
            stmt = stmt.where(ResultadoAnalise.tipo_analise == tipo_analise)
        stmt = stmt.order_by(ResultadoAnalise.km)
        return list(self.session.scalars(stmt))
