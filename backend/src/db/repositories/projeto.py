from __future__ import annotations

import uuid

from sqlalchemy import func, select

from src.db.models import Projeto
from src.db.repositories.base import BaseRepository


class ProjetoRepository(BaseRepository):
    def get(self, projeto_id: uuid.UUID) -> Projeto | None:
        return self.session.get(Projeto, projeto_id)

    def get_by_nome(self, nome: str) -> Projeto | None:
        stmt = select(Projeto).where(
            func.lower(Projeto.nome) == nome.strip().lower(),
            Projeto.deleted_at.is_(None),
        )
        return self.session.scalars(stmt).first()

    def get_or_create(
        self, nome: str, rodovia: str | None = None, trecho: str | None = None
    ) -> Projeto:
        projeto = self.get_by_nome(nome)
        if projeto is None:
            projeto = Projeto(nome=nome.strip(), rodovia=rodovia, trecho=trecho)
            self.session.add(projeto)
            self._flush()
        return projeto

    def list(self) -> list[Projeto]:
        stmt = select(Projeto).where(Projeto.deleted_at.is_(None)).order_by(Projeto.nome)
        return list(self.session.scalars(stmt))
