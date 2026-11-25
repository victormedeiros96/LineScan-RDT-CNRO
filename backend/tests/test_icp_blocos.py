"""Fechamento dos blocos do ICP pelo cruzamento transversais × longitudinais (RDT1-33).

Cobre a regra de completar juntas faltantes pelo tamanho médio (junsta virtuais),
o não-forçamento de vão incoerente (bloco sem máscara) e a integração no
inventário de placas — DNIT 062/2004-PRO.
"""
from __future__ import annotations

import pytest

from src.services import icp_blocos
from src.services.icp_placas import (
    MM_POR_PX,
    FaixaInfo,
    montar_inventario,
)

MM = MM_POR_PX
PX_POR_M = 1000.0 / MM  # 1024 px/m no display do LineScan


def _px(metros: float) -> float:
    return metros * PX_POR_M


def _faixas(n_blocos: int = 1) -> list[FaixaInfo]:
    faixas: list[FaixaInfo] = []
    for bloco in range(n_blocos):
        for indice in range(4):
            inicio = bloco * 0.020 + indice * 0.005
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


def _transversais(*metros: float) -> list[dict]:
    return [
        {"y": _px(m), "angulo_graus": 0.0, "x0": 0.0, "x1": 4095.0, "origem": "detectada"}
        for m in metros
    ]


def _retas(largura_m: float = 3.6):
    altura = _px(15.0)
    return (
        icp_blocos.reta_vertical(200.0, altura),
        icp_blocos.reta_vertical(200.0 + _px(largura_m), altura),
    )


# ── Juntas virtuais pelo tamanho médio ──────────────────────────────────────
def test_vao_multiplo_do_medio_gera_juntas_virtuais() -> None:
    """Detectadas em 0, 4,5 e 13,5 m: o vão de 9 m (k=2) recebe 1 junta virtual."""
    r = icp_blocos.montar_blocos(
        _transversais(0.0, 4.5, 13.5),
        km0=100.0,
        mm_por_px_y=MM,
        retas_por_bloco={0: _retas()},
        blocos_ranges=[(0, 0.0, _px(15.0))],
    )

    assert r["tamanho_medio_m"] == 4.5
    assert [t.get("origem") for t in r["transversais"]] == [
        "detectada",
        "detectada",
        "virtual",
        "detectada",
    ]
    assert len(r["blocos"]) == 3
    assert r["blocos_sem_mascara"] == []
    assert [b["comprimento_m"] for b in r["blocos"]] == [4.5, 4.5, 4.5]
    # O bloco fechado no vão virtual é sinalizado como tal.
    assert [b["origem_juntas"] for b in r["blocos"]] == [
        "detectada",
        "virtual",
        "virtual",
    ]


def test_vao_incoerente_nao_e_forcado_e_fica_sem_mascara() -> None:
    """Vão de 11,5 m com médio de 4,5 m não é múltiplo coerente: não força."""
    r = icp_blocos.montar_blocos(
        _transversais(0.0, 4.5, 16.0),
        km0=0.0,
        mm_por_px_y=MM,
        retas_por_bloco={0: _retas()},
        blocos_ranges=[(0, 0.0, _px(20.0))],
    )

    assert r["avisos"] and "incoerente" in r["avisos"][0]
    assert any(
        t.get("origem") == "detectada" for t in r["transversais"]
    )
    assert any(
        b["motivo"].startswith("vão incoerente") for b in r["blocos_sem_mascara"]
    )
    # Só o bloco 0–4,5 m fecha; o vão incoerente fica sem máscara.
    assert len(r["blocos"]) == 1


def test_sem_longitudinais_nao_fecha_bloco() -> None:
    r = icp_blocos.montar_blocos(
        _transversais(0.0, 4.5, 9.0),
        km0=0.0,
        mm_por_px_y=MM,
        retas_por_bloco={},
        blocos_ranges=[(0, 0.0, _px(15.0))],
    )

    assert r["blocos"] == []
    assert all(
        b["motivo"] == "sem par de juntas longitudinais"
        for b in r["blocos_sem_mascara"]
    )


def test_largura_virtual_quando_so_um_bloco_tem_longitudinais() -> None:
    """Sem par no bloco, reaproveita a largura média e marca a largura como virtual."""
    r = icp_blocos.montar_blocos(
        _transversais(0.0, 4.5),
        km0=0.0,
        mm_por_px_y=MM,
        retas_por_bloco={9: _retas()},  # par de outro bloco
        blocos_ranges=[(0, 0.0, _px(10.0)), (9, _px(10.0), _px(20.0))],
    )

    assert len(r["blocos"]) == 1
    assert r["blocos"][0]["origem_largura"] == "virtual"
    assert r["blocos"][0]["largura_m"] == pytest.approx(3.6, abs=1e-3)


