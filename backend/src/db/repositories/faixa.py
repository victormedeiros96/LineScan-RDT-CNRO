from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import select

from src.db.models import Faixa
from src.db.repositories.base import BaseRepository


class FaixaRepository(BaseRepository):
    def get(self, faixa_id: uuid.UUID) -> Faixa | None:
        return self.session.get(Faixa, faixa_id)

    def get_by_arquivo(self, viagem_id: uuid.UUID, arquivo: str) -> Faixa | None:
        stmt = select(Faixa).where(
            Faixa.viagem_id == viagem_id,
            Faixa.arquivo == arquivo,
            Faixa.deleted_at.is_(None),
        )
        return self.session.scalars(stmt).first()

    def get_by_bloco_index(self, bloco_id: uuid.UUID, faixa_index: int) -> Faixa | None:
        stmt = select(Faixa).where(
            Faixa.bloco_id == bloco_id,
            Faixa.faixa_index == faixa_index,
            Faixa.deleted_at.is_(None),
        )
        return self.session.scalars(stmt).first()

    def upsert(
        self,
        *,
        viagem_id: uuid.UUID,
        bloco_id: uuid.UUID,
        faixa_index: int,
        arquivo: str,
        km_inicio: Decimal | float | None = None,
        km_fim: Decimal | float | None = None,
        altura_px: int | None = None,
        imagens_raw: list[Any] | None = None,
        offset_y_px: int | None = None,
        percentual_ultima_img: Decimal | float | None = None,
    ) -> Faixa:
        faixa = self.get_by_arquivo(viagem_id, arquivo)
        if faixa is None:
            faixa = Faixa(
                viagem_id=viagem_id,
                bloco_id=bloco_id,
                faixa_index=faixa_index,
                arquivo=arquivo,
            )
            self.session.add(faixa)

        faixa.bloco_id = bloco_id
        faixa.faixa_index = faixa_index
        if km_inicio is not None:
            faixa.km_inicio = km_inicio
        if km_fim is not None:
            faixa.km_fim = km_fim
        if altura_px is not None:
            faixa.altura_px = altura_px
        if imagens_raw is not None:
            faixa.imagens_raw = imagens_raw
        if offset_y_px is not None:
            faixa.offset_y_px = offset_y_px
        if percentual_ultima_img is not None:
            faixa.percentual_ultima_img = percentual_ultima_img

        self._flush()
        return faixa

    def list_by_viagem(self, viagem_id: uuid.UUID) -> list[Faixa]:
        stmt = (
            select(Faixa)
            .where(Faixa.viagem_id == viagem_id, Faixa.deleted_at.is_(None))
            .order_by(Faixa.bloco_id, Faixa.faixa_index)
        )
        return list(self.session.scalars(stmt))
