from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, select

from src.db.models import ParametroKm
from src.db.repositories.base import BaseRepository


class ParametroKmRepository(BaseRepository):
    def get(self, viagem_id: uuid.UUID, km: int) -> ParametroKm | None:
        stmt = select(ParametroKm).where(
            ParametroKm.viagem_id == viagem_id,
            ParametroKm.km == km,
            ParametroKm.deleted_at.is_(None),
        )
        return self.session.scalars(stmt).first()

    def upsert(
        self,
        *,
        viagem_id: uuid.UUID,
        km: int,
        tri_serie: list[float] | None = None,
        tre_serie: list[float] | None = None,
        extras: dict[str, Any] | None = None,
    ) -> ParametroKm:
        """Upsert por `(viagem_id, km)`.

        Preserva campos já existentes (R2: o editor substituía o objeto inteiro).
        `extras` é mesclado, não sobrescrito, para não perder parâmetros desconhecidos.
        """
        parametro = self.get(viagem_id, km)
        if parametro is None:
            parametro = ParametroKm(
                viagem_id=viagem_id, km=km, tri_serie=tri_serie, tre_serie=tre_serie
            )
            parametro.extras = dict(extras or {})
            self.session.add(parametro)
        else:
            if tri_serie is not None:
                parametro.tri_serie = tri_serie
            if tre_serie is not None:
                parametro.tre_serie = tre_serie
            if extras:
                parametro.extras = {**(parametro.extras or {}), **extras}
        self._flush()
        return parametro

    def list_by_viagem(self, viagem_id: uuid.UUID) -> list[ParametroKm]:
        stmt = (
            select(ParametroKm)
            .where(ParametroKm.viagem_id == viagem_id, ParametroKm.deleted_at.is_(None))
            .order_by(ParametroKm.km)
        )
        return list(self.session.scalars(stmt))

    def count_by_viagem(self, viagem_id: uuid.UUID) -> int:
        stmt = select(func.count(ParametroKm.id)).where(
            ParametroKm.viagem_id == viagem_id, ParametroKm.deleted_at.is_(None)
        )
        return int(self.session.scalar(stmt) or 0)
