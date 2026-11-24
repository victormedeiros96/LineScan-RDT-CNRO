"""Cálculo do ICP do trecho (RDT1-25) — DNIT 062/2004-PRO, itens 6 e 7.3.

Os casos são conferidos à mão. O exemplo da norma usa a mesma ficha de
`test_icp_calculator` (ICP 52,23 / Razoável pelo método da concessionária).
"""
from __future__ import annotations

import math

import pytest

from src.services.icp_calculator import calcular_icp_amostra
from src.services.icp_trecho import (
    METODO_AMOSTRAGEM,
    METODO_AMOSTRAGEM_ADICIONAL,
    METODO_TOTAL,
    calcular_icp_trecho,
    eh_trecho,
    para_persistencia,
)

# Ficha de exemplo da DNIT 062/2004-PRO, a mesma de `test_icp_calculator`.
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


def _amostras(*icps: float) -> list[dict[str, object]]:
    return [
        {"amostra": f"ICP_{indice + 1}", "icp": icp}
        for indice, icp in enumerate(icps)
    ]


def test_exemplo_da_norma_vira_trecho_razoavel() -> None:
    """Um trecho de uma amostra é a média (item 7.3.1/7.3.2) = ICP da amostra."""
    amostra = calcular_icp_amostra(EXEMPLO_DNIT)
    assert amostra["icp"] == pytest.approx(52.226600545400714, abs=1e-9)
    assert amostra["conceito"] == "Razoável"

    trecho = calcular_icp_trecho(
        [{"amostra": "ICP_1", "icp": amostra["icp"]}]
    )

    assert trecho["metodo"] == METODO_TOTAL
    assert trecho["icp"] == pytest.approx(52.226600545400714, abs=1e-9)
    assert trecho["conceito"] == "Razoável"
    assert trecho["N"] == 1
    assert trecho["n_amostras"] == 1
    assert trecho["desvio_padrao"] == 0.0
    assert trecho["n_minimo"] is None


def test_inspecao_total_usa_media_aritmetica() -> None:
    trecho = calcular_icp_trecho(_amostras(80, 60, 40))
    assert trecho["icp"] == pytest.approx(60.0)
    assert trecho["conceito"] == "Bom"
    assert trecho["ICP1"] == pytest.approx(60.0)
    assert trecho["ICP2"] is None
    assert trecho["A"] == 0


def test_amostragem_sem_adicionais_usa_media() -> None:
    trecho = calcular_icp_trecho(_amostras(80, 60, 40), metodo=METODO_AMOSTRAGEM)
    assert trecho["metodo"] == METODO_AMOSTRAGEM
    assert trecho["icp"] == pytest.approx(60.0)


def test_amostragem_com_adicionais_aplica_formula_7_3_3() -> None:
    # N = 10: 8 amostras normais com ICP 60 e 2 adicionais com ICP 80.
    # ICPt = ((10 - 2) / 10) * 60 + (2 / 10) * 80 = 48 + 16 = 64.
    amostras = _amostras(*([60.0] * 8 + [80.0] * 2))
    trecho = calcular_icp_trecho(
        amostras,
        metodo=METODO_AMOSTRAGEM_ADICIONAL,
        adicionais=["ICP_9", "ICP_10"],
    )

    assert trecho["N"] == 10
    assert trecho["A"] == 2
    assert trecho["ICP1"] == pytest.approx(60.0)
    assert trecho["ICP2"] == pytest.approx(80.0)
    assert trecho["icp"] == pytest.approx(64.0)
    assert trecho["conceito"] == "Bom"
    assert trecho["adicionais"] == ["ICP_10", "ICP_9"]


def test_desvio_padrao_e_numero_minimo_de_amostras() -> None:
    # Valores 50, 60, 70, 80, 90: média 70; S amostral = sqrt(1000 / 4) =
    # sqrt(250) = 15,8113883.
    # n = 4 * 5 * S² / (5² * (5 - 1) + 4 * S²)
    #   = 5000 / (100 + 1000) = 4,5454... -> 5 amostras (arredonda para cima).
    trecho = calcular_icp_trecho(_amostras(50, 60, 70, 80, 90), e=5)

    desvio = math.sqrt(250)
    assert trecho["desvio_padrao"] == pytest.approx(desvio, abs=1e-12)
    assert trecho["n_minimo_exato"] == pytest.approx(4.545454545454545, abs=1e-12)
    assert trecho["n_minimo"] == 5
    assert trecho["n_disponivel"] is True
    assert trecho["e"] == 5.0


def test_sem_e_calcula_icp_e_desvio_mas_nao_n() -> None:
    trecho = calcular_icp_trecho(_amostras(50, 60, 70), e=None)
    assert trecho["icp"] == pytest.approx(60.0)
    assert trecho["desvio_padrao"] == pytest.approx(10.0)
    assert trecho["n_minimo"] is None
    assert trecho["n_minimo_exato"] is None
    assert trecho["n_disponivel"] is False


@pytest.mark.parametrize("erro", [0, -1, 0.0, float("inf")])
def test_e_invalido_falha(erro: float) -> None:
    with pytest.raises(ValueError):
        calcular_icp_trecho(_amostras(60, 70), e=erro)


def test_sem_amostras_falha() -> None:
    with pytest.raises(ValueError):
        calcular_icp_trecho([])


def test_n_divergente_do_inventario_falha() -> None:
    with pytest.raises(ValueError):
        calcular_icp_trecho(_amostras(60, 70), n=3)


def test_metodo_invalido_falha() -> None:
    with pytest.raises(ValueError):
        calcular_icp_trecho(_amostras(60), metodo="qualquer")


def test_adicionais_precisam_existir_e_nao_ser_o_trecho_inteiro() -> None:
    with pytest.raises(ValueError):
        calcular_icp_trecho(
            _amostras(60, 70),
            metodo=METODO_AMOSTRAGEM_ADICIONAL,
            adicionais=["ICP_9"],
        )
    with pytest.raises(ValueError):
        calcular_icp_trecho(
            _amostras(60, 70),
            metodo=METODO_AMOSTRAGEM_ADICIONAL,
            adicionais=["ICP_1", "ICP_2"],
        )
    with pytest.raises(ValueError):
        calcular_icp_trecho(
            _amostras(60, 70), metodo=METODO_AMOSTRAGEM_ADICIONAL
        )


def test_para_persistencia_usa_km_sentinela_e_amostra_nula() -> None:
    trecho = calcular_icp_trecho(_amostras(80, 60))
    dado = para_persistencia(trecho, km_inicio=33.6, km_fim=33.7)

    assert dado["nivel"] == "trecho"
    assert dado["km"] == 0
    assert dado["km_inicio"] == 33.6
    assert dado["km_fim"] == 33.7
    assert dado["icp"] == pytest.approx(70.0)
    assert eh_trecho({"nivel": "trecho"})
    assert eh_trecho({"amostra": None, "tipo_analise": "icp"})
    assert not eh_trecho({"amostra": "ICP_1", "tipo_analise": "icp"})