def test_reta_para_global_desloca_o_intercepto() -> None:
    from juntas_detector.longitudinais import Reta

    reta = Reta(a=0.1, b=100.0, n=1, comp=10.0, y0=0.0, y1=10.0,
                cobertura=1.0, residuo=0.0, obs=[(0.0, 10.0)])
    global_ = icp_blocos.reta_para_global(reta, 1000.0)

    assert global_.a == pytest.approx(0.1)
    assert global_.b == pytest.approx(100.0 - 0.1 * 1000.0)
    assert global_.x(1000.0) == pytest.approx(reta.x(0.0))


# ── Integração no inventário ────────────────────────────────────────────────
def test_montar_inventario_com_estrutura_fecha_blocos_e_marca_origem() -> None:
    estrutura = {
        "transversais": _transversais(0.0, 4.5, 13.5),
        "retas_por_bloco": {0: _retas()},
        "blocos_ranges": [(0, 0.0, _px(20.0))],
        "km0": 0.0,
    }

    inventario = montar_inventario(
        _faixas(n_blocos=1), estrutura=estrutura, mm_por_px_y=MM, mm_por_px_x=MM
    )

    assert inventario["calibracao"]["fonte"] == "detector_longitudinais"
    assert inventario["calibracao"]["n_juntas_virtuais"] == 1
    assert len(inventario["placas"]) == 3
    assert [p["id"] for p in inventario["placas"]] == ["P0001", "P0002", "P0003"]
    assert all(p["fechado"] for p in inventario["placas"])
    assert [p["origem_juntas"] for p in inventario["placas"]] == [
        "detectada",
        "virtual",
        "virtual",
    ]
    assert inventario["placas"][0]["comprimento_m"] == pytest.approx(4.5, abs=1e-3)
    assert inventario["placas"][0]["largura_m"] == pytest.approx(3.6, abs=1e-3)
    # Polígono e pontos por faixa prontos para o overlay da UI.
    assert len(inventario["placas"][0]["poligono"]) == 4
    assert inventario["placas"][0]["pontos_faixa"]
    assert inventario["blocos_sem_mascara"] == []


def test_montar_inventario_registra_blocos_sem_mascara() -> None:
    estrutura = {
        "transversais": _transversais(0.0, 4.5, 9.0),
        "retas_por_bloco": {},
        "blocos_ranges": [(0, 0.0, _px(15.0))],
        "km0": 0.0,
    }

    inventario = montar_inventario(
        _faixas(n_blocos=1), estrutura=estrutura, mm_por_px_y=MM, mm_por_px_x=MM
    )

    assert inventario["placas"] == []
    assert len(inventario["blocos_sem_mascara"]) == 2
    assert inventario["calibracao"]["n_blocos_sem_mascara"] == 2
    assert inventario["avisos"]


# ── Orquestração da detecção sobre o strip concatenado ──────────────────────
def test_detectar_estrutura_separa_transversais_e_longitudinais(
    tmp_path, monkeypatch
) -> None:
    """O modelo roda uma vez por bloco e entrega as duas famílias de juntas."""
    import numpy as np

    from src.services import icp_juntas

    altura = 20480
    largura = 4096
    ys = [1000.0, 5608.0, 10216.0]
    formas = [
        {
            "label": "junta",
            "points": [
                [0.0, y],
                [largura / 3, y],
                [2 * largura / 3, y],
                [largura - 1.0, y],
            ],
        }
        for y in ys
    ]
    for x in (200.0, 3886.4):
        for y0 in range(0, altura, 3000):
            formas.append(
                {
                    "label": "junta",
                    "points": [
                        [x, float(y0)],
                        [x, float(y0 + 900)],
                        [x, float(y0 + 1800)],
                        [x, float(y0 + 2800)],
                    ],
                }
            )

    class FakeProvedor:
        def __init__(self, *_a, **_k):
            pass

        def detectar(self, _imagem):
            return formas

        @staticmethod
        def para_pontos(formas, rotulo="junta"):
            return [
                np.asarray(f["points"], float)
                for f in formas
                if f.get("label") == rotulo and len(f.get("points", [])) >= 4
            ]

    import juntas_detector

    monkeypatch.setattr(juntas_detector, "ProvedorTrincaDetector", FakeProvedor)
    monkeypatch.setattr(
        icp_juntas,
        "_strip_do_bloco",
        lambda _base, _faixas: (
            np.zeros((altura, largura), dtype=np.uint8),
            altura,
            0.0,
            1.0,
        ),
    )

    (tmp_path / "nuclio").mkdir()
    (tmp_path / "modelo.pt").write_text("x")
    config = icp_juntas.ConfigJuntas(
        nuclio_dir=tmp_path / "nuclio", modelo_pt=tmp_path / "modelo.pt"
    )

    estrutura = icp_juntas.detectar_estrutura(tmp_path, _faixas(), config)

    assert len(estrutura["transversais"]) == 3
    assert all(t["origem"] == "detectada" for t in estrutura["transversais"])
    assert 0 in estrutura["retas_por_bloco"]
    esq, dir_ = estrutura["retas_por_bloco"][0]
    largura_m = abs(dir_.x(altura / 2) - esq.x(altura / 2)) * MM / 1000.0
    assert largura_m == pytest.approx(3.6, abs=0.05)
    assert estrutura["blocos_ranges"][0][0] == 0

    inventario = montar_inventario(
        _faixas(), estrutura=estrutura, mm_por_px_y=MM, mm_por_px_x=MM
    )
    assert len(inventario["placas"]) == 2
    assert inventario["placas"][0]["origem_largura"] == "detectada"


