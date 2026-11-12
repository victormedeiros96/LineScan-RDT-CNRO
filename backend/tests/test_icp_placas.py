"""Placas de ICP a partir das juntas (juntas-detector) e amostras de 20.

Os casos de período (5,12 m e 4,10 m) reproduzem os dois padrões medidos na
BR-364 (pista sul, faixa 2) e são a validação de referência desta etapa.
"""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from src.services.icp_placas import (
    FaixaInfo,
    agrupar_amostras,
    calibrar_por_juntas,
    gerar_placas,
    listar_faixas,
    montar_inventario,
    salvar_rois,
)


def _faixas(*, km_inicio: float = 0.0, n_blocos: int = 6) -> list[FaixaInfo]:
    """Faixas de 5 m, 4 por bloco de 20 m, contíguas a partir de ``km_inicio``."""
    faixas: list[FaixaInfo] = []
    for bloco in range(n_blocos):
        for indice in range(4):
            inicio = km_inicio + bloco * 0.020 + indice * 0.005
            faixas.append(
                FaixaInfo(
                    arquivo=f"lote_{bloco:04d}_faixa_{indice}.png",
                    bloco_indice=bloco,
                    faixa_index=indice,
                    km_inicio=inicio,
                    km_fim=inicio + 0.005,
                )
            )
    return faixas


# ── Rede de placas e validação BR-364 ────────────────────────────────────────
def test_periodo_br364_5_12_m() -> None:
    placas, descartadas = gerar_placas(_faixas(n_blocos=6), periodo_m=5.12)

    assert len(placas) == 23
    assert descartadas == []
    assert placas[0]["km_inicio"] == 0.0
    assert abs(placas[0]["comprimento_m"] - 5.12) < 1e-3
    assert abs(placas[-1]["km_fim"] - 23 * 5.12 / 1000) < 1e-3
    assert all(abs(p["comprimento_m"] - 5.12) < 0.01 for p in placas)
    for anterior, atual in zip(placas, placas[1:]):
        assert atual["km_inicio"] == anterior["km_fim"]
        assert atual["id"] > anterior["id"]


def test_periodo_br364_4_10_m() -> None:
    placas, descartadas = gerar_placas(_faixas(n_blocos=6), periodo_m=4.10)

    assert len(placas) == 29
    assert descartadas == []
    assert all(abs(p["comprimento_m"] - 4.10) < 0.01 for p in placas)
    assert abs(placas[-1]["km_fim"] - 29 * 4.10 / 1000) < 1e-3


def test_calibracao_por_juntas_recupera_o_periodo() -> None:
    juntas = [i * 5.12 / 1000 for i in range(24)]
    calibracao = calibrar_por_juntas(juntas)

    assert abs(calibracao["periodo_m"] - 5.12) < 0.01
    assert abs(calibracao["fase_m"]) < 0.02
    assert calibracao["n_na_rede"] >= 22


def test_calibracao_por_juntas_devolve_fase_em_metros() -> None:
    km0 = 326.0
    juntas = [km0 + (1.2 + i * 5.12) / 1000 for i in range(24)]
    calibracao = calibrar_por_juntas(juntas, km0=km0)

    assert abs(calibracao["periodo_m"] - 5.12) < 0.01
    assert abs(calibracao["fase_m"] - 1.2) < 0.01


# ── Coordenadas na imagem original ───────────────────────────────────────────
def test_poligono_volta_para_as_faixas_originais() -> None:
    placas, _ = gerar_placas(_faixas(n_blocos=1), periodo_m=5.12)
    placa = placas[0]

    assert placa["faixa"] == "lote_0000_faixa_0.png"
    assert placa["faixa_index"] == 0
    assert placa["faixas"] == ["lote_0000_faixa_0.png", "lote_0000_faixa_1.png"]

    # x em metros: a placa padrão de 3,6 m fica centralizada (0,2–3,8 m).
    xs = [ponto["x_m"] for ponto in placa["poligono_m"]]
    assert abs(min(xs) - 0.2) < 1e-3
    assert abs(max(xs) - 3.8) < 1e-3

    # A primeira placa começa no topo da faixa 0 e termina 120 px dentro da faixa 1.
    pontos = placa["pontos_faixa"]
    assert pontos[0]["arquivo"] == "lote_0000_faixa_0.png"
    assert pontos[0]["y"] == 0.0
    assert pontos[2]["arquivo"] == "lote_0000_faixa_1.png"
    assert abs(pontos[2]["y"] - 0.12 * 1024) < 0.5


