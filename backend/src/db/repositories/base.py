from __future__ import annotations

from sqlalchemy.orm import Session


class BaseRepository:
    """Repositório com sessão injetada.

    Nunca chama `commit`: a transação é controlada por `src.db.session.session_scope`
    (ou pela dependência `get_session` do FastAPI). Os repositórios só fazem `flush`
    para obter ids/constraints dentro da transação corrente.
    """

    def __init__(self, session: Session) -> None:
        self.session = session

    def _flush(self) -> None:
        self.session.flush()
