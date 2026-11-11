from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from src.services.icp_calculator import (
    calcular_icp_amostra,
    classificar_icp,
    densidade,
    valor_deduzivel,
)

FIXTURE = Path(__file__).parent / "fixtures" / "icp_concessionaria.json"
CASOS = json.loads(FIXTURE.read_text(encoding="utf-8"))

# Ficha de exemplo da DNIT 062/2004-PRO (pág. 6), com as densidades usadas na
# planilha da concessionária. Pelo método da concessionária o resultado é
# ligeiramente diferente do impresso na norma (VDC 47,77 e ICP 52,23 em vez de
# 48 e 52) — ver a explicação no docstring de icp_calculator.
EXEMPLO_DNIT = [
    (5, "M", 0),
    (10, "U", 50),
    (11, "U", 10),
    (15, "U", 10),
    (1, "B", 20),
    (2, "B", 10),
    (2, "A", 10),
    (13, "M", 20),
    (18, "B", 5),
]


def test_ficha_de_exemplo_da_norma() -> None:
    r = calcular_icp_amostra(EXEMPLO_DNIT)
    assert r["vdt"] == pytest.approx(93.8842, abs=1e-4)
    assert r["q"] == 5
    assert r["vdc"] == pytest.approx(47.773399454599286, abs=1e-9)
    assert r["icp"] == pytest.approx(52.226600545400714, abs=1e-9)
    assert r["conceito"] == "Razoável"


def test_amostra_sem_defeitos() -> None:
    r = calcular_icp_amostra([])
    assert r == {
        "vds": [],
        "vdt": 0,
        "q": 0,
        "vdc": 0.0,
        "icp": 100.0,
        "conceito": "Excelente",
    }


def test_selagem_conta_so_a_maior_severidade() -> None:
    r = calcular_icp_amostra([(5, "B", 10), (5, "A", 10), (5, "M", 10)])
    assert len(r["vds"]) == 1
    assert r["vds"][0]["severidade"] == "A"
    assert r["vds"][0]["vd"] == 8.0


def test_defeitos_10_11_15_ignoram_severidade() -> None:
    r = calcular_icp_amostra([(10, "A", 100), (11, "B", 100), (15, "M", 100)])
    assert [v["severidade"] for v in r["vds"]] == ["U", "U", "U"]


def test_densidade_por_placas() -> None:
    assert densidade(4, 20) == pytest.approx(20.0)
    with pytest.raises(ValueError):
        densidade(1, 0)


def test_valor_deduzivel_valida_entrada() -> None:
    with pytest.raises(ValueError):
        valor_deduzivel(99, "A", 10)
    with pytest.raises(ValueError):
        valor_deduzivel(1, "X", 10)


def test_defeito_repetido_falha() -> None:
    with pytest.raises(ValueError):
        calcular_icp_amostra([(1, "B", 10), (1, "B", 20)])


@pytest.mark.parametrize(
    ("icp", "conceito"),
    [
        (0, "Destruído"),
        (9.999, "Destruído"),
        (10, "Muito Ruim"),
        (25, "Ruim"),
        (40, "Razoável"),
        (55, "Bom"),
        (70, "Muito Bom"),
        (85, "Excelente"),
        (100, "Excelente"),
    ],
)
def test_limites_de_conceito(icp: float, conceito: str) -> None:
    assert classificar_icp(icp) == conceito


@pytest.mark.parametrize(
    "caso",
    CASOS,
    ids=[f"{c['workbook']}-{c['amostra']}" for c in CASOS],
)
def test_regressao_planilhas_concessionaria(caso: dict[str, Any]) -> None:
    """Trava os 108 casos das planilhas ICP Pedágio com o resultado da origem."""
    r = calcular_icp_amostra(caso["defeitos"])
    esperado = caso["esperado"]
    assert r["vdt"] == pytest.approx(esperado["vdt"], abs=1e-9)
    assert r["vdc"] == pytest.approx(esperado["vdc"], abs=1e-9)
    assert r["icp"] == pytest.approx(esperado["icp"], abs=1e-9)
    assert r["conceito"] == esperado["conceito"]
    if esperado["q"] is not None:
        assert r["q"] == esperado["q"]
