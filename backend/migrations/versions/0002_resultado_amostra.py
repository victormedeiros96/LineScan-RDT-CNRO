"""Resultado de ICP por amostra (RDT1-17).

O ICP é calculado por amostra de uma ficha de inspeção e várias amostras podem
compartilhar o mesmo KM (as faixas de uma praça de pedágio). A chave única de
`resultado_analise` passa a incluir `amostra`; resultados por KM (IGG) seguem
com `amostra IS NULL` sob o índice antigo.

Revision ID: 0002_resultado_amostra
Revises: 0001_schema_inicial
Create Date: 2026-11-11
"""

from __future__ import annotations

from alembic import op

revision = "0002_resultado_amostra"
down_revision = "0001_schema_inicial"
branch_labels = None
depends_on = None

_UPGRADE_SQL = """
ALTER TABLE resultado_analise ADD COLUMN amostra text;

DROP INDEX IF EXISTS ux_resultado_viagem_km_tipo;

CREATE UNIQUE INDEX ux_resultado_viagem_km_tipo
    ON resultado_analise (viagem_id, km, tipo_analise)
    WHERE deleted_at IS NULL AND amostra IS NULL;

CREATE UNIQUE INDEX ux_resultado_viagem_km_tipo_amostra
    ON resultado_analise (viagem_id, km, tipo_analise, amostra)
    WHERE deleted_at IS NULL AND amostra IS NOT NULL;
"""

_DOWNGRADE_SQL = """
DROP INDEX IF EXISTS ux_resultado_viagem_km_tipo_amostra;
DROP INDEX IF EXISTS ux_resultado_viagem_km_tipo;

CREATE UNIQUE INDEX ux_resultado_viagem_km_tipo
    ON resultado_analise (viagem_id, km, tipo_analise)
    WHERE deleted_at IS NULL;

ALTER TABLE resultado_analise DROP COLUMN amostra;
"""


def upgrade() -> None:
    op.execute(_UPGRADE_SQL)


def downgrade() -> None:
    op.execute(_DOWNGRADE_SQL)
