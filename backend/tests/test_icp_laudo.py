"""Testes do laudo do ICP (DNIT 062/2004-PRO, item 5.5) — RDT1-26."""

from __future__ import annotations

import pytest

from src.services.icp_laudo import (
    LIMITE_ICP_CRITICO,
    larguras_por_amostra,
    montar_laudo,
)


def _amostra(
    amostra: str,
    icp: float,
    *,
    conceito: str | None = None,
    km: float = 33.6,
    defeitos: list[dict] | None = None,
    **extras,
) -> dict:
    return {
        "amostra": amostra,
        "tipo_analise": "icp",
        "km": int(km),
        "km_inicial": km,
        "km_final": km + 0.02,
        "rodovia": "BR-364",
        "faixa": "2",
        "data": "12/11/2026",
        "operador": "Fulano",
        "total_placas": 20,
        "vdt": sum(float(d.get("vd") or 0) for d in (defeitos or [])),
        "q": 1,
        "vdc": 100.0 - icp,
        "icp": icp,
        "conceito": conceito or ("Bom" if icp >= 55 else "Ruim"),
        "defeitos": defeitos or [],
        **extras,
    }


def test_laudo_agrega_secoes_e_amostras_criticas() -> None:
    itens = [
        _amostra(
            "ICP_1",
            84.0,
            conceito="Muito Bom",
            defeitos=[
                {"tipo": 5, "severidade": "B", "densidade": 10.0, "placas_afetadas": 2, "vd": 2.0},
                {"tipo": 10, "severidade": "U", "densidade": 100.0, "placas_afetadas": 20, "vd": 14.0},
            ],
            atipicos=["Infiltração sob a placa"],
        ),
        _amostra(
            "ICP_2",
            100.0,
            conceito="Excelente",
            km=33.62,
        ),
        _amostra(
            "ICP_3",
            LIMITE_ICP_CRITICO,
            conceito="Ruim",
            km=33.64,
            defeitos=[
                {"tipo": 5, "severidade": "A", "densidade": 25.0, "placas_afetadas": 5, "vd": 8.0},
            ],
            assentamentos=[{"descricao": "Recalque de 10 mm"}],
            buracos=[{"descricao": "Buraco junto à junta", "severidade": "M"}],
        ),
        _amostra(
            "ICP_4",
            40.01,
            conceito="Razoável",
            km=33.66,
            defeitos=[
                {"tipo": 99, "severidade": "M", "densidade": 5.0, "placas_afetadas": 1, "vd": 0.0},
            ],
        ),
    ]

    laudo = montar_laudo(itens, larguras={"ICP_1": 3.6, "ICP_3": 3.6})

    assert laudo["norma"] == "DNIT 062/2004-PRO"
    assert laudo["metadados"]["rodovia"] == "BR-364"
    assert laudo["metadados"]["km_inicio"] == pytest.approx(33.6)
    assert laudo["metadados"]["km_fim"] == pytest.approx(33.68)
    assert laudo["metadados"]["limite_icp_critico"] == 40.0

    assert laudo["total_amostras"] == 4
    assert laudo["amostras_em_branco"] == ["ICP_2"]

    # Amostra em branco no resumo, mas fora das seções de defeitos.
    assert {item["amostra"] for item in laudo["defeitos"]} == {"ICP_1", "ICP_3"}
    assert "ICP_2" not in {item["amostra"] for item in laudo["secoes"]["juntas_selante"]["itens"]}

    juntas = laudo["secoes"]["juntas_selante"]
    assert len(juntas["itens"]) == 2
    assert juntas["resumo"]["amostras_afetadas"] == 2
    assert juntas["resumo"]["juntas_afetadas"] == 7
    assert juntas["resumo"]["extensao_total_m"] == pytest.approx(25.2)
    assert juntas["resumo"]["por_severidade"] == {"A": 1, "B": 1}

    atipicos = laudo["secoes"]["defeitos_atipicos"]["itens"]
    assert {item["nome"] for item in atipicos} == {
        "Defeito 99",
        "Infiltração sob a placa",
    }
    assert laudo["secoes"]["assentamentos"]["resumo"]["total"] == 1
    assert laudo["secoes"]["buracos"]["resumo"]["total"] == 1

    # ICP = 40 entra (≤ 40); 40,01 não.
    criticas = {item["amostra"] for item in laudo["amostras_criticas"]}
    assert criticas == {"ICP_3"}

    conceitos = {item["conceito"]: item["quantidade"] for item in laudo["distribuicao_conceitos"]}
    assert conceitos == {"Ruim": 1, "Razoável": 1, "Muito Bom": 1, "Excelente": 1}

    # Sem trecho persistido, o laudo consolida pela média (item 7.3.1).
    trecho = laudo["resumo_trecho"]
    assert trecho["metodo"] == "total"
    assert trecho["icp"] == pytest.approx((84.0 + 100.0 + 40.0 + 40.01) / 4)


def test_laudo_usa_trecho_persistido() -> None:
    trecho = {
        "amostra": None,
        "nivel": "trecho",
        "tipo_analise": "icp",
        "icp": 64.0,
        "conceito": "Bom",
        "N": 2,
        "n_minimo": 1,
        "desvio_padrao": 5.0,
        "e": 5.0,
        "metodo": "total",
    }
    laudo = montar_laudo(
        [_amostra("ICP_1", 60.0), _amostra("ICP_2", 68.0), trecho]
    )
    assert laudo["resumo_trecho"]["icp"] == 64.0
    assert laudo["total_amostras"] == 2


def test_laudo_sem_amostras_falha() -> None:
    with pytest.raises(ValueError):
        montar_laudo([])


def test_larguras_por_amostra_usa_mediana() -> None:
    inventario = {
        "placas": [
            {"amostra_id": "ICP_1", "largura_m": 3.5},
            {"amostra_id": "ICP_1", "largura_m": 3.7},
            {"amostra_id": "ICP_1", "largura_m": 3.6},
            {"amostra_id": "ICP_2", "largura_m": 0},
        ]
    }
    assert larguras_por_amostra(inventario) == {"ICP_1": pytest.approx(3.6)}
    assert larguras_por_amostra(None) == {}
