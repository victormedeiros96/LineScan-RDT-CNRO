from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select, update

from src.db.models import ResultadoAnalise
from src.db.repositories.base import BaseRepository


class ResultadoAnaliseRepository(BaseRepository):
    def get(
        self,
        viagem_id: uuid.UUID,
        km: int,
        tipo_analise: str,
        amostra: str | None = None,
    ) -> ResultadoAnalise | None:
        stmt = select(ResultadoAnalise).where(
            ResultadoAnalise.viagem_id == viagem_id,
            ResultadoAnalise.km == km,
            ResultadoAnalise.tipo_analise == tipo_analise,
            ResultadoAnalise.deleted_at.is_(None),
        )
        if amostra is None:
            stmt = stmt.where(ResultadoAnalise.amostra.is_(None))
        else:
            stmt = stmt.where(ResultadoAnalise.amostra == amostra)
        return self.session.scalars(stmt).first()

    def upsert(
        self,
        *,
        viagem_id: uuid.UUID,
        km: int,
        tipo_analise: str,
        valor: float | Decimal,
        amostra: str | None = None,
        norma: str | None = None,
        conceito: str | None = None,
        n_estacoes: int | None = None,
        detalhe: dict[str, Any] | None = None,
    ) -> ResultadoAnalise:
        resultado = self.get(viagem_id, km, tipo_analise, amostra)
        if resultado is None:
            resultado = ResultadoAnalise(
                viagem_id=viagem_id,
                km=km,
                tipo_analise=tipo_analise,
                amostra=amostra,
                valor=valor,
            )
            self.session.add(resultado)
        resultado.valor = valor
        resultado.norma = norma
        resultado.conceito = conceito
        resultado.n_estacoes = n_estacoes
        resultado.detalhe = dict(detalhe or {})
        self._flush()
        return resultado

    def soft_delete_except(
        self,
        viagem_id: uuid.UUID,
        tipo_analise: str,
        manter: set[uuid.UUID],
    ) -> int:
        """Marca como removidos os resultados do tipo que saíram do conjunto."""
        stmt = (
            update(ResultadoAnalise)
            .where(
                ResultadoAnalise.viagem_id == viagem_id,
                ResultadoAnalise.tipo_analise == tipo_analise,
                ResultadoAnalise.deleted_at.is_(None),
            )
            .values(deleted_at=func.now())
        )
        if manter:
            stmt = stmt.where(ResultadoAnalise.id.notin_(list(manter)))
        resultado = self.session.execute(stmt)
        return resultado.rowcount or 0

    def list_by_viagem(
        self, viagem_id: uuid.UUID, tipo_analise: str | None = None
    ) -> list[ResultadoAnalise]:
        stmt = select(ResultadoAnalise).where(
            ResultadoAnalise.viagem_id == viagem_id,
            ResultadoAnalise.deleted_at.is_(None),
        )
        if tipo_analise is not None:
            stmt = stmt.where(ResultadoAnalise.tipo_analise == tipo_analise)
        stmt = stmt.order_by(ResultadoAnalise.km, ResultadoAnalise.amostra)
        return list(self.session.scalars(stmt))
