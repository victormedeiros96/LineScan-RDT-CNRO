"""Schema relacional inicial (RDT1-8).

Reaproveita o DDL autoritativo em `migrations/0001_schema_inicial.sql`
(PostgreSQL 14+). O downgrade remove todas as tabelas de domínio e a função
de trigger `set_updated_at`.

Revision ID: 0001_schema_inicial
Revises:
Create Date: 2026-11-09
"""

from __future__ import annotations

from pathlib import Path

from alembic import op

revision = "0001_schema_inicial"
down_revision = None
branch_labels = None
depends_on = None

_DDL_PATH = Path(__file__).resolve().parents[1] / "0001_schema_inicial.sql"

_DOWNGRADE_SQL = """
DROP TABLE IF EXISTS arquivo_upload, resultado_analise, job, parametro_km,
    deteccao, faixa, bloco, viagem, projeto CASCADE;
DROP FUNCTION IF EXISTS set_updated_at() CASCADE;
"""


def upgrade() -> None:
    op.execute(_DDL_PATH.read_text(encoding="utf-8"))


def downgrade() -> None:
    op.execute(_DOWNGRADE_SQL)
