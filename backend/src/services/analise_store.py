"""Persistência e leitura de análise/detecções/parâmetros TRI-TRE (RDT1-12).

Segundo slice vertical da migração de persistência. A inferência grava as detecções
de cada faixa em uma transação curta e independente (sem escrita parcial de JSON) e
o editor carrega/salva análise e parâmetros diretamente no banco.

Regras:

* Cada faixa é substituída por inteiro dentro da sua própria transação
  (``replace_for_faixa``), então uma falha no meio do job não deixa detecções
  parciais de uma faixa.
* Detecções guardam classe, confiança, área, caixa e polígono em ``JSONB``; os
  campos de compatibilidade do JSON (``area``/``score``/``direction``) são derivados
  na leitura.
* Parâmetros TRI/TRE são gravados por ``(viagem, km)``; o restante do objeto do
  editor fica em ``extras`` para não perder parâmetros desconhecidos.
* Se a viagem ainda não estiver no banco, as leituras devolvem ``None`` para o
  chamador cair no JSON legado. Análises antigas podem ser importadas por
  :func:`migrar_analise_legada`.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable, Iterable
from decimal import Decimal
from typing import Any, TypeVar

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from src.db.models import Deteccao, Faixa, Viagem
from src.db.repositories import (
    DeteccaoRepository,
    FaixaRepository,
    ParametroKmRepository,
    ViagemRepository,
)
from src.db.session import session_scope

logger = logging.getLogger(__name__)

T = TypeVar("T")

LABEL_TRI = "TRI (mm)"
LABEL_TRE = "TRE (mm)"


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return float(value)
    return float(value)


def _clamp(value: Any, minimo: float, maximo: float) -> float | None:
    if value is None:
        return None
    try:
        numero = float(value)
    except (TypeError, ValueError):
        return None
    return max(minimo, min(maximo, numero))


def _inteiro_ou_none(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _localizar_viagem(session: Session, viagem_nome: str) -> Viagem | None:
    repo = ViagemRepository(session)
    return repo.get_by_pasta(viagem_nome.strip()) or repo.get_by_pasta(viagem_nome)


def _origem(deteccao: dict[str, Any]) -> str:
    origem = deteccao.get("origem")
    if origem in ("inferencia", "manual"):
        return origem
    return "manual" if deteccao.get("direction") == "manual" else "inferencia"


def _linha_ou_none(value: Any) -> int | None:
    linha = _inteiro_ou_none(value)
    if linha is None:
        return None
    return max(0, min(19, linha))


def _coluna_ou_none(value: Any) -> int | None:
    coluna = _inteiro_ou_none(value)
    if coluna is None:
        return None
    return max(0, min(2, coluna))


def _row(deteccao: dict[str, Any]) -> dict[str, Any]:
    """Converte a detecção do JSON/documento para as colunas da tabela."""
    area = _inteiro_ou_none(deteccao.get("area_pixels"))
    if area is None:
        area = _inteiro_ou_none(deteccao.get("area")) or 0
    area_m2 = deteccao.get("area_m2")
    if area_m2 is not None:
        try:
            area_m2 = max(0.0, float(area_m2))
        except (TypeError, ValueError):
            area_m2 = None

    return {
        "classe": str(deteccao.get("classe") or ""),
        "area_pixels": max(0, area),
        "area_m2": area_m2,
        "confidence": _clamp(deteccao.get("confidence"), 0.0, 1.0),
        "confidence_max": _clamp(deteccao.get("confidence_max"), 0.0, 1.0),
        "linha": _linha_ou_none(deteccao.get("linha")),
        "coluna": _coluna_ou_none(deteccao.get("coluna")),
        "global_box": deteccao.get("global_box"),
        "global_polygon": deteccao.get("global_polygon"),
        "origem": _origem(deteccao),
    }


def _det_dict(deteccao: Deteccao) -> dict[str, Any]:
    """Repõe o formato consumido pelo editor a partir das colunas."""
    confidence = float(deteccao.confidence or 0.0)
    confidence_max = deteccao.confidence_max
    return {
        "classe": deteccao.classe,
        "area": deteccao.area_pixels,
        "area_pixels": deteccao.area_pixels,
        "area_m2": _to_float(deteccao.area_m2) or 0.0,
        "confidence": confidence,
        "score": confidence,
        "confidence_max": float(confidence_max) if confidence_max is not None else confidence,
        "linha": deteccao.linha or 0,
        "coluna": deteccao.coluna or 0,
        "global_box": deteccao.global_box or [],
        "global_polygon": deteccao.global_polygon or [],
        "direction": "manual" if deteccao.origem == "manual" else "",
    }


def _faixa_meta(faixa: Faixa, bloco_indice: int) -> dict[str, Any]:
    return {
        "arquivo": faixa.arquivo,
        "bloco_indice": bloco_indice,
        "km_inicio": _to_float(faixa.km_inicio),
        "km_fim": _to_float(faixa.km_fim),
        "altura_px": faixa.altura_px,
    }


def _persistir_deteccoes(
    session: Session, viagem: Viagem, arquivo: str, deteccoes: Iterable[dict[str, Any]]
) -> bool:
    faixa = FaixaRepository(session).get_by_arquivo(viagem.id, arquivo)
    if faixa is None:
        return False
    DeteccaoRepository(session).replace_for_faixa(
        faixa.id, [_row(deteccao) for deteccao in deteccoes]
    )
    return True


def persistir_deteccoes_faixa_em_sessao(
    session: Session,
    *,
    viagem_nome: str,
    arquivo: str,
    deteccoes: Iterable[dict[str, Any]],
) -> bool:
    """Substitui as detecções de uma faixa na transação corrente.

    Erro de viagem/faixa inexistente é explícito: a inferência só grava se a
    concatenação (RDT1-11) já tiver criado a faixa no banco.
    """
    viagem = _localizar_viagem(session, viagem_nome)
    if viagem is None:
        raise LookupError(f"Viagem '{viagem_nome}' não está no banco")
    if not _persistir_deteccoes(session, viagem, arquivo, deteccoes):
        raise LookupError(f"Faixa '{arquivo}' não está no banco para a viagem '{viagem_nome}'")


def persistir_deteccoes_faixa(
    *,
    viagem_nome: str,
    arquivo: str,
    deteccoes: Iterable[dict[str, Any]],
) -> None:
    """Grava as detecções de uma faixa em uma transação curta (worker)."""
    with session_scope() as session:
        persistir_deteccoes_faixa_em_sessao(
            session, viagem_nome=viagem_nome, arquivo=arquivo, deteccoes=deteccoes
        )


def persistir_analise_em_sessao(
    session: Session,
    *,
    viagem_nome: str,
    deteccoes_por_imagem: dict[str, Any],
    parametros_por_km: dict[str, Any] | None = None,
) -> tuple[int, int]:
    """Grava a edição do editor (detecções e parâmetros) em uma transação."""
    viagem = _localizar_viagem(session, viagem_nome)
    if viagem is None:
        raise LookupError(f"Viagem '{viagem_nome}' não está no banco")

    imagens = 0
    for arquivo, deteccoes in deteccoes_por_imagem.items():
        if not isinstance(deteccoes, list):
            continue
        if _persistir_deteccoes(session, viagem, arquivo, deteccoes):
            imagens += 1

    parametros = 0
    if parametros_por_km is not None:
        parametros = _persistir_parametros(session, viagem, parametros_por_km)
    return imagens, parametros


def persistir_analise(
    *,
    viagem_nome: str,
    deteccoes_por_imagem: dict[str, Any],
    parametros_por_km: dict[str, Any] | None = None,
) -> tuple[int, int]:
    """Grava a edição do editor em uma transação curta (API)."""
    with session_scope() as session:
        return persistir_analise_em_sessao(
            session,
            viagem_nome=viagem_nome,
            deteccoes_por_imagem=deteccoes_por_imagem,
            parametros_por_km=parametros_por_km,
        )


def _serie(valor: Any) -> list[Any] | None:
    return list(valor) if isinstance(valor, list) else None


def _persistir_parametros(
    session: Session, viagem: Viagem, parametros_por_km: dict[str, Any]
) -> int:
    repo = ParametroKmRepository(session)
    ativos: set[uuid.UUID] = set()
    for km_key, valores in parametros_por_km.items():
        km = _inteiro_ou_none(km_key)
        if km is None or km < 0:
            continue
        valores = valores if isinstance(valores, dict) else {}
        extras = {
            chave: valor
            for chave, valor in valores.items()
            if chave not in (LABEL_TRI, LABEL_TRE)
        }
        parametro = repo.upsert(
            viagem_id=viagem.id,
            km=km,
            tri_serie=_serie(valores.get(LABEL_TRI)),
            tre_serie=_serie(valores.get(LABEL_TRE)),
            extras=extras or None,
        )
        ativos.add(parametro.id)
    repo.soft_delete_except(viagem.id, ativos)
    return len(ativos)


def persistir_parametros(
    *, viagem_nome: str, parametros_por_km: dict[str, Any]
) -> int:
    """Grava os parâmetros TRI/TRE por KM em uma transação."""
    with session_scope() as session:
        return persistir_parametros_em_sessao(
            session, viagem_nome=viagem_nome, parametros_por_km=parametros_por_km
        )


def persistir_parametros_em_sessao(
    session: Session, *, viagem_nome: str, parametros_por_km: dict[str, Any]
) -> int:
    """Grava os parâmetros TRI/TRE por KM na transação corrente."""
    viagem = _localizar_viagem(session, viagem_nome)
    if viagem is None:
        raise LookupError(f"Viagem '{viagem_nome}' não está no banco")
    return _persistir_parametros(session, viagem, parametros_por_km)


def _ler_analise(session: Session, viagem_nome: str) -> dict[str, Any] | None:
    viagem = _localizar_viagem(session, viagem_nome)
    if viagem is None:
        return None

    resultado: dict[str, Any] = {}
    for faixa, bloco in FaixaRepository(session).list_by_viagem_com_bloco(viagem.id):
        deteccoes = DeteccaoRepository(session).list_by_faixa(faixa.id)
        resultado[faixa.arquivo] = {
            "deteccoes": [_det_dict(deteccao) for deteccao in deteccoes],
            "faixa_meta": _faixa_meta(faixa, bloco.indice),
        }
    # Sem faixas no banco não há análise: o chamador usa o JSON legado.
    return resultado or None


def _ler_parametros(session: Session, viagem_nome: str) -> dict[str, Any] | None:
    viagem = _localizar_viagem(session, viagem_nome)
    if viagem is None:
        return None

    parametros: dict[str, Any] = {}
    for parametro in ParametroKmRepository(session).list_by_viagem(viagem.id):
        item: dict[str, Any] = {}
        if parametro.tri_serie is not None:
            item[LABEL_TRI] = parametro.tri_serie
        if parametro.tre_serie is not None:
            item[LABEL_TRE] = parametro.tre_serie
        item.update(parametro.extras or {})
        parametros[str(parametro.km)] = item
    return parametros


def _com_sessao(
    func: Callable[..., T], session: Session | None, *args: Any
) -> T | None:
    """Executa uma leitura no banco; sem banco/erro devolve ``None`` (fallback)."""
    if session is not None:
        return func(session, *args)
    try:
        with session_scope() as own_session:
            return func(own_session, *args)
    except (RuntimeError, SQLAlchemyError) as exc:
        logger.warning("Banco indisponível para leitura de análise: %s", exc)
        return None


def carregar_analise(
    viagem_nome: str, session: Session | None = None
) -> dict[str, Any] | None:
    """Análise (detecções por faixa) vinda do banco, ou ``None`` para o JSON legado."""
    return _com_sessao(_ler_analise, session, viagem_nome)


def carregar_parametros(
    viagem_nome: str, session: Session | None = None
) -> dict[str, Any] | None:
    """Parâmetros TRI/TRE por KM vindos do banco, ou ``None`` para o JSON legado."""
    return _com_sessao(_ler_parametros, session, viagem_nome)


def migrar_analise_legada(
    *,
    viagem_nome: str,
    analise: dict[str, Any],
    parametros_por_km: dict[str, Any] | None = None,
) -> dict[str, int]:
    """Importa uma análise em JSON (formato antigo ou novo) para o banco.

    Aceita tanto ``{arquivo: [deteccoes]}`` (formato antigo) quanto
    ``{arquivo: {deteccoes, faixa_meta}}``. Faixas que não existam no banco são
    ignoradas — a concatenação (RDT1-11) precisa ter rodado antes.
    """
    faixas = 0
    with session_scope() as session:
        viagem = _localizar_viagem(session, viagem_nome)
        if viagem is None:
            raise LookupError(f"Viagem '{viagem_nome}' não está no banco")
        for arquivo, dados in analise.items():
            if isinstance(dados, dict):
                deteccoes = dados.get("deteccoes") or []
            elif isinstance(dados, list):
                deteccoes = dados
            else:
                continue
            if _persistir_deteccoes(session, viagem, arquivo, deteccoes):
                faixas += 1
        parametros = (
            _persistir_parametros(session, viagem, parametros_por_km)
            if parametros_por_km is not None
            else 0
        )
    return {"faixas": faixas, "parametros": parametros}