# ── Filtro + rede periódica (RDT1-37) ───────────────────────────────────────
def test_aplicar_rede_periodica_filtra_espurias_e_completa_virtuais() -> None:
    """Rede de 5 m: duplicata e falsos positivos fora da rede são descartados.

    Caso conferido à mão (y em metros, período 5 m, fase 0, tolerância 1,25 m):
    detecções 0,0 / 0,2 (duplicata) / 2,0 (espúria) / 5,0 / 9,9 / 12,0 (espúria).
    Linhas da rede: 0, 5, 10, 15. Resultado esperado: 0, 5, 10 (detectadas) e
    15 (virtual); as de 2,0 e 12,0 ficam fora da rede (2 descartadas).
    """
    transversais = [
        {"y": _px(0.0), "angulo_graus": 0.0, "x0": 0.0, "x1": 100.0},
        {"y": _px(0.2), "angulo_graus": 1.0, "x0": 0.0, "x1": 4095.0},
        {"y": _px(2.0), "angulo_graus": 0.0, "x0": 0.0, "x1": 4095.0},
        {"y": _px(5.0), "angulo_graus": 0.0, "x0": 0.0, "x1": 4095.0},
        {"y": _px(9.9), "angulo_graus": 0.0, "x0": 0.0, "x1": 4095.0},
        {"y": _px(12.0), "angulo_graus": 0.0, "x0": 0.0, "x1": 4095.0},
    ]

    r = icp_blocos.aplicar_rede_periodica(
        transversais,
        periodo_px=_px(5.0),
        fase_px=0.0,
        y_inicio=0.0,
        y_fim=_px(15.0),
        mm_por_px_y=MM,
    )

    assert [round(t["y"] / PX_POR_M, 3) for t in r["transversais"]] == [0.0, 5.0, 10.0, 15.0]
    assert [t["origem"] for t in r["transversais"]] == [
        "detectada",
        "detectada",
        "detectada",
        "virtual",
    ]
    # A duplicata de 0,2 m foi absorvida pela linha 0 e prevaleceu a de maior
    # cobertura lateral (x1=4095); a virtual herdou essa cobertura.
    assert r["transversais"][0]["x1"] == pytest.approx(4095.0)
    assert r["transversais"][3]["x1"] == pytest.approx(4095.0)
    assert r["n_detectadas_fora_rede"] == 2
    assert r["tamanho_medio_m"] == pytest.approx(5.0, abs=1e-3)


def test_montar_blocos_com_rede_periodica_gera_placas_coerentes() -> None:
    """Com período 5 m, os blocos saem em 5 m mesmo com duplicatas/espúrias."""
    r = icp_blocos.montar_blocos(
        _transversais(0.0, 0.2, 2.0, 5.0, 9.9),
        km0=0.0,
        mm_por_px_y=MM,
        retas_por_bloco={0: _retas()},
        blocos_ranges=[(0, 0.0, _px(15.0))],
        periodo_m=5.0,
        fase_m=0.0,
    )

    assert len(r["blocos"]) == 3
    assert [b["comprimento_m"] for b in r["blocos"]] == pytest.approx([5.0, 5.0, 5.0], abs=1e-3)
    assert [b["origem_juntas"] for b in r["blocos"]] == [
        "detectada",
        "detectada",
        "virtual",
    ]
    assert r["n_detectadas_fora_rede"] == 1  # a de 2,0 m


def test_montar_inventario_com_rede_periodica_aplica_na_calibracao() -> None:
    estrutura = {
        "transversais": _transversais(0.0, 2.0, 5.0, 10.0),
        "retas_por_bloco": {0: _retas()},
        "blocos_ranges": [(0, 0.0, _px(15.0))],
        "km0": 0.0,
    }

    inventario = montar_inventario(
        _faixas(n_blocos=1),
        estrutura=estrutura,
        periodo_m=5.0,
        fase_m=0.0,
        mm_por_px_y=MM,
        mm_por_px_x=MM,
    )

    assert len(inventario["placas"]) == 3
    assert [p["comprimento_m"] for p in inventario["placas"]] == pytest.approx(
        [5.0, 5.0, 5.0], abs=1e-3
    )
    cal = inventario["calibracao"]
    assert cal["n_juntas"] == 4  # linhas 0, 5, 10 e 15 (virtual)
    assert cal["n_juntas_virtuais"] == 1
    assert cal["n_juntas_fora_rede"] == 1  # a de 2,0 m
    assert [t["origem"] for t in cal["juntas_geometria"]] == [
        "detectada",
        "detectada",
        "detectada",
        "virtual",
    ]
