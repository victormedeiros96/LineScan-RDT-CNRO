from __future__ import annotations

import uuid

from sqlalchemy import select

from src.db.models import ArquivoUpload
from src.db.repositories.base import BaseRepository


class ArquivoUploadRepository(BaseRepository):
    def get_by_nome(self, viagem_nome: str, nome: str) -> ArquivoUpload | None:
        stmt = select(ArquivoUpload).where(
            ArquivoUpload.viagem_nome == viagem_nome,
            ArquivoUpload.nome == nome,
            ArquivoUpload.deleted_at.is_(None),
        )
        return self.session.scalars(stmt).first()

    def upsert(
        self,
        *,
        viagem_nome: str,
        nome: str,
        caminho_relativo: str,
        viagem_id: uuid.UUID | None = None,
        tamanho_bytes: int | None = None,
        content_type: str | None = None,
        md5: str | None = None,
    ) -> ArquivoUpload:
        arquivo = self.get_by_nome(viagem_nome, nome)
        if arquivo is None:
            arquivo = ArquivoUpload(viagem_nome=viagem_nome, nome=nome)
            self.session.add(arquivo)
        arquivo.caminho_relativo = caminho_relativo
        if viagem_id is not None:
            arquivo.viagem_id = viagem_id
        if tamanho_bytes is not None:
            arquivo.tamanho_bytes = tamanho_bytes
        if content_type is not None:
            arquivo.content_type = content_type
        if md5 is not None:
            arquivo.md5 = md5
        self._flush()
        return arquivo

    def list_by_viagem_nome(self, viagem_nome: str) -> list[ArquivoUpload]:
        stmt = (
            select(ArquivoUpload)
            .where(
                ArquivoUpload.viagem_nome == viagem_nome,
                ArquivoUpload.deleted_at.is_(None),
            )
            .order_by(ArquivoUpload.nome)
        )
        return list(self.session.scalars(stmt))
