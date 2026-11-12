"""Calibração de juntas do rígido (RDT1-22).

Cobre o caminho manual (``juntas_km`` e ``periodo_m``) e a falha explícita
quando o modelo externo não está configurado. A detecção com o modelo real é
validada na imagem de referência no ambiente com GPU.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from src.services import icp_juntas
from src.services.icp_placas import FaixaInfo


def _faixas(km_inicio: float = 0.0, n_blocos: int = 6) -> list[FaixaInfo]:
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


def test_calibrar_manual_por_juntas_km() -> None:
    km0 = 326.0
    juntas = [km0 + (1.2 + i * 5.12) / 1000 for i in range(24)]

    cal = icp_juntas.calibrar_manual(juntas_km=juntas, km0=km0)

    assert cal["fonte"] == "juntas_km"
    assert abs(cal["periodo_m"] - 5.12) < 0.01
    assert abs(cal["fase_m"]) < 0.02


def test_calibrar_manual_por_periodo_e_fase() -> None:
    cal = icp_juntas.calibrar_manual(periodo_m=5.12, fase_m=0.30)

    assert cal["fonte"] == "manual"
    assert cal["periodo_m"] == 5.12
    assert cal["fase_m"] == 0.30
    assert cal["norma"] == "DNIT 062/2004-PRO"


def test_calibrar_manual_rejeita_periodo_invalido() -> None:
    with pytest.raises(ValueError):
        icp_juntas.calibrar_manual(periodo_m=0)


def test_calibrar_viagem_prefere_juntas_km(tmp_path: Path) -> None:
    base = tmp_path
    km0 = 100.0
    juntas = [km0 + (0.5 + i * 4.10) / 1000 for i in range(30)]

    cal = icp_juntas.calibrar_viagem(
        base,
        _faixas(km_inicio=km0),
        config={"juntas_km": juntas, "periodo_m": 5.12},
    )

    assert cal["fonte"] == "juntas_km"
    assert abs(cal["periodo_m"] - 4.10) < 0.01


def test_calibrar_viagem_usa_detector_e_falha_sem_modelo(tmp_path: Path) -> None:
    detector = icp_juntas.ConfigJuntas(
        nuclio_dir=tmp_path / "nao-existe",
        modelo_pt=tmp_path / "nao-existe.pt",
    )

    with pytest.raises(RuntimeError, match="Modelo de juntas indisponível"):
        icp_juntas.calibrar_viagem(tmp_path, _faixas(), detector=detector)


def test_carregar_config_le_bloco_juntas(tmp_path: Path) -> None:
    pasta = tmp_path / "icp"
    pasta.mkdir()
    (pasta / "config.json").write_text(
        '{"nome": "x", "juntas": {"nuclio_dir": "/m/nuclio", '
        '"modelo_pt": "/m/modelo.pt", "limiar": 0.7, "mm_por_px_y": 2.0}}',
        encoding="utf-8",
    )

    cfg = icp_juntas.carregar_config(
        tmp_path, nuclio_dir=Path("/fallback"), modelo_pt=Path("/fallback.pt")
    )

    assert cfg.nuclio_dir == Path("/m/nuclio")
    assert cfg.modelo_pt == Path("/m/modelo.pt")
    assert cfg.limiar == 0.7
    assert cfg.mm_por_px_y == 2.0
