"""Calibração de juntas do rígido (RDT1-22).

Cobre o caminho manual (``juntas_km`` e ``periodo_m``) e a falha explícita
quando o modelo externo não está configurado. A detecção com o modelo real é
validada na imagem de referência no ambiente com GPU.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
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
    # Fase = distância da primeira junta (1,2 m) até km0.
    assert abs(cal["fase_m"] - 1.2) < 0.02


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


# ── Seleção da transversal (RDT1-31) ─────────────────────────────────────────
def _grupo(linha: dict, *, n: int = 1, comprimento: float | None = None) -> dict:
    """Grupo no formato de ``geometria.agrupar`` (t0/t1 estendidos)."""
    a, b = float(linha["a"]), float(linha["b"])
    return {
        "orientacao": "h",
        "a": a,
        "b": b,
        "t0": 0.0,
        "t1": 4095.0,
        "comprimento": comprimento if comprimento is not None else float(np.ptp([a, b])),
        "n_fragmentos": n,
        "n_pontos": n * 2,
        "angulo_graus": float(np.degrees(np.arctan(a))),
    }


def test_selecionar_transversal_prefere_a_junta_real_em_vez_do_ajuste_fora_da_imagem() -> None:
    """Mesma cobertura estendida: não pode vencer o ajuste extrapolado.

    Regressão do RDT1-31: com ``max(t1 - t0)`` todos empatavam e o primeiro
    (menor ``b``, fora da imagem) era escolhido — a junta real em ``y≈860``
    era descartada, sobrando ~2 juntas no trecho inteiro.
    """
    fora = _grupo({"a": np.tan(np.radians(42.0)), "b": -176.0}, n=1)
    real = _grupo({"a": 0.0, "b": 860.0}, n=3)

    escolhido = icp_juntas._selecionar_transversal(
        [fora, real], altura=1024, largura=4096
    )

    assert escolhido is real


def test_selecionar_transversal_rejeita_trinca_diagonal() -> None:
    diagonal = _grupo({"a": np.tan(np.radians(35.0)), "b": 600.0}, n=5)
    horizontal = _grupo({"a": 0.0, "b": 640.0}, n=1)

    escolhido = icp_juntas._selecionar_transversal(
        [diagonal, horizontal], altura=1024, largura=4096
    )

    assert escolhido is horizontal


def test_selecionar_transversal_sem_grupo_valido() -> None:
    fora = _grupo({"a": np.tan(np.radians(45.0)), "b": -800.0}, n=4)

    assert icp_juntas._selecionar_transversal([fora], altura=1024, largura=4096) is None


class _ProvedorFake:
    def __init__(self, base: Path, faixas: list[FaixaInfo]):
        self._faixas = faixas

    def __len__(self) -> int:
        return len(self._faixas)

    def frame(self, _indice: int) -> np.ndarray:
        return np.zeros((1024, 4096), dtype=np.uint8)

    def km_da_janela(self, _indice: int, y: float) -> float:
        return 100.0 + y * icp_juntas.MM_POR_PX_Y_PADRAO / 1_000_000.0


class _ModeloFake:
    """Devolve duas polilinhas: uma extrapolada (fora) e a junta real."""

    def __init__(self, *_args, **_kwargs) -> None:
        pass

    @staticmethod
    def para_pontos(formas, rotulo: str = "junta") -> list[np.ndarray]:
        return [
            np.asarray(forma["points"], float).reshape(-1, 2)
            for forma in formas
            if forma.get("label") == rotulo
        ]

    def detectar(self, _imagem) -> list[dict]:
        def linha(x0: float, y0: float, x1: float, y1: float) -> dict:
            return {
                "confidence": "1.0",
                "label": "junta",
                "type": "polyline",
                "points": [x0, y0, x1, y1],
            }

        return [
            linha(0.0, -176.0, 2048.0, -176.0 + 0.9 * 2048.0),
            linha(0.0, 860.0, 2048.0, 860.0),
            linha(2048.0, 862.0, 4095.0, 862.0),
        ]


def test_detectar_juntas_geometria_usa_melhor_por_janela(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import juntas_detector

    monkeypatch.setattr(icp_juntas, "_ProvedorFaixas", _ProvedorFake)
    monkeypatch.setattr(juntas_detector, "ProvedorTrincaDetector", _ModeloFake)
    cfg = icp_juntas.ConfigJuntas(
        nuclio_dir=tmp_path, modelo_pt=tmp_path / "modelo.pt"
    )
    (tmp_path / "modelo.pt").write_text("x", encoding="utf-8")

    juntas = icp_juntas.detectar_juntas_geometria(
        tmp_path, _faixas(n_blocos=1)[:1], cfg
    )

    assert len(juntas) == 1
    # Junta real em y≈860–862 → ~1,68 m; o ajuste fora da imagem não entra.
    assert juntas[0]["km"] == pytest.approx(100.00168, abs=1e-4)
    assert abs(juntas[0]["angulo_graus"]) < 1.0


def test_detectar_estrutura_aplica_offset_de_bloco_em_km(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regressão RDT1-36: o offset do bloco é km→px (×1.000), não km×px.

    Com o fator errado, as juntas do bloco 1 caíam no KM do bloco 0 e todas as
    placas ficavam empilhadas no início da viagem.
    """
    import juntas_detector

    def strip_fake(_base, faixas_bloco):
        return (
            np.zeros((1024, icp_juntas.icp_placas.LARGURA_FAIXA_PX), dtype=np.uint8),
            1024,
            faixas_bloco[0].km_inicio,
        )

    monkeypatch.setattr(icp_juntas, "_strip_do_bloco", strip_fake)
    monkeypatch.setattr(juntas_detector, "ProvedorTrincaDetector", _ModeloFake)
    cfg = icp_juntas.ConfigJuntas(
        nuclio_dir=tmp_path, modelo_pt=tmp_path / "modelo.pt"
    )
    (tmp_path / "modelo.pt").write_text("x", encoding="utf-8")

    estrutura = icp_juntas.detectar_estrutura(tmp_path, _faixas(n_blocos=2), cfg)

    por_bloco = {
        t["bloco_indice"]: t["km"] for t in estrutura["transversais"]
    }
    # Junta local em y≈860–862 → 0,84 m dentro de cada bloco de 20 m.
    assert por_bloco[0] == pytest.approx(0.00084, abs=1e-4)
    assert por_bloco[1] == pytest.approx(0.02084, abs=1e-4)


def test_agrupar_juntas_nao_funde_juntas_de_metros_diferentes() -> None:
    """Regressão RDT1-31: a tolerância é de 15 cm, não de 150 m.

    ``km`` é em quilômetros; comparar direto com ``TOLERANCIA_JUNTA_M``
    (metros) tratava juntas a 3 m como duplicatas e deixava ~1 por 100 m.
    """
    juntas = [
        {"km": 100.0016},
        {"km": 100.0046},  # 3 m depois: junta diferente
        {"km": 100.0047},  # 10 cm depois da anterior: duplicata
    ]

    agrupadas = icp_juntas._agrupar_juntas(juntas)

    assert [round(j["km"], 4) for j in agrupadas] == [100.0016, 100.0046]
