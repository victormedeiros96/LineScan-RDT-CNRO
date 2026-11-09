"""Persistência e leitura de viagem/config/blocos/faixas no banco (RDT1-11).

Este é o primeiro slice vertical da migração de persistência: a concatenação grava
config/blocos/faixas no PostgreSQL e a API passa a lê-los do banco. Os JSONs legados
(``viagem_config.json`` e ``lote_*_meta.json``) continuam sendo escritos e servem de
fallback enquanto a transição não termina (ver RDT1-6).

Regras:

* Toda escrita ocorre dentro de uma transação (`session_scope` ou sessão injetada):
  config, blocos e faixas entram juntos ou não entram.
* A chave de idempotência é ``(projeto, nome da viagem)``; blocos por índice e faixas
  pelo nome do arquivo. Reprocessar reconcilia (soft delete) o que saiu do conjunto.
* Um advisory lock transacional serializa escritas concorrentes da mesma viagem.
* Se o banco não estiver configurado/indisponível, as leituras devolvem ``None`` para
  o chamador cair no JSON legado — o worker e a API não quebram.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path
from typing import Any, TypeVar

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from src.db.models import Faixa, Viagem
from src.db.repositories import (
    BlocoRepository,
    FaixaRepository,
    ProjetoRepository,
    ViagemRepository,
)
from src.db.session import session_scope

logger = logging.getLogger(__name__)

T = TypeVar("T")

# Enquanto não há gestão de projetos no produto, toda viagem entra no projeto padrão.
PROJETO_PADRAO = "RDT01"


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return float(value)
    return float(value)


def coletar_metas(pasta: Path) -> list[dict[str, Any]]:
    """Lê os ``lote_*_meta.json`` de uma viagem na ordem dos blocos."""
    metas: list[dict[str, Any]] = []
    for meta_path in sorted(pasta.glob("lote_*_meta.json")):
        try:
            metas.append(json.loads(meta_path.read_text(encoding="utf-8")))
        except (OSError, ValueError) as exc:
            logger.warning("Ignorando metadados ilegíveis %s: %s", meta_path, exc)
    return metas


def persistir_viagem_em_sessao(
    session: Session,
    *,
    projeto_nome: str,
    viagem_nome: str,
    pasta: str | None,
    config: dict[str, Any],
    lotes: list[dict[str, Any]],
) -> uuid.UUID:
    chave = f"{projeto_nome.strip().lower()}:{viagem_nome.strip().lower()}"
    session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:chave))"), {"chave": chave}
    )
    # Serializa também a criação do projeto: jobs concorrentes de viagens
    # diferentes no mesmo projeto não devem colidir no índice único do nome.
    session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:chave))"),
        {"chave": f"projeto:{projeto_nome.strip().lower()}"},
    )

    projeto = ProjetoRepository(session).get_or_create(projeto_nome)
    viagem = ViagemRepository(session).upsert(
        projeto_id=projeto.id,
        nome=viagem_nome,
        pasta=pasta,
        km_inicial=config.get("km_inicial"),
        km_final=config.get("km_final"),
        tipo_pista=config.get("tipo_pista") or "simples",
        sentido=config.get("sentido") or "crescente",
        faixa=config.get("faixa"),
    )

    bloco_repo = BlocoRepository(session)
    faixa_repo = FaixaRepository(session)
    blocos_ativos: set[uuid.UUID] = set()
    faixas_ativas: set[uuid.UUID] = set()

    for meta in lotes:
        bloco_meta = meta.get("bloco") or {}
        indice = int(bloco_meta.get("indice", meta.get("indice", 0)))
        bloco = bloco_repo.upsert(
            viagem_id=viagem.id,
            indice=indice,
            lado=bloco_meta.get("lado") or "left",
            km_inicio=bloco_meta.get("km_inicio"),
            km_fim=bloco_meta.get("km_fim"),
        )
        blocos_ativos.add(bloco.id)

        for faixa_meta in meta.get("faixas") or []:
            arquivo = faixa_meta.get("arquivo")
            if not arquivo:
                continue
            faixa = faixa_repo.upsert(
                viagem_id=viagem.id,
                bloco_id=bloco.id,
                faixa_index=int(faixa_meta.get("faixa_index", 0)),
                arquivo=arquivo,
                km_inicio=faixa_meta.get("km_inicio"),
                km_fim=faixa_meta.get("km_fim"),
                altura_px=faixa_meta.get("altura_px"),
                imagens_raw=faixa_meta.get("imagens_raw"),
                offset_y_px=faixa_meta.get("offset_y_px"),
                percentual_ultima_img=faixa_meta.get("percentual_ultima_img"),
            )
            faixas_ativas.add(faixa.id)

    # Reconciliação: o que não veio nesta concatenação sai de cena (soft delete).
    faixa_repo.soft_delete_except(viagem.id, faixas_ativas)
    bloco_repo.soft_delete_except(viagem.id, blocos_ativos)

    return viagem.id


def persistir_viagem(
    *,
    viagem_nome: str,
    config: dict[str, Any],
    lotes: list[dict[str, Any]],
    pasta: str | None = None,
    projeto_nome: str = PROJETO_PADRAO,
) -> uuid.UUID:
    """Grava config/blocos/faixas em uma única transação. Use no worker/ETL."""
    with session_scope() as session:
        return persistir_viagem_em_sessao(
            session,
            projeto_nome=projeto_nome,
            viagem_nome=viagem_nome,
            pasta=pasta,
            config=config,
            lotes=lotes,
        )


def _com_sessao(
    func: Callable[..., T],
    session: Session | None,
    *args: Any,
) -> T | None:
    """Executa uma leitura no banco; sem banco/erro devolve ``None`` (fallback)."""
    if session is not None:
        return func(session, *args)
    try:
        with session_scope() as own_session:
            return func(own_session, *args)
    except (RuntimeError, SQLAlchemyError) as exc:
        logger.warning("Banco indisponível para leitura de viagem: %s", exc)
        return None


def _localizar_viagem(session: Session, viagem_nome: str) -> Viagem | None:
    repo = ViagemRepository(session)
    return repo.get_by_pasta(viagem_nome.strip()) or repo.get_by_pasta(viagem_nome)


def _ler_config(session: Session, viagem_nome: str) -> dict[str, Any] | None:
    viagem = _localizar_viagem(session, viagem_nome)
    if viagem is None:
        return None
    return {
        "nome": viagem.nome,
        "km_inicial": _to_float(viagem.km_inicial),
        "km_final": _to_float(viagem.km_final),
        "tipo_pista": viagem.tipo_pista,
        "sentido": viagem.sentido,
        "faixa": viagem.faixa,
    }


def ler_config_viagem(
    viagem_nome: str, session: Session | None = None
) -> dict[str, Any] | None:
    """Configuração da viagem vinda do banco, ou ``None`` para usar o JSON legado."""
    return _com_sessao(_ler_config, session, viagem_nome)


def _faixa_dict(faixa: Faixa, bloco_indice: int, bloco: dict[str, Any]) -> dict[str, Any]:
    return {
        "bloco_indice": bloco_indice,
        "faixa_index": faixa.faixa_index,
        "arquivo": faixa.arquivo,
        "caminho": None,
        "km_inicio": _to_float(faixa.km_inicio),
        "km_fim": _to_float(faixa.km_fim),
        "altura_px": faixa.altura_px,
        "imagens_raw": faixa.imagens_raw,
        "offset_y_px": faixa.offset_y_px,
        "percentual_ultima_img": _to_float(faixa.percentual_ultima_img),
        "_bloco": bloco,
    }


def _ler_faixas(session: Session, viagem_nome: str) -> dict[str, Any] | None:
    viagem = _localizar_viagem(session, viagem_nome)
    if viagem is None:
        return None

    faixas: list[dict[str, Any]] = []
    for faixa, bloco in FaixaRepository(session).list_by_viagem_com_bloco(viagem.id):
        bloco_dict = {
            "indice": bloco.indice,
            "lado": bloco.lado,
            "km_inicio": _to_float(bloco.km_inicio),
            "km_fim": _to_float(bloco.km_fim),
        }
        faixas.append(_faixa_dict(faixa, bloco.indice, bloco_dict))

    return {
        "viagem": viagem.pasta or viagem.nome,
        "total_faixas": len(faixas),
        "faixas": faixas,
    }


def listar_faixas_viagem(
    viagem_nome: str, session: Session | None = None
) -> dict[str, Any] | None:
    """Listagem de faixas da viagem vinda do banco, ou ``None`` para o JSON legado."""
    return _com_sessao(_ler_faixas, session, viagem_nome)


def _ler_meta_bloco(
    session: Session, viagem_nome: str, bloco_indice: int
) -> dict[str, Any] | None:
    viagem = _localizar_viagem(session, viagem_nome)
    if viagem is None:
        return None
    bloco = BlocoRepository(session).get_by_indice(viagem.id, bloco_indice)
    if bloco is None:
        return None

    bloco_dict = {
        "indice": bloco.indice,
        "lado": bloco.lado,
        "km_inicio": _to_float(bloco.km_inicio),
        "km_fim": _to_float(bloco.km_fim),
    }
    faixas = [
        _faixa_dict(faixa, bloco.indice, bloco_dict)
        for faixa in FaixaRepository(session).list_by_bloco(bloco.id)
    ]
    return {"bloco": bloco_dict, "faixas": faixas}


def obter_meta_bloco(
    viagem_nome: str, bloco_indice: int, session: Session | None = None
) -> dict[str, Any] | None:
    """Metadados de um bloco vindos do banco, ou ``None`` para o JSON legado."""
    return _com_sessao(_ler_meta_bloco, session, viagem_nome, bloco_indice)
