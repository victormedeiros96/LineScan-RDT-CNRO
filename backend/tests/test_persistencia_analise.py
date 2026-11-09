"""Testes do slice de análise/detecções/parâmetros TRI-TRE (RDT1-12).

Cobrem gravação por faixa em transação curta, substituição idempotente, leitura no
formato do editor, parâmetros por KM (preservação de séries e soft delete) e a
importação de análises antigas em JSON.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.db.models import Deteccao, ParametroKm
from src.services import analise_store, viagem_store


def _config(nome: str) -> dict[str, Any]:
    return {
        "nome": nome,
        "km_inicial": 100.0,
        "km_final": 100.4,
        "tipo_pista": "simples",
        "sentido": "crescente",
        "faixa": None,
    }


def _lotes() -> list[dict[str, Any]]:
    return [
        {
            "bloco": {"indice": 0, "lado": "left", "km_inicio": 100.0, "km_fim": 100.02},
            "faixas": [
                {
                    "bloco_indice": 0,
                    "faixa_index": 0,
                    "arquivo": "lote_0000_faixa_0.png",
                    "km_inicio": 100.0,
                    "km_fim": 100.005,
                    "altura_px": 5120,
                }
            ],
        },
        {
            "bloco": {"indice": 1, "lado": "left", "km_inicio": 100.02, "km_fim": 100.04},
            "faixas": [
                {
                    "bloco_indice": 1,
                    "faixa_index": 0,
                    "arquivo": "lote_0001_faixa_0.png",
                    "km_inicio": 100.02,
                    "km_fim": 100.025,
                    "altura_px": 5120,
                }
            ],
        },
    ]


def _viagem(session: Session, nome: str) -> None:
    viagem_store.persistir_viagem_em_sessao(
        session,
        projeto_nome="Projeto análise",
        viagem_nome=nome,
        pasta=nome,
        config=_config(nome),
        lotes=_lotes(),
    )


def _deteccao(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "classe": "Trincas",
        "area": 120,
        "area_pixels": 120,
        "area_m2": 0.0012,
        "confidence": 0.9,
        "score": 0.9,
        "confidence_max": 0.95,
        "linha": 2,
        "coluna": 1,
        "global_box": [10, 20, 30, 40],
        "global_polygon": [[10, 20], [30, 20], [30, 40]],
        "direction": "",
    }
    base.update(over)
    return base


def test_grava_e_le_deteccoes_por_faixa(session: Session) -> None:
    nome = f"Viagem {uuid.uuid4().hex[:8]}"
    _viagem(session, nome)

    analise_store.persistir_deteccoes_faixa_em_sessao(
        session,
        viagem_nome=nome,
        arquivo="lote_0000_faixa_0.png",
        deteccoes=[_deteccao()],
    )

    analise = analise_store.carregar_analise(nome, session=session)
    assert analise is not None
    dados = analise["lote_0000_faixa_0.png"]
    assert dados["faixa_meta"]["bloco_indice"] == 0
    assert dados["faixa_meta"]["km_inicio"] == 100.0
    assert dados["faixa_meta"]["altura_px"] == 5120

    det = dados["deteccoes"][0]
    assert det["classe"] == "Trincas"
    assert det["area"] == 120
    assert det["area_pixels"] == 120
    assert det["confidence"] == 0.9
    assert det["score"] == 0.9
    assert det["confidence_max"] == 0.95
    assert det["linha"] == 2
    assert det["coluna"] == 1
    assert det["global_box"] == [10, 20, 30, 40]
    assert det["global_polygon"] == [[10, 20], [30, 20], [30, 40]]
    assert det["direction"] == ""

    # A segunda faixa ainda não foi processada, mas aparece com zero detecções.
    assert analise["lote_0001_faixa_0.png"]["deteccoes"] == []


def test_manual_mapeia_origem_e_direction(session: Session) -> None:
    nome = f"Viagem {uuid.uuid4().hex[:8]}"
    _viagem(session, nome)

    analise_store.persistir_deteccoes_faixa_em_sessao(
        session,
        viagem_nome=nome,
        arquivo="lote_0000_faixa_0.png",
        deteccoes=[_deteccao(direction="manual", classe="Panela")],
    )

    analise = analise_store.carregar_analise(nome, session=session)
    assert analise is not None
    det = analise["lote_0000_faixa_0.png"]["deteccoes"][0]
    assert det["classe"] == "Panela"
    assert det["direction"] == "manual"


def test_reprocessar_faixa_substitui_deteccoes(session: Session) -> None:
    nome = f"Viagem {uuid.uuid4().hex[:8]}"
    _viagem(session, nome)

    analise_store.persistir_deteccoes_faixa_em_sessao(
        session,
        viagem_nome=nome,
        arquivo="lote_0000_faixa_0.png",
        deteccoes=[_deteccao(), _deteccao(classe="Remendo")],
    )
    analise_store.persistir_deteccoes_faixa_em_sessao(
        session,
        viagem_nome=nome,
        arquivo="lote_0000_faixa_0.png",
        deteccoes=[_deteccao(classe="Panela")],
    )

    analise = analise_store.carregar_analise(nome, session=session)
    assert analise is not None
    deteccoes = analise["lote_0000_faixa_0.png"]["deteccoes"]
    assert [d["classe"] for d in deteccoes] == ["Panela"]

    total = session.scalar(
        select(func.count()).select_from(Deteccao).where(Deteccao.deleted_at.is_(None))
    )
    assert total == 1


def test_faixa_inexistente_nao_grava(session: Session) -> None:
    nome = f"Viagem {uuid.uuid4().hex[:8]}"
    _viagem(session, nome)

    try:
        analise_store.persistir_deteccoes_faixa_em_sessao(
            session,
            viagem_nome=nome,
            arquivo="lote_9999_faixa_0.png",
            deteccoes=[_deteccao()],
        )
    except LookupError:
        pass
    else:  # pragma: no cover - falha explícita
        raise AssertionError("Esperava LookupError para faixa inexistente")


def test_parametros_tri_tre_por_km(session: Session) -> None:
    nome = f"Viagem {uuid.uuid4().hex[:8]}"
    _viagem(session, nome)

    analise_store.persistir_parametros_em_sessao(
        session,
        viagem_nome=nome,
        parametros_por_km={
            "100": {
                "TRI (mm)": [1.0, 2.0],
                "TRE (mm)": [1.5, 2.5],
                "IRI (3E)": [3.2, 3.4],
            }
        },
    )
    # Apenas extras: séries já gravadas devem ser preservadas.
    analise_store.persistir_parametros_em_sessao(
        session,
        viagem_nome=nome,
        parametros_por_km={"100": {"IRI (3E)": [4.0]}},
    )

    parametros = analise_store.carregar_parametros(nome, session=session)
    assert parametros is not None
    assert parametros["100"]["TRI (mm)"] == [1.0, 2.0]
    assert parametros["100"]["TRE (mm)"] == [1.5, 2.5]
    assert parametros["100"]["IRI (3E)"] == [4.0]


def test_reprocessar_parametros_remove_km_ausente(session: Session) -> None:
    nome = f"Viagem {uuid.uuid4().hex[:8]}"
    _viagem(session, nome)

    analise_store.persistir_parametros_em_sessao(
        session,
        viagem_nome=nome,
        parametros_por_km={
            "100": {"TRI (mm)": [1.0]},
            "101": {"TRI (mm)": [2.0]},
        },
    )
    analise_store.persistir_parametros_em_sessao(
        session,
        viagem_nome=nome,
        parametros_por_km={"100": {"TRI (mm)": [1.5]}},
    )

    parametros = analise_store.carregar_parametros(nome, session=session)
    assert parametros is not None
    assert list(parametros) == ["100"]
    assert parametros["100"]["TRI (mm)"] == [1.5]

    ativos = session.scalar(
        select(func.count()).select_from(ParametroKm).where(ParametroKm.deleted_at.is_(None))
    )
    assert ativos == 1


def test_migra_analise_legada(session: Session) -> None:
    nome = f"Viagem {uuid.uuid4().hex[:8]}"
    _viagem(session, nome)

    # Formato antigo: lista de detecções por arquivo, sem faixa_meta.
    legado = {"lote_0000_faixa_0.png": [_deteccao()]}
    resultado = analise_store.migrar_analise_legada(
        viagem_nome=nome,
        analise=legado,
        parametros_por_km={"100": {"TRI (mm)": [1.0], "TRE (mm)": [2.0]}},
    )
    assert resultado == {"faixas": 1, "parametros": 1}

    analise = analise_store.carregar_analise(nome, session=session)
    assert analise is not None
    assert analise["lote_0000_faixa_0.png"]["deteccoes"][0]["classe"] == "Trincas"
    parametros = analise_store.carregar_parametros(nome, session=session)
    assert parametros is not None
    assert parametros["100"]["TRE (mm)"] == [2.0]


def test_viagem_fora_do_banco_cai_no_fallback(session: Session) -> None:
    assert analise_store.carregar_analise("viagem inexistente", session=session) is None
    assert analise_store.carregar_parametros("viagem inexistente", session=session) is None
