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


def test_calcula_e_persiste_icp_do_trecho(
    viagem: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    itens = [
        {"km": 33, "amostra": "ICP_1", "tipo_analise": "icp", "icp": 80.0},
        {"km": 33, "amostra": "ICP_2", "tipo_analise": "icp", "icp": 60.0},
        {"km": 34, "amostra": "ICP_3", "tipo_analise": "icp", "icp": 40.0},
    ]
    monkeypatch.setattr(
        resultado_store, "carregar_resultados", lambda *_args, **_kwargs: itens
    )
    salvo: list[dict] = []
    monkeypatch.setattr(
        resultado_store,
        "persistir_resultados",
        lambda **kwargs: salvo.append(kwargs) or 1,
    )

    resposta = _cliente(viagem).post(
        "/api/relatorios/icp/Viagem ICP/trecho",
        json={"metodo": "total", "e": 5},
    )

    assert resposta.status_code == 200
    trecho = resposta.json()["trecho"]
    assert trecho["icp"] == pytest.approx(60.0)
    assert trecho["conceito"] == "Bom"
    assert trecho["N"] == 3
    assert trecho["n_minimo"] == 3
    assert len(salvo) == 1
    assert salvo[0]["tipo_analise"] == "icp"
    persistido = salvo[0]["resultados"][0]
    assert persistido["km"] == 0
    assert persistido["nivel"] == "trecho"
    assert persistido["icp"] == pytest.approx(60.0)


def test_trecho_com_e_invalido_retorna_400(
    viagem: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    itens = [{"km": 33, "amostra": "ICP_1", "tipo_analise": "icp", "icp": 80.0}]
    monkeypatch.setattr(
        resultado_store, "carregar_resultados", lambda *_args, **_kwargs: itens
    )

    resposta = _cliente(viagem).post(
        "/api/relatorios/icp/Viagem ICP/trecho", json={"e": 0}
    )

    assert resposta.status_code == 400


def test_trecho_sem_amostras_retorna_404(
    viagem: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        resultado_store, "carregar_resultados", lambda *_args, **_kwargs: []
    )

    resposta = _cliente(viagem).post(
        "/api/relatorios/icp/Viagem ICP/trecho", json={}
    )

    assert resposta.status_code == 404


def test_get_trecho_persistido_e_separa_das_amostras(
    viagem: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    trecho = {
        "km": 0,
        "amostra": None,
        "tipo_analise": "icp",
        "nivel": "trecho",
        "valor": 64.0,
        "icp": 64.0,
        "conceito": "Bom",
        "N": 10,
        "n_minimo": 6,
    }
    amostra = {
        "km": 33,
        "amostra": "ICP_1",
        "tipo_analise": "icp",
        "valor": 80.0,
    }
    monkeypatch.setattr(
        resultado_store,
        "carregar_resultados",
        lambda *_args, **_kwargs: [trecho, amostra],
    )

    cliente = _cliente(viagem)
    consulta = cliente.get("/api/relatorios/icp/Viagem ICP/trecho")
    amostras = cliente.get("/api/relatorios/icp/Viagem ICP")

    assert consulta.status_code == 200
    assert consulta.json()["icp"] == 64.0
    assert consulta.json()["n_minimo"] == 6
    assert amostras.status_code == 200
    assert [item["amostra"] for item in amostras.json()] == ["ICP_1"]


def test_get_trecho_inexistente_retorna_404(
    viagem: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        resultado_store, "carregar_resultados", lambda *_args, **_kwargs: []
    )

    resposta = _cliente(viagem).get("/api/relatorios/icp/Viagem ICP/trecho")
    assert resposta.status_code == 404


def test_exporta_trecho_no_csv(viagem: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    itens = [
        {
            "km": 33,
            "amostra": "ICP_1",
            "tipo_analise": "icp",
            "valor": 80.0,
            "conceito": "Muito Bom",
            "defeitos": [],
        },
        {
            "km": 0,
            "amostra": None,
            "tipo_analise": "icp",
            "nivel": "trecho",
            "valor": 64.0,
            "icp": 64.0,
            "conceito": "Bom",
            "N": 10,
            "n_minimo": 6,
            "desvio_padrao": 12.5,
            "e": 5.0,
            "metodo": "amostragem_adicional",
        },
    ]
    monkeypatch.setattr(
        resultado_store, "carregar_resultados", lambda *_args, **_kwargs: itens
    )

    resposta = _cliente(viagem).get("/api/relatorios/icp/Viagem ICP/exportar")

    assert resposta.status_code == 200
    linhas = resposta.text.strip().splitlines()
    assert "N_MINIMO" in linhas[0]
    assert "METODO" in linhas[0]
    assert "TRECHO" in linhas[-1]
    assert "amostragem_adicional" in linhas[-1]
