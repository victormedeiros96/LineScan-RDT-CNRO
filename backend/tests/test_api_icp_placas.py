"""Endpoints do inventário de placas e amostras de ICP."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.core.config import Settings
from src.core.dependencies import get_settings_dep
from src.main import app


def _criar_viagem(tmp_path: Path) -> Path:
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
    return viagem


@pytest.fixture()
def client(tmp_path: Path):
    _criar_viagem(tmp_path)
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


@pytest.fixture()
def client_viagem(tmp_path: Path):
    viagem = _criar_viagem(tmp_path)
    settings = Settings(dados_dir=tmp_path)
    app.dependency_overrides[get_settings_dep] = lambda: settings
    try:
        yield TestClient(app), viagem
    finally:
        app.dependency_overrides.clear()


def _gerar_inventario(client: TestClient) -> None:
    resposta = client.post(
        "/api/relatorios/icp/BR-364/placas",
        json={"periodo_m": 5.12, "gerar_rois": False},
    )
    assert resposta.status_code == 200, resposta.text


def test_detalhe_da_placa_traz_amostra_e_vizinhos(client_viagem) -> None:
    client, _viagem = client_viagem
    _gerar_inventario(client)

    primeira = client.get("/api/relatorios/icp/BR-364/placas/P0001")
    assert primeira.status_code == 200, primeira.text
    corpo = primeira.json()
    assert corpo["placa"]["id"] == "P0001"
    assert corpo["amostra"]["id"] == "A001"
    assert corpo["anterior"] is None
    assert corpo["proxima"] == "P0002"
    assert corpo["recorte_disponivel"] is False
    assert corpo["recorte_url"] is None

    ultima = client.get("/api/relatorios/icp/BR-364/placas/P0003")
    assert ultima.status_code == 200
    assert ultima.json()["anterior"] == "P0002"
    assert ultima.json()["proxima"] is None

    inexistente = client.get("/api/relatorios/icp/BR-364/placas/P9999")
    assert inexistente.status_code == 404


def test_detalhe_sem_inventario_e_404(client: TestClient) -> None:
    resposta = client.get("/api/relatorios/icp/BR-364/placas/P0001")
    assert resposta.status_code == 404
    assert "Inventário" in resposta.json()["detail"]


def test_recorte_da_placa_serve_png_e_valida_caminho(client_viagem) -> None:
    client, viagem = client_viagem
    _gerar_inventario(client)
    png = b"\x89PNG\r\n\x1a\nconteudo-fake"
    rois = viagem / "icp_rois"
    rois.mkdir()
    (rois / "icp_placa0001.png").write_bytes(png)

    inventario_path = viagem / "icp_placas.json"
    inventario = json.loads(inventario_path.read_text(encoding="utf-8"))
    inventario["placas"][0]["roi"] = {
        "arquivo": "icp_placa0001.png",
        "faixa": "lote_0000_faixa_0.png",
        "x": 0,
        "y": 0,
        "largura": 3600,
        "altura": 5120,
    }
    inventario_path.write_text(json.dumps(inventario), encoding="utf-8")

    resposta = client.get("/api/relatorios/icp/BR-364/placas/P0001/recorte")
    assert resposta.status_code == 200, resposta.text
    assert resposta.headers["content-type"] == "image/png"
    assert resposta.content == png

    sem_recorte = client.get("/api/relatorios/icp/BR-364/placas/P0002/recorte")
    assert sem_recorte.status_code == 404

    inventario["placas"][0]["roi"]["arquivo"] = "../lote_0000_meta.json"
    inventario_path.write_text(json.dumps(inventario), encoding="utf-8")
    traversal = client.get("/api/relatorios/icp/BR-364/placas/P0001/recorte")
    assert traversal.status_code == 400

    inventario["placas"][0]["roi"]["arquivo"] = "icp_placa0099.png"
    inventario_path.write_text(json.dumps(inventario), encoding="utf-8")
    ausente = client.get("/api/relatorios/icp/BR-364/placas/P0001/recorte")
    assert ausente.status_code == 404
