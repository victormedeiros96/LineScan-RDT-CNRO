"""Marcações manuais de patologias do ICP (RDT1-23)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.core.config import Settings
from src.core.dependencies import get_settings_dep
from src.main import app
from src.services import resultado_store
from src.services.icp_calculator import calcular_icp_amostra


@pytest.fixture()
def client_viagem(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
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
    (viagem / "lote_0000_meta.json").write_text(json.dumps(meta), encoding="utf-8")

    settings = Settings(dados_dir=tmp_path)
    app.dependency_overrides[get_settings_dep] = lambda: settings

    estado: dict[str, list[dict]] = {"resultados": []}

    def _carregar(viagem_nome: str, tipo_analise: str | None = None, session=None):
        return [dict(item) for item in estado["resultados"]]

    def _persistir(**kwargs) -> int:
        resultados = kwargs["resultados"]
        for novo in resultados:
            estado["resultados"] = [
                item
                for item in estado["resultados"]
                if item.get("amostra") != novo.get("amostra")
            ]
            estado["resultados"].append(dict(novo))
        return len(resultados)

    monkeypatch.setattr(resultado_store, "carregar_resultados", _carregar)
    monkeypatch.setattr(resultado_store, "persistir_resultados", _persistir)
    try:
        yield TestClient(app), viagem, estado
    finally:
        app.dependency_overrides.clear()


def _gerar_inventario(client: TestClient) -> None:
    resposta = client.post(
        "/api/relatorios/icp/BR-364/placas",
        json={"periodo_m": 5.12, "gerar_rois": False},
    )
    assert resposta.status_code == 200, resposta.text


def test_marcacao_recalcula_amostra_e_persiste(client_viagem) -> None:
    client, viagem, estado = client_viagem
    _gerar_inventario(client)

    resposta = client.post(
        "/api/relatorios/icp/BR-364/marcacoes",
        json={"placa_id": "P0001", "tipo": 1, "severidade": "A", "autor": "teste"},
    )
    assert resposta.status_code == 200, resposta.text
    corpo = resposta.json()
    assert corpo["marcacao"]["placa_id"] == "P0001"
    assert corpo["marcacao"]["amostra"] == "A001"
    assert corpo["marcacao"]["criado_por"] == "teste"
    assert corpo["resultado"]["composicao"]["marcacoes_manuais"] == 1

    esperado = calcular_icp_amostra([(1, "A", 1 / 3 * 100)], 3)
    assert corpo["resultado"]["icp"] == pytest.approx(esperado["icp"])
    assert corpo["resultado"]["defeitos"][0]["placas_afetadas_manual"] == 1

    assert (viagem / "icp_marcacoes.json").is_file()
    assert estado["resultados"][0]["marcacoes"][0]["id"] == corpo["marcacao"]["id"]

    listagem = client.get("/api/relatorios/icp/BR-364/marcacoes")
    assert listagem.status_code == 200
    assert len(listagem.json()["marcacoes"]) == 1


def test_marcacao_duplicada_e_conflito(client_viagem) -> None:
    client, _viagem, _estado = client_viagem
    _gerar_inventario(client)
    payload = {"placa_id": "P0001", "tipo": 2, "severidade": "M"}
    assert client.post("/api/relatorios/icp/BR-364/marcacoes", json=payload).status_code == 200
    duplicada = client.post("/api/relatorios/icp/BR-364/marcacoes", json=payload)
    assert duplicada.status_code == 409


def test_remove_marcacao_com_soft_delete(client_viagem) -> None:
    client, _viagem, _estado = client_viagem
    _gerar_inventario(client)
    criada = client.post(
        "/api/relatorios/icp/BR-364/marcacoes",
        json={"placa_id": "P0001", "tipo": 7, "severidade": "B"},
    ).json()["marcacao"]

    removida = client.delete(f"/api/relatorios/icp/BR-364/marcacoes/{criada['id']}")
    assert removida.status_code == 200, removida.text
    assert removida.json()["resultado"]["icp"] == pytest.approx(100.0)

    ativas = client.get("/api/relatorios/icp/BR-364/marcacoes").json()["marcacoes"]
    assert ativas == []
    todas = client.get(
        "/api/relatorios/icp/BR-364/marcacoes",
        params={"incluir_removidas": True},
    ).json()["marcacoes"]
    assert len(todas) == 1
    assert todas[0]["ativo"] is False


def test_marcacao_soma_a_ficha_importada(client_viagem) -> None:
    client, viagem, estado = client_viagem
    _gerar_inventario(client)
    estado["resultados"].append(
        {
            "amostra": "A001",
            "km": 0,
            "total_placas": 3,
            "defeitos": [
                {"tipo": 2, "severidade": "M", "placas_afetadas": 1, "densidade": 33.0}
            ],
            "defeitos_ficha": [
                {"tipo": 2, "severidade": "M", "placas_afetadas": 1, "densidade": 33.0}
            ],
        }
    )

    resposta = client.post(
        "/api/relatorios/icp/BR-364/marcacoes",
        json={"placa_id": "P0002", "tipo": 2, "severidade": "M"},
    )
    assert resposta.status_code == 200, resposta.text
    defeito = resposta.json()["resultado"]["defeitos"][0]
    assert defeito["placas_afetadas_ficha"] == 1
    assert defeito["placas_afetadas_manual"] == 1
    assert defeito["placas_afetadas"] == 2


def test_marcacao_sem_inventario_e_404(client_viagem) -> None:
    client, _viagem, _estado = client_viagem
    resposta = client.post(
        "/api/relatorios/icp/BR-364/marcacoes",
        json={"placa_id": "P0001", "tipo": 1, "severidade": "A"},
    )
    assert resposta.status_code == 404
