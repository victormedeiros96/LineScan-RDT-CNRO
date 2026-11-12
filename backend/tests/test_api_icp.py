"""Testes dos endpoints de ICP (RDT1-17)."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from icp_xlsx_builder import montar_workbook

from src.api.routes_reports import router
from src.core.config import Settings
from src.core.dependencies import get_settings_dep
from src.services import resultado_store


def _cliente(dados_dir: Path) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_settings_dep] = lambda: Settings(dados_dir=dados_dir)
    return TestClient(app)


@pytest.fixture()
def viagem(tmp_path: Path) -> Path:
    base = tmp_path / "dados" / "Viagem ICP"
    base.mkdir(parents=True, exist_ok=True)
    return tmp_path / "dados"


def test_importa_ficha_e_devolve_icps(viagem: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    chamadas: list[dict] = []
    monkeypatch.setattr(
        resultado_store,
        "persistir_resultados",
        lambda **kwargs: chamadas.append(kwargs) or len(kwargs["resultados"]),
    )
    conteudo = montar_workbook(
        [
            {
                "amostra": "ICP_1",
                "total_placas": 12,
                "defeitos": [[5, "B", 33.3333333333], [10, "U", 100.0]],
            }
        ]
    )

    resposta = _cliente(viagem).post(
        "/api/relatorios/icp/Viagem ICP",
        files={
            "file": (
                "ficha.xlsx",
                conteudo,
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
    )

    assert resposta.status_code == 200
    corpo = resposta.json()
    assert corpo["total_amostras"] == 1
    assert corpo["persistidos"] == 1
    assert corpo["resultados"][0]["icp"] == pytest.approx(86.468221664025, abs=1e-6)
    assert corpo["resultados"][0]["km"] == 33
    assert chamadas[0]["tipo_analise"] == "icp"
    assert chamadas[0]["norma"] == resultado_store.NORMA_ICP


def test_get_icp_filtra_por_km(viagem: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    itens = [
        {"km": 33, "amostra": "ICP_1", "tipo_analise": "icp", "valor": 84.11},
        {"km": 214, "amostra": "ICP_1", "tipo_analise": "icp", "valor": 70.0},
    ]
    monkeypatch.setattr(
        resultado_store, "carregar_resultados", lambda *_args, **_kwargs: itens
    )

    cliente = _cliente(viagem)
    todos = cliente.get("/api/relatorios/icp/Viagem ICP")
    filtrados = cliente.get("/api/relatorios/icp/Viagem ICP", params={"km": 214})

    assert todos.status_code == 200
    assert len(todos.json()) == 2
    assert [item["km"] for item in filtrados.json()] == [214]


def test_viagem_desconhecida_retorna_404(tmp_path: Path) -> None:
    resposta = _cliente(tmp_path).post(
        "/api/relatorios/icp/Inexistente",
        files={"file": ("ficha.xlsx", b"x", "application/octet-stream")},
    )
    assert resposta.status_code == 404


def test_arquivo_invalido_retorna_400(viagem: Path) -> None:
    resposta = _cliente(viagem).post(
        "/api/relatorios/icp/Viagem ICP",
        files={"file": ("ficha.txt", b"x", "text/plain")},
    )
    assert resposta.status_code == 400


def test_exporta_laudo_icp_em_csv(viagem: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    itens = [
        {
            "km": 33,
            "amostra": "ICP_1",
            "tipo_analise": "icp",
            "valor": 84.11,
            "conceito": "Muito Bom",
            "rodovia": "BR-364",
            "km_inicial": 33.6,
            "km_final": 33.7,
            "faixa": "2",
            "total_placas": 20,
            "vdt": 20.0,
            "q": 1,
            "vdc": 15.89,
            "defeitos": [
                {"tipo": 5, "severidade": "B", "densidade": 33.33, "placas_afetadas": 7, "vd": 2.0},
                {"tipo": 10, "severidade": "U", "densidade": 100.0, "placas_afetadas": 20, "vd": 18.0},
            ],
        }
    ]
    monkeypatch.setattr(
        resultado_store, "carregar_resultados", lambda *_args, **_kwargs: itens
    )

    resposta = _cliente(viagem).get("/api/relatorios/icp/Viagem ICP/exportar")

    assert resposta.status_code == 200
    assert "text/csv" in resposta.headers["content-type"]
    assert "icp_Viagem ICP.csv" in resposta.headers["content-disposition"]
    linhas = resposta.text.strip().splitlines()
    assert linhas[0].startswith("AMOSTRA,RODOVIA,KM_INICIAL")
    assert len(linhas) == 3
    assert "ICP_1,BR-364" in linhas[1]
    assert "Defeito na selagem das juntas" in linhas[1]
    assert "Desgaste superficial" in linhas[2]


def test_exporta_laudo_sem_resultados_retorna_404(
    viagem: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        resultado_store, "carregar_resultados", lambda *_args, **_kwargs: []
    )

    resposta = _cliente(viagem).get("/api/relatorios/icp/Viagem ICP/exportar")

    assert resposta.status_code == 404
