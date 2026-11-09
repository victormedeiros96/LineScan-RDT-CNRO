from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.services.igg_calculator import calcular_igg_por_km, montar_estacoes_por_km
from src.services.image_processor import (
    deve_pular_bloco,
    modo_amostragem,
    parametros_amostragem,
)

# ── Modo de amostragem: mesma regra em image_processor e igg_calculator ──
#
# DNIT 006/2003-PRO, item 6.1 + regra operacional confirmada (#2):
#   pista simples        → 50 estações/km, passo 20 m
#   dupla faixa 1 (leve) → 25 estações/km, passo 40 m (pula bloco ímpar)
#   dupla faixa 2 (pes.) → 50 estações/km, passo 20 m


@pytest.mark.parametrize(
    ("tipo_pista", "faixa", "modo", "n_estacoes", "passo"),
    [
        ("simples", None, "50", 50, 20),
        ("simples", 1, "50", 50, 20),
        ("dupla", 1, "25", 25, 40),
        ("dupla", 2, "50", 50, 20),
        ("dupla", None, "50", 50, 20),
    ],
)
def test_parametros_amostragem(tipo_pista, faixa, modo, n_estacoes, passo) -> None:
    assert modo_amostragem(tipo_pista, faixa) == modo
    assert parametros_amostragem(tipo_pista, faixa) == (n_estacoes, passo)


def test_deve_pular_bloco_acompanha_o_modo() -> None:
    # Só o modo de 25 (dupla faixa 1) pula os blocos ímpares.
    assert deve_pular_bloco("dupla", 1) is True
    assert deve_pular_bloco("simples", None) is False
    assert deve_pular_bloco("dupla", 2) is False


def _base_com_config(tmp_path: Path, **config) -> Path:
    base = tmp_path / "viagem"
    base.mkdir(parents=True)
    (base / "viagem_config.json").write_text(json.dumps(config), encoding="utf-8")
    return base


def test_montar_estacoes_simples_50_estacoes_passo_20(tmp_path) -> None:
    # Caso conferido à mão, km_inicial 1,0 crescente:
    #   lote_0000_faixa_0 → 1,000 km  → 0 m   → estação int(0/20)  = 0
    #   lote_0000_faixa_3 → 1,015 km  → 15 m  → estação int(15/20) = 0
    #   lote_0001_faixa_0 → 1,020 km  → 20 m  → estação int(20/20) = 1
    #   lote_0002_faixa_0 → 1,040 km  → 40 m  → estação int(40/20) = 2
    base = _base_com_config(tmp_path, km_inicial=1.0, sentido="crescente", tipo_pista="simples")
    analise = {
        "lote_0000_faixa_0.png": {"deteccoes": [{"classe": "Trincas"}]},
        "lote_0000_faixa_3.png": {"deteccoes": [{"classe": "Remendo"}]},
        "lote_0001_faixa_0.png": {"deteccoes": [{"classe": "Panela"}]},
        "lote_0002_faixa_0.png": {"deteccoes": [{"classe": "Desgaste"}]},
    }

    kms = montar_estacoes_por_km(analise, "viagem", base)

    estacoes = kms["1"]["estacoes"]
    assert len(estacoes) == 50
    assert estacoes[0] == [{"classe": "Trincas"}, {"classe": "Remendo"}]
    assert estacoes[1] == [{"classe": "Panela"}]
    assert estacoes[2] == [{"classe": "Desgaste"}]


def test_montar_estacoes_dupla_faixa_1_25_estacoes_passo_40(tmp_path) -> None:
    # Só blocos pares são amostrados no modo de 25. km_inicial 1,0 crescente:
    #   lote_0000_faixa_0 → 1,000 km → 0 m  → estação int(0/40)  = 0
    #   lote_0000_faixa_3 → 1,015 km → 15 m → estação int(15/40) = 0
    #   lote_0002_faixa_0 → 1,040 km → 40 m → estação int(40/40) = 1
    #   lote_0004_faixa_0 → 1,080 km → 80 m → estação int(80/40) = 2
    base = _base_com_config(
        tmp_path, km_inicial=1.0, sentido="crescente", tipo_pista="dupla", faixa=1
    )
    analise = {
        "lote_0000_faixa_0.png": {"deteccoes": [{"classe": "Trincas"}]},
        "lote_0000_faixa_3.png": {"deteccoes": [{"classe": "Remendo"}]},
        "lote_0002_faixa_0.png": {"deteccoes": [{"classe": "Panela"}]},
        "lote_0004_faixa_0.png": {"deteccoes": [{"classe": "Desgaste"}]},
    }

    kms = montar_estacoes_por_km(analise, "viagem", base)

    estacoes = kms["1"]["estacoes"]
    assert len(estacoes) == 25
    assert estacoes[0] == [{"classe": "Trincas"}, {"classe": "Remendo"}]
    assert estacoes[1] == [{"classe": "Panela"}]
    assert estacoes[2] == [{"classe": "Desgaste"}]


def test_montar_estacoes_dupla_faixa_2_50_estacoes_passo_20(tmp_path) -> None:
    base = _base_com_config(
        tmp_path, km_inicial=1.0, sentido="crescente", tipo_pista="dupla", faixa=2
    )
    analise = {"lote_0001_faixa_0.png": {"deteccoes": [{"classe": "Panela"}]}}

    kms = montar_estacoes_por_km(analise, "viagem", base)

    estacoes = kms["1"]["estacoes"]
    assert len(estacoes) == 50
    assert estacoes[1] == [{"classe": "Panela"}]


def test_n_estacoes_entra_na_frequencia_do_igi(tmp_path) -> None:
    # n = 25 (dupla faixa 1): 1 estação com T1 em 25 → FR = 4,0% → IGI = 4,0 × 0,2 = 0,8.
    base25 = _base_com_config(
        tmp_path / "m25", km_inicial=1.0, sentido="crescente", tipo_pista="dupla", faixa=1
    )
    analise = {"lote_0000_faixa_0.png": {"deteccoes": [{"classe": "Trincas"}]}}
    kms25 = montar_estacoes_por_km(analise, "viagem", base25)
    r25 = calcular_igg_por_km(1, kms25["1"]["estacoes"], {})

    assert r25["n_estacoes"] == 25
    t1_25 = next(g for g in r25["grupos"] if g["codigo"] == "T1")
    assert t1_25["fa"] == 1
    assert t1_25["fr"] == pytest.approx(4.0)
    assert t1_25["igi"] == pytest.approx(0.8)

    # n = 50 (pista simples): mesma 1 estação com T1 em 50 → FR = 2,0% → IGI = 0,4.
    base50 = _base_com_config(tmp_path / "m50", km_inicial=1.0, sentido="crescente", tipo_pista="simples")
    kms50 = montar_estacoes_por_km(analise, "viagem", base50)
    r50 = calcular_igg_por_km(1, kms50["1"]["estacoes"], {})

    assert r50["n_estacoes"] == 50
    t1_50 = next(g for g in r50["grupos"] if g["codigo"] == "T1")
    assert t1_50["fa"] == 1
    assert t1_50["fr"] == pytest.approx(2.0)
    assert t1_50["igi"] == pytest.approx(0.4)
