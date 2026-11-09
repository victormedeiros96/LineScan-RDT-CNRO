from __future__ import annotations

import uuid
from collections.abc import Iterable
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select

from src.db.models import Deteccao, Faixa
from src.db.repositories.base import BaseRepository

_CAMPOS = (
    "classe",
    "area_pixels",
    "area_m2",
    "confidence",
    "confidence_max",
    "linha",
    "coluna",
    "global_box",
    "global_polygon",
    "origem",
)


def _normalizar(item: dict[str, Any]) -> dict[str, Any]:
    return {campo: item[campo] for campo in _CAMPOS if campo in item}


class DeteccaoRepository(BaseRepository):
    def list_by_faixa(self, faixa_id: uuid.UUID) -> list[Deteccao]:
        stmt = select(Deteccao).where(
            Deteccao.faixa_id == faixa_id, Deteccao.deleted_at.is_(None)
        )
        return list(self.session.scalars(stmt))

    def replace_for_faixa(
        self, faixa_id: uuid.UUID, deteccoes: Iterable[dict[str, Any]]
    ) -> list[Deteccao]:
        """Substitui (soft delete + inserção) todas as detecções de uma faixa.

        Tudo dentro da transação corrente — a inferência grava por faixa numa
        transação curta, sem deixar estado parcial.
        """
        agora = datetime.now(timezone.utc)
        atuais = self.session.scalars(
            select(Deteccao).where(
                Deteccao.faixa_id == faixa_id, Deteccao.deleted_at.is_(None)
            )
        )
        for deteccao in atuais:
            deteccao.deleted_at = agora

        novas: list[Deteccao] = []
        for item in deteccoes:
            deteccao = Deteccao(faixa_id=faixa_id, **_normalizar(item))
            self.session.add(deteccao)
            novas.append(deteccao)

        self._flush()
        return novas

    def count_by_viagem_classe(self, viagem_id: uuid.UUID, classe: str) -> int:
        stmt = (
            select(func.count(Deteccao.id))
            .join(Faixa, Faixa.id == Deteccao.faixa_id)
            .where(
                Faixa.viagem_id == viagem_id,
                Deteccao.classe == classe,
                Deteccao.deleted_at.is_(None),
                Faixa.deleted_at.is_(None),
            )
        )
        return int(self.session.scalar(stmt) or 0)
