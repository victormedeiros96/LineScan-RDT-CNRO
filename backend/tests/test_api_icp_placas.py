"""Endpoints do inventário de placas e amostras de ICP."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.core.config import Settings
from src.core.dependencies import get_settings_dep
from src.main import app


@pytest.fixture()
def client(tmp_path: Path):
    viagem = tmp_path / "BR-364"
    viagem.mkdir()
    faixas = []
    for indice in range(4):
        inicio = indice * 0.005
        faixas.append(
            {
                "arquivo": f"lote_0000_faixa_{indice}.png",
                "bloco_indice": 0,
                "faixa_index": indice,
                "km_inicio": inicio,
                "km_fim": inicio + 0.005,
                "altura_px": 5120,
            }
        )
    meta = {
        "bloco": {"indice": 0, "lado": "left", "km_inicio": 0.0, "km_fim": 0.020},
        "faixas": faixas,
    }
    (viagem / "lote_0000_meta.json").write_text(
        json.dumps(meta), encoding="utf-8"
    )

    settings = Settings(dados_dir=tmp_path)
    app.dependency_overrides[get_settings_dep] = lambda: settings
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def test_gera_e_le_inventario_de_placas(client: TestClient) -> None:
    resposta = client.post(
        "/api/relatorios/icp/BR-364/placas",
        json={"periodo_m": 5.12, "gerar_rois": False},
    )
    assert resposta.status_code == 200, resposta.text
    corpo = resposta.json()
    assert corpo["status"] == "ok"
    assert corpo["total_placas"] == 3
    assert corpo["total_amostras"] == 1
    assert corpo["inventario"]["placas"][0]["comprimento_m"] == pytest.approx(5.12)

    listagem = client.get("/api/relatorios/icp/BR-364/placas")
    assert listagem.status_code == 200
    assert listagem.json()["trecho"]["n_faixas"] == 4

    amostras = client.get("/api/relatorios/icp/BR-364/amostras")
    assert amostras.status_code == 200
    assert amostras.json()["amostras"][0]["n_placas"] == 3


def test_calibra_pelas_juntas_informadas(client: TestClient) -> None:
    juntas = [i * 5.12 / 1000 for i in range(4)]
    resposta = client.post(
        "/api/relatorios/icp/BR-364/placas",
        json={"juntas_km": juntas, "gerar_rois": False},
    )
    assert resposta.status_code == 200, resposta.text
    assert resposta.json()["inventario"]["calibracao"]["periodo_m"] == pytest.approx(
        5.12, abs=0.01
    )


def test_periodo_ausente_e_erro_claro(client: TestClient) -> None:
    resposta = client.post(
        "/api/relatorios/icp/BR-364/placas", json={"gerar_rois": False}
    )
    assert resposta.status_code == 400
    assert "periodo_m" in resposta.json()["detail"]
