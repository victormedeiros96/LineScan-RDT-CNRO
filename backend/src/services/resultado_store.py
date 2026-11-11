"""Resultados de IGG/ICP por trecho/KM no banco (RDT1-13).

Os relatórios calculam os índices a partir da análise e passam a persistir cada
resultado em `resultado_analise` por ``(viagem, km, tipo_analise)``. O campo
flexível `detalhe` (`JSONB`) guarda o restante do resultado (grupos, parcelas,
etc.), de modo que a leitura consegue reconstruir o relatório.

Regras:

* Reprocessar o mesmo KM atualiza o registro (idempotente).
* Quando o conjunto completo é recalculado, os KMs que saíram do resultado são
  reconciliados com soft delete; ao calcular um KM isolado, nada é removido.
* Sem banco configurado/indisponível a persistência é *best-effort* e o
  relatório continua sendo gerado a partir do JSON/análise.
"""

from __future__ import annotations

import logging
import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from src.db.models import ResultadoAnalise, Viagem
from src.db.repositories import ResultadoAnaliseRepository, ViagemRepository
from src.db.session import session_scope

logger = logging.getLogger(__name__)

NORMA_IGG = "DNIT 006/2003-PRO"
NORMA_ICP = "DNIT 062/2004-PRO"
_TIPOS = ("igg", "icp")


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _localizar_viagem(session: Session, viagem_nome: str) -> Viagem | None:
    repo = ViagemRepository(session)
    return repo.get_by_pasta(viagem_nome.strip()) or repo.get_by_pasta(viagem_nome)


def _norma_padrao(tipo_analise: str) -> str | None:
    if tipo_analise == "igg":
        return NORMA_IGG
    if tipo_analise == "icp":
        return NORMA_ICP
    return None


def _valor(resultado: dict[str, Any]) -> float | None:
    for chave in ("valor", "igg", "icp", "indice"):
        valor = _to_float(resultado.get(chave))
        if valor is not None:
            return valor
    return None


def _detalhe(resultado: dict[str, Any]) -> dict[str, Any]:
    return {
        chave: valor
        for chave, valor in resultado.items()
        if chave
        not in (
            "km",
            "amostra",
            "valor",
            "igg",
            "icp",
            "indice",
            "conceito",
            "norma",
            "n_estacoes",
        )
    }


def _amostra(resultado: dict[str, Any]) -> str | None:
    amostra = resultado.get("amostra")
    if amostra is None:
        return None
    texto = str(amostra).strip()
    return texto or None


def persistir_resultados_em_sessao(
    session: Session,
    *,
    viagem_nome: str,
    tipo_analise: str,
    resultados: list[dict[str, Any]],
    norma: str | None = None,
    reconciliar: bool = True,
) -> int:
    """Grava os resultados na transação corrente e devolve quantos entraram."""
    if tipo_analise not in _TIPOS:
        raise ValueError(f"tipo_analise inválido: {tipo_analise!r}")

    viagem = _localizar_viagem(session, viagem_nome)
    if viagem is None:
        raise LookupError(f"Viagem '{viagem_nome}' não está no banco")

    repo = ResultadoAnaliseRepository(session)
    norma = norma or _norma_padrao(tipo_analise)
    ativos: set[uuid.UUID] = set()
    gravados = 0
    for resultado in resultados:
        amostra = _amostra(resultado)
        km = resultado.get("km")
        try:
            km = int(km)
        except (TypeError, ValueError):
            # Amostras de ICP sem KM informado (ex.: PPV01) usam km 0 como
            # sentinela; ``detalhe.km_informado`` guarda a informação original.
            if tipo_analise == "icp" and amostra is not None:
                km = 0
            else:
                continue
        valor = _valor(resultado)
        if valor is None:
            continue
        registro = repo.upsert(
            viagem_id=viagem.id,
            km=km,
            tipo_analise=tipo_analise,
            amostra=amostra,
            valor=Decimal(str(round(valor, 2))),
            norma=resultado.get("norma") or norma,
            conceito=resultado.get("conceito"),
            n_estacoes=resultado.get("n_estacoes"),
            detalhe=_detalhe(resultado),
        )
        ativos.add(registro.id)
        gravados += 1

    if reconciliar:
        repo.soft_delete_except(viagem.id, tipo_analise, ativos)
    return gravados


def persistir_resultados(
    *,
    viagem_nome: str,
    tipo_analise: str,
    resultados: list[dict[str, Any]],
    norma: str | None = None,
    reconciliar: bool = True,
) -> int | None:
    """Persiste os resultados em uma transação curta (best-effort)."""
    try:
        with session_scope() as session:
            return persistir_resultados_em_sessao(
                session,
                viagem_nome=viagem_nome,
                tipo_analise=tipo_analise,
                resultados=resultados,
                norma=norma,
                reconciliar=reconciliar,
            )
    except (LookupError, RuntimeError, SQLAlchemyError) as exc:
        logger.warning(
            "Falha ao persistir resultados %s de %s; mantendo cálculo em memória: %s",
            tipo_analise,
            viagem_nome,
            exc,
        )
        return None


def _item(resultado: ResultadoAnalise) -> dict[str, Any]:
    item: dict[str, Any] = {
        "km": resultado.km,
        "amostra": resultado.amostra,
        "tipo_analise": resultado.tipo_analise,
        "valor": float(resultado.valor),
        "norma": resultado.norma,
        "conceito": resultado.conceito,
        "n_estacoes": resultado.n_estacoes,
        "calculado_em": resultado.calculado_em.isoformat()
        if resultado.calculado_em
        else None,
    }
    item.update(resultado.detalhe or {})
    if resultado.tipo_analise == "icp":
        item.setdefault("icp", float(resultado.valor))
    return item


def _ler_resultados(
    session: Session, viagem_nome: str, tipo_analise: str | None
) -> list[dict[str, Any]] | None:
    viagem = _localizar_viagem(session, viagem_nome)
    if viagem is None:
        return None
    registros = ResultadoAnaliseRepository(session).list_by_viagem(
        viagem.id, tipo_analise
    )
    return [_item(registro) for registro in registros]


def carregar_resultados(
    viagem_nome: str, tipo_analise: str | None = None, session: Session | None = None
) -> list[dict[str, Any]] | None:
    """Resultados persistidos, ou ``None`` quando o banco não está disponível."""
    def _acao(sessao: Session) -> list[dict[str, Any]] | None:
        return _ler_resultados(sessao, viagem_nome, tipo_analise)

    if session is not None:
        return _acao(session)
    try:
        with session_scope() as own_session:
            return _acao(own_session)
    except (RuntimeError, SQLAlchemyError) as exc:
        logger.warning("Banco indisponível para ler resultados: %s", exc)
        return None