def test_placas_sem_cobertura_sao_descartadas() -> None:
    faixas = _faixas(n_blocos=3)
    # Remove a faixa de 20–25 m: simula um bloco pulado pela amostragem.
    faixas = [f for f in faixas if f.arquivo != "lote_0001_faixa_0.png"]

    placas, descartadas = gerar_placas(faixas, periodo_m=5.12)

    assert any(d["motivo"] == "sem cobertura de faixa" for d in descartadas)
    for placa in placas:
        assert "lote_0001_faixa_0.png" not in placa["faixas"]
        assert not (
            placa["km_inicio"] < 0.025 and placa["km_fim"] > 0.020
        )


# ── Amostras de 20 placas ────────────────────────────────────────────────────
def test_agrupa_amostras_de_20_placas() -> None:
    placas, _ = gerar_placas(_faixas(n_blocos=7), periodo_m=4.10)
    assert len(placas) > 20

    amostras = agrupar_amostras(placas, tamanho=20)

    assert amostras[0]["n_placas"] == 20
    assert amostras[0]["placas"] == [p["id"] for p in placas[:20]]
    assert len(amostras) == 2
    assert amostras[1]["n_placas"] == len(placas) - 20
    assert all(p["amostra_id"] == amostras[1]["id"] for p in placas[20:])
    assert amostras[0]["km_inicio"] == placas[0]["km_inicio"]
    assert amostras[0]["km_fim"] == placas[19]["km_fim"]


def test_montar_inventario_agrupa_e_inclui_calibracao() -> None:
    inventario = montar_inventario(
        _faixas(n_blocos=6), periodo_m=5.12, calibracao={"periodo_m": 5.12, "R": 0.94}
    )

    assert inventario["norma"] == "DNIT 062/2004-PRO"
    assert inventario["calibracao"] == {"periodo_m": 5.12, "R": 0.94}
    assert inventario["trecho"]["km_inicio"] == 0.0
    assert inventario["trecho"]["km_fim"] == 0.12
    assert len(inventario["amostras"]) == 2


# ── ROIs persistidas ─────────────────────────────────────────────────────────
def test_salva_roi_por_placa(tmp_path: Path) -> None:
    altura, largura = 50, 200
    faixas = [
        FaixaInfo(
            arquivo="f0.png",
            bloco_indice=0,
            faixa_index=0,
            km_inicio=0.0,
            km_fim=0.005,
            altura_px=altura,
        ),
        FaixaInfo(
            arquivo="f1.png",
            bloco_indice=0,
            faixa_index=1,
            km_inicio=0.005,
            km_fim=0.010,
            altura_px=altura,
        ),
    ]
    for faixa in faixas:
        imagem = np.full((altura, largura, 3), 200, dtype=np.uint8)
        cv2.imwrite(str(tmp_path / faixa.arquivo), imagem)

    placa = {
        "id": "P0001",
        "faixas": ["f0.png", "f1.png"],
        "poligono_m": [
            {"x_m": 0.2, "km": 0.001},
            {"x_m": 1.0, "km": 0.001},
            {"x_m": 1.0, "km": 0.006},
            {"x_m": 0.2, "km": 0.006},
        ],
        "roi": None,
    }

    gravadas = salvar_rois(
        tmp_path,
        faixas,
        [placa],
        mm_por_px_x=10.0,
        mm_por_px_y=10.0,
        folga=2,
    )

    assert gravadas == 1
    assert placa["roi"] is not None
    assert (tmp_path / "icp_rois" / "icp_placa0001.png").is_file()


# ── Leitura das faixas ───────────────────────────────────────────────────────
def test_listar_faixas_le_metadados(tmp_path: Path) -> None:
    meta = {
        "bloco": {"indice": 2, "lado": "left", "km_inicio": 0.040, "km_fim": 0.045},
        "faixas": [
            {
                "arquivo": "lote_0002_faixa_0.png",
                "faixa_index": 0,
                "km_inicio": 0.040,
                "km_fim": 0.045,
                "altura_px": 5120,
            }
        ],
    }
    (tmp_path / "lote_0002_meta.json").write_text(
        __import__("json").dumps(meta), encoding="utf-8"
    )

    faixas = listar_faixas(tmp_path)

    assert faixas == [
        FaixaInfo(
            arquivo="lote_0002_faixa_0.png",
            bloco_indice=2,
            faixa_index=0,
            km_inicio=0.040,
            km_fim=0.045,
            altura_px=5120,
        )
    ]
