from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import func, select

from src.db.models import Viagem
from src.db.repositories.base import BaseRepository


class ViagemRepository(BaseRepository):
    def get(self, viagem_id: uuid.UUID) -> Viagem | None:
        return self.session.get(Viagem, viagem_id)

    def get_by_nome(self, projeto_id: uuid.UUID, nome: str) -> Viagem | None:
        stmt = select(Viagem).where(
            Viagem.projeto_id == projeto_id,
            func.lower(Viagem.nome) == nome.strip().lower(),
            Viagem.deleted_at.is_(None),
        )
        return self.session.scalars(stmt).first()

    def get_by_pasta(self, pasta: str) -> Viagem | None:
        """Localiza a viagem pela pasta em `dados/` (identidade usada pela API).

        Como a API recebe só o nome da pasta, a busca não depende do projeto;
        havendo mais de uma, retorna a atualizada mais recentemente.
        """
        if not pasta:
            return None
        stmt = (
            select(Viagem)
            .where(Viagem.pasta == pasta, Viagem.deleted_at.is_(None))
            .order_by(Viagem.updated_at.desc())
        )
        return self.session.scalars(stmt).first()

    def create(
        self,
        *,
        projeto_id: uuid.UUID,
        nome: str,
        pasta: str | None = None,
        km_inicial: Decimal | float | None = None,
        km_final: Decimal | float | None = None,
        tipo_pista: str = "simples",
        sentido: str = "crescente",
        faixa: int | None = None,
    ) -> Viagem:
        viagem = Viagem(
            projeto_id=projeto_id,
            nome=nome.strip(),
            pasta=pasta,
            km_inicial=km_inicial,
            km_final=km_final,
            tipo_pista=tipo_pista,
            sentido=sentido,
            faixa=faixa,
        )
        self.session.add(viagem)
        self._flush()
        return viagem

    def upsert(
        self,
        *,
        projeto_id: uuid.UUID,
        nome: str,
        pasta: str | None = None,
        km_inicial: Decimal | float | None = None,
        km_final: Decimal | float | None = None,
        tipo_pista: str | None = None,
        sentido: str | None = None,
        faixa: int | None = None,
    ) -> Viagem:
        """Idempotente por `(projeto_id, nome)`; usado pela ETL e pela concatenação."""
        viagem = self.get_by_nome(projeto_id, nome)
        if viagem is None:
            return self.create(
                projeto_id=projeto_id,
                nome=nome,
                pasta=pasta,
                km_inicial=km_inicial,
                km_final=km_final,
                tipo_pista=tipo_pista or "simples",
                sentido=sentido or "crescente",
                faixa=faixa,
            )
        if pasta is not None:
            viagem.pasta = pasta
        if km_inicial is not None:
            viagem.km_inicial = km_inicial
        if km_final is not None:
            viagem.km_final = km_final
        if tipo_pista is not None:
            viagem.tipo_pista = tipo_pista
        if sentido is not None:
            viagem.sentido = sentido
        if faixa is not None:
            viagem.faixa = faixa
        self._flush()
        return viagem

    def list_by_projeto(self, projeto_id: uuid.UUID) -> list[Viagem]:
        stmt = (
            select(Viagem)
            .where(Viagem.projeto_id == projeto_id, Viagem.deleted_at.is_(None))
            .order_by(Viagem.nome)
        )
        return list(self.session.scalars(stmt))
