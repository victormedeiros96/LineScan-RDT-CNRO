"""Modelos SQLAlchemy do schema relacional (RDT1-8 / `migrations/0001_schema_inicial.sql`).

Espelham o DDL inicial: soft delete (`deleted_at`), índices únicos parciais, checks de
domínio e `JSONB` para payloads variáveis. As regras completas estão na nota Paca
"Schema e ERD do Banco de Dados".
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    REAL,
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(
        primary_key=True, default=uuid.uuid4, server_default=text("gen_random_uuid()")
    )


class Projeto(Base, TimestampMixin):
    __tablename__ = "projeto"

    id: Mapped[uuid.UUID] = _uuid_pk()
    nome: Mapped[str] = mapped_column(Text, nullable=False)
    rodovia: Mapped[str | None] = mapped_column(Text)
    trecho: Mapped[str | None] = mapped_column(Text)
    modelos_selecionados: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )

    __table_args__ = (
        Index(
            "ux_projeto_nome_ativo",
            text("lower(nome)"),
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )


class Viagem(Base, TimestampMixin):
    __tablename__ = "viagem"

    id: Mapped[uuid.UUID] = _uuid_pk()
    projeto_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projeto.id", ondelete="RESTRICT"), nullable=False
    )
    nome: Mapped[str] = mapped_column(Text, nullable=False)
    pasta: Mapped[str | None] = mapped_column(Text)
    km_inicial: Mapped[Decimal | None] = mapped_column(Numeric(10, 3))
    km_final: Mapped[Decimal | None] = mapped_column(Numeric(10, 3))
    tipo_pista: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'simples'")
    )
    sentido: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'crescente'")
    )
    faixa: Mapped[int | None] = mapped_column(SmallInteger)

    __table_args__ = (
        CheckConstraint("tipo_pista IN ('simples', 'dupla')", name="ck_viagem_tipo_pista"),
        CheckConstraint("sentido IN ('crescente', 'decrescente')", name="ck_viagem_sentido"),
        CheckConstraint("faixa IN (1, 2)", name="ck_viagem_faixa_valor"),
        CheckConstraint(
            "tipo_pista = 'dupla' OR faixa IS NULL", name="ck_viagem_faixa_tipo"
        ),
        CheckConstraint(
            "km_inicial IS NULL OR km_final IS NULL OR km_final >= km_inicial",
            name="ck_viagem_km",
        ),
        Index(
            "ux_viagem_projeto_nome_ativo",
            "projeto_id",
            text("lower(nome)"),
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "ux_viagem_projeto_pasta",
            "projeto_id",
            "pasta",
            unique=True,
            postgresql_where=text("deleted_at IS NULL AND pasta IS NOT NULL"),
        ),
        Index("ix_viagem_projeto", "projeto_id"),
    )


class Bloco(Base, TimestampMixin):
    __tablename__ = "bloco"

    id: Mapped[uuid.UUID] = _uuid_pk()
    viagem_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("viagem.id", ondelete="CASCADE"), nullable=False
    )
    indice: Mapped[int] = mapped_column(Integer, nullable=False)
    lado: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'left'"))
    km_inicio: Mapped[Decimal | None] = mapped_column(Numeric(10, 3))
    km_fim: Mapped[Decimal | None] = mapped_column(Numeric(10, 3))

    __table_args__ = (
        CheckConstraint("indice >= 0", name="ck_bloco_indice"),
        CheckConstraint("lado IN ('left', 'right')", name="ck_bloco_lado"),
        CheckConstraint(
            "km_inicio IS NULL OR km_fim IS NULL OR km_fim >= km_inicio",
            name="ck_bloco_km",
        ),
        UniqueConstraint("id", "viagem_id", name="uq_bloco_id_viagem"),
        Index(
            "ux_bloco_viagem_indice",
            "viagem_id",
            "indice",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index("ix_bloco_viagem_km", "viagem_id", "km_inicio", "km_fim"),
    )


class Faixa(Base, TimestampMixin):
    __tablename__ = "faixa"

    id: Mapped[uuid.UUID] = _uuid_pk()
    viagem_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    bloco_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    faixa_index: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    arquivo: Mapped[str] = mapped_column(Text, nullable=False)
    km_inicio: Mapped[Decimal | None] = mapped_column(Numeric(10, 3))
    km_fim: Mapped[Decimal | None] = mapped_column(Numeric(10, 3))
    altura_px: Mapped[int | None] = mapped_column(Integer)
    imagens_raw: Mapped[list[Any] | None] = mapped_column(JSONB(none_as_null=True))
    offset_y_px: Mapped[int | None] = mapped_column(Integer)
    percentual_ultima_img: Mapped[Decimal | None] = mapped_column(Numeric(6, 3))

    __table_args__ = (
        ForeignKeyConstraint(
            ["bloco_id", "viagem_id"],
            ["bloco.id", "bloco.viagem_id"],
            ondelete="CASCADE",
            name="fk_faixa_bloco",
        ),
        CheckConstraint("faixa_index >= 0", name="ck_faixa_index"),
        CheckConstraint("altura_px IS NULL OR altura_px > 0", name="ck_faixa_altura"),
        CheckConstraint(
            "percentual_ultima_img IS NULL OR percentual_ultima_img BETWEEN 0 AND 100",
            name="ck_faixa_percentual",
        ),
        CheckConstraint(
            "km_inicio IS NULL OR km_fim IS NULL OR km_fim >= km_inicio",
            name="ck_faixa_km",
        ),
        Index(
            "ux_faixa_bloco_index",
            "bloco_id",
            "faixa_index",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "ux_faixa_viagem_arquivo",
            "viagem_id",
            "arquivo",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index("ix_faixa_viagem_km", "viagem_id", "km_inicio", "km_fim"),
    )


class Deteccao(Base, TimestampMixin):
    __tablename__ = "deteccao"

    id: Mapped[uuid.UUID] = _uuid_pk()
    faixa_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("faixa.id", ondelete="CASCADE"), nullable=False
    )
    classe: Mapped[str] = mapped_column(Text, nullable=False)
    area_pixels: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    area_m2: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    confidence: Mapped[float | None] = mapped_column(REAL)
    confidence_max: Mapped[float | None] = mapped_column(REAL)
    linha: Mapped[int | None] = mapped_column(SmallInteger)
    coluna: Mapped[int | None] = mapped_column(SmallInteger)
    global_box: Mapped[dict[str, Any] | list[Any] | None] = mapped_column(JSONB(none_as_null=True))
    global_polygon: Mapped[list[Any] | None] = mapped_column(JSONB(none_as_null=True))
    origem: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'inferencia'")
    )

    __table_args__ = (
        CheckConstraint("area_pixels >= 0", name="ck_deteccao_area"),
        CheckConstraint("area_m2 IS NULL OR area_m2 >= 0", name="ck_deteccao_area_m2"),
        CheckConstraint(
            "confidence IS NULL OR confidence BETWEEN 0 AND 1", name="ck_deteccao_confidence"
        ),
        CheckConstraint(
            "confidence_max IS NULL OR confidence_max BETWEEN 0 AND 1",
            name="ck_deteccao_confidence_max",
        ),
        CheckConstraint("linha IS NULL OR linha BETWEEN 0 AND 19", name="ck_deteccao_linha"),
        CheckConstraint("coluna IS NULL OR coluna BETWEEN 0 AND 2", name="ck_deteccao_coluna"),
        CheckConstraint(
            "origem IN ('inferencia', 'manual')", name="ck_deteccao_origem"
        ),
        Index(
            "ix_deteccao_faixa",
            "faixa_id",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "ix_deteccao_classe",
            "classe",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "ix_deteccao_polygon_gin",
            "global_polygon",
            postgresql_using="gin",
            postgresql_ops={"global_polygon": "jsonb_path_ops"},
        ),
    )


class ParametroKm(Base, TimestampMixin):
    __tablename__ = "parametro_km"

    id: Mapped[uuid.UUID] = _uuid_pk()
    viagem_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("viagem.id", ondelete="CASCADE"), nullable=False
    )
    km: Mapped[int] = mapped_column(Integer, nullable=False)
    tri_serie: Mapped[list[float] | None] = mapped_column(JSONB(none_as_null=True))
    tre_serie: Mapped[list[float] | None] = mapped_column(JSONB(none_as_null=True))
    extras: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )

    __table_args__ = (
        CheckConstraint("km >= 0", name="ck_parametro_km"),
        CheckConstraint(
            "tri_serie IS NULL OR jsonb_typeof(tri_serie) = 'array'", name="ck_parametro_tri"
        ),
        CheckConstraint(
            "tre_serie IS NULL OR jsonb_typeof(tre_serie) = 'array'", name="ck_parametro_tre"
        ),
        Index(
            "ux_parametro_km_viagem_km",
            "viagem_id",
            "km",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )


class Job(Base, TimestampMixin):
    __tablename__ = "job"

    id: Mapped[uuid.UUID] = _uuid_pk()
    rq_job_id: Mapped[str | None] = mapped_column(Text, unique=True)
    viagem_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("viagem.id", ondelete="SET NULL")
    )
    viagem_nome: Mapped[str] = mapped_column(Text, nullable=False)
    tipo: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'queued'")
    )
    meta: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    progresso: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    resultado: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    erro: Mapped[str | None] = mapped_column(Text)
    iniciado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finalizado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(
            "tipo IN ('concatenacao', 'inferencia', 'upload_zip')", name="ck_job_tipo"
        ),
        CheckConstraint(
            "status IN ('queued', 'started', 'finished', 'failed', 'canceled')",
            name="ck_job_status",
        ),
        Index("ix_job_viagem", "viagem_id"),
        Index("ix_job_status", "status"),
        Index("ix_job_created_at", text("created_at DESC")),
    )


class ResultadoAnalise(Base, TimestampMixin):
    __tablename__ = "resultado_analise"

    id: Mapped[uuid.UUID] = _uuid_pk()
    viagem_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("viagem.id", ondelete="CASCADE"), nullable=False
    )
    km: Mapped[int] = mapped_column(Integer, nullable=False)
    tipo_analise: Mapped[str] = mapped_column(Text, nullable=False)
    norma: Mapped[str | None] = mapped_column(Text)
    valor: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    conceito: Mapped[str | None] = mapped_column(Text)
    n_estacoes: Mapped[int | None] = mapped_column(Integer)
    detalhe: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    calculado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint("km >= 0", name="ck_resultado_km"),
        CheckConstraint("tipo_analise IN ('igg', 'icp')", name="ck_resultado_tipo"),
        CheckConstraint("n_estacoes IS NULL OR n_estacoes > 0", name="ck_resultado_n_estacoes"),
        Index(
            "ux_resultado_viagem_km_tipo",
            "viagem_id",
            "km",
            "tipo_analise",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "ix_resultado_viagem_tipo_km",
            "viagem_id",
            "tipo_analise",
            "km",
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )


class ArquivoUpload(Base, TimestampMixin):
    __tablename__ = "arquivo_upload"

    id: Mapped[uuid.UUID] = _uuid_pk()
    viagem_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("viagem.id", ondelete="SET NULL")
    )
    viagem_nome: Mapped[str] = mapped_column(Text, nullable=False)
    nome: Mapped[str] = mapped_column(Text, nullable=False)
    caminho_relativo: Mapped[str] = mapped_column(Text, nullable=False)
    tamanho_bytes: Mapped[int | None] = mapped_column(BigInteger)
    content_type: Mapped[str | None] = mapped_column(Text)
    md5: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        CheckConstraint(
            "tamanho_bytes IS NULL OR tamanho_bytes >= 0", name="ck_arquivo_tamanho"
        ),
        Index(
            "ux_arquivo_upload_viagem_nome",
            "viagem_nome",
            "nome",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index("ix_arquivo_upload_viagem", "viagem_id"),
    )


__all__ = [
    "ArquivoUpload",
    "Bloco",
    "Deteccao",
    "Faixa",
    "Job",
    "ParametroKm",
    "Projeto",
    "ResultadoAnalise",
    "Viagem",
]
