from __future__ import annotations

import uuid
from collections.abc import Iterable
from decimal import Decimal

from sqlalchemy import func, select, update

from src.db.models import Bloco
from src.db.repositories.base import BaseRepository


class BlocoRepository(BaseRepository):
    def get(self, bloco_id: uuid.UUID) -> Bloco | None:
        return self.session.get(Bloco, bloco_id)

    def get_by_indice(self, viagem_id: uuid.UUID, indice: int) -> Bloco | None:
        stmt = select(Bloco).where(
            Bloco.viagem_id == viagem_id,
            Bloco.indice == indice,
            Bloco.deleted_at.is_(None),
        )
        return self.session.scalars(stmt).first()

    def upsert(
        self,
        *,
        viagem_id: uuid.UUID,
        indice: int,
        lado: str = "left",
        km_inicio: Decimal | float | None = None,
        km_fim: Decimal | float | None = None,
    ) -> Bloco:
        bloco = self.get_by_indice(viagem_id, indice)
        if bloco is None:
            bloco = Bloco(
                viagem_id=viagem_id, indice=indice, lado=lado, km_inicio=km_inicio, km_fim=km_fim
            )
            self.session.add(bloco)
        else:
            bloco.lado = lado
            if km_inicio is not None:
                bloco.km_inicio = km_inicio
            if km_fim is not None:
                bloco.km_fim = km_fim
        self._flush()
        return bloco

    def list_by_viagem(self, viagem_id: uuid.UUID) -> list[Bloco]:
        stmt = (
            select(Bloco)
            .where(Bloco.viagem_id == viagem_id, Bloco.deleted_at.is_(None))
            .order_by(Bloco.indice)
        )
        return list(self.session.scalars(stmt))

    def soft_delete_except(self, viagem_id: uuid.UUID, keep_ids: Iterable[uuid.UUID]) -> None:
        """Marca como removidos os blocos ativos da viagem fora de ``keep_ids``.

        Usado na reconciliação da concatenação: reprocessar uma viagem não deve
        manter blocos antigos visíveis na API.
        """
        stmt = update(Bloco).where(
            Bloco.viagem_id == viagem_id, Bloco.deleted_at.is_(None)
        )
        ids = list(keep_ids)
        if ids:
            stmt = stmt.where(Bloco.id.notin_(ids))
        self.session.execute(stmt.values(deleted_at=func.now()))
