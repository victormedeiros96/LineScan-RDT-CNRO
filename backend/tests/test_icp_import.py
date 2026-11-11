"""Testes da importação das fichas de ICP (RDT1-17)."""

from __future__ import annotations

import csv
import json
from collections import defaultdict
from pathlib import Path

import pytest
from icp_xlsx_builder import montar_workbook

from src.services.icp_import import importar_icp, parse_km

FIXTURE = Path(__file__).parent / "fixtures" / "icp_concessionaria.json"
CASOS = json.loads(FIXTURE.read_text(encoding="utf-8"))


def _casos_por_workbook() -> dict[str, list[dict]]:
    grupos: dict[str, list[dict]] = defaultdict(list)
    for caso in CASOS:
        grupos[caso["workbook"]].append(caso)
    return grupos


def test_parse_km() -> None:
    assert parse_km("33+600") == pytest.approx(33.6)
    assert parse_km("214+400") == pytest.approx(214.4)
    assert parse_km("766+700") == pytest.approx(766.7)
    assert parse_km("-") is None
    assert parse_km(None) is None


def test_importa_as_108_amostras_das_planilhas(tmp_path: Path) -> None:
    """Reconstrói as planilhas a partir da fixture e confere as 108 amostras."""
    total = 0
    for workbook, casos in _casos_por_workbook().items():
        amostras = [
            {
                "amostra": caso["amostra"],
                "total_placas": caso["total_placas"],
                "defeitos": caso["defeitos"],
            }
            for caso in casos
        ]
        caminho = tmp_path / f"{workbook}.xlsx"
        caminho.write_bytes(montar_workbook(amostras))

        importados = {item["amostra"]: item for item in importar_icp(caminho)}
        assert set(importados) == {caso["amostra"] for caso in casos}

        for caso in casos:
            item = importados[caso["amostra"]]
            esperado = caso["esperado"]
            assert item["vdt"] == pytest.approx(esperado["vdt"], abs=1e-6)
            assert item["vdc"] == pytest.approx(esperado["vdc"], abs=1e-6)
            assert item["icp"] == pytest.approx(esperado["icp"], abs=1e-6)
            assert item["conceito"] == esperado["conceito"]
            assert item["norma"] == "DNIT 062/2004-PRO"
            assert item["total_placas"] == caso["total_placas"]
            total += 1

    assert total == 108


def test_amostra_sem_defeitos_vira_icp_100(tmp_path: Path) -> None:
    """Abas sem nenhum defeito (as 4 em branco da RAR) entram com ICP 100."""
    caminho = tmp_path / "branca.xlsx"
    caminho.write_bytes(
        montar_workbook([{"amostra": "ICP_12", "total_placas": 12, "defeitos": []}])
    )

    [item] = importar_icp(caminho)
    assert item["icp"] == 100.0
    assert item["conceito"] == "Excelente"
    assert item["defeitos"] == []


def test_importa_entradas_manuais_r_u(tmp_path: Path) -> None:
    """R/U digitados manualmente têm precedência sobre os códigos C/I."""
    caminho = tmp_path / "ppv01.xlsx"
    caminho.write_bytes(
        montar_workbook(
            [
                {
                    "amostra": "ICP_1",
                    "total_placas": 8,
                    "entradas_manuais": [(8, "B", 2), (10, "-", 8)],
                }
            ],
            km_inicial="-",
            km_final="-",
            pedagio="PVV01 (Seletiva)",
        )
    )

    [item] = importar_icp(caminho)
    assert item["km"] is None
    assert item["km_informado"] is False
    assert item["local"] == "PVV01 (Seletiva)"
    assert item["vdt"] == pytest.approx(19.7887, abs=1e-6)
    assert item["icp"] == pytest.approx(84.11105726720862, abs=1e-6)
    assert item["conceito"] == "Muito Bom"


def test_importa_csv_normalizado(tmp_path: Path) -> None:
    caminho = tmp_path / "ficha.csv"
    with caminho.open("w", encoding="utf-8", newline="") as handle:
        escritor = csv.writer(handle)
        escritor.writerow(
            [
                "amostra",
                "rodovia",
                "km_inicial",
                "total_placas",
                "tipo",
                "severidade",
                "placas_afetadas",
            ]
        )
        escritor.writerow(["ICP_1", "BR-163/MT", "33+600", 12, 5, "B", 4])
        escritor.writerow(["ICP_1", "BR-163/MT", "33+600", 12, 10, "-", 12])
        escritor.writerow(["ICP_2", "BR-163/MT", "33+600", 12, 15, "-", 1])

    importados = {item["amostra"]: item for item in importar_icp(caminho)}
    assert set(importados) == {"ICP_1", "ICP_2"}
    assert importados["ICP_1"]["km"] == 33
    assert importados["ICP_1"]["rodovia"] == "BR-163/MT"
    assert importados["ICP_1"]["vdt"] == pytest.approx(
        2.0 + 9.9919, abs=1e-4
    )
    assert importados["ICP_1"]["conceito"] == "Excelente"
    assert importados["ICP_2"]["icp"] == pytest.approx(100.0, abs=1e-9)


def test_planilha_sem_abas_icp_falha(tmp_path: Path) -> None:
    from openpyxl import Workbook

    wb = Workbook()
    wb.active.title = "Outra"
    caminho = tmp_path / "vazia.xlsx"
    wb.save(caminho)

    with pytest.raises(ValueError, match="ICP_n"):
        importar_icp(caminho)


def test_extensao_invalida_falha(tmp_path: Path) -> None:
    caminho = tmp_path / "ficha.txt"
    caminho.write_text("x", encoding="utf-8")
    with pytest.raises(ValueError, match="Formato"):
        importar_icp(caminho)
