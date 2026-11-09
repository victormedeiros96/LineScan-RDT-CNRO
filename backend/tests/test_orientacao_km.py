from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np
import pytest

from src.services import image_processor as ip
from src.services.igg_calculator import montar_estacoes_por_km

# Cada frame sintético tem um tom sólido que codifica o número do pulso
# (frame_0000 = primeiro pulso da gravação). Com o CLAHE neutralizado no teste,
# o tom é preservado e permite identificar qual pulso caiu em cada extremo.
TOM_BASE = 10
TOM_PASSO = 20


def _tom(pulso: int) -> int:
    return TOM_BASE + pulso * TOM_PASSO


def _pulso_do_tom(tom: int) -> int:
    return round((int(tom) - TOM_BASE) / TOM_PASSO)


def _criar_frames(pasta: Path, n: int = 10) -> list[Path]:
    pasta.mkdir(parents=True, exist_ok=True)
    caminhos = []
    for pulso in range(n):
        img = np.full((ip.ALTURA_PX, ip.LARGURA_PX, 3), _tom(pulso), dtype=np.uint8)
        caminho = pasta / f"frame_{pulso:04d}.png"
        assert cv2.imwrite(str(caminho), img)
        caminhos.append(caminho)
    return caminhos


def _rodar_lote(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    sentido: str,
    faixa_alvo: int,
) -> ip.LoteResult:
    # Neutraliza o CLAHE para que os tons sólidos cheguem intactos ao PNG salvo.
    monkeypatch.setattr(ip, "apply_clahe", lambda img, **_kw: img)
    frames = _criar_frames(tmp_path / "frames")
    destino = tmp_path / f"dest_{sentido}"
    args = (
        0,
        [str(p) for p in frames],
        str(destino),
        "left",
        5.0,
        sentido,
        2.0,
        8,
        faixa_alvo,
    )
    resultado = ip._processar_lote(args)
    assert resultado is not None
    return resultado


def _pulso_no_extremo(img: np.ndarray, linha: int) -> int:
    tom = img[linha, ip.LARGURA_PX // 2, 0]
    return _pulso_do_tom(tom)


def test_crescente_nao_inverte_e_amostra_topo(tmp_path, monkeypatch) -> None:
    resultado = _rodar_lote(tmp_path, monkeypatch, sentido="crescente", faixa_alvo=0)
    faixa = resultado.faixas[0]

    salva = cv2.imread(str(faixa.caminho))
    assert salva is not None
    # Crescente: topo = primeiro pulso (menor KM), base = pulsos seguintes.
    assert _pulso_no_extremo(salva, 0) == 0
    assert _pulso_no_extremo(salva, salva.shape[0] - 1) == 2

    # A faixa 0 (topo) é o primeiro 5 m do bloco: KM sobe do topo para a base.
    assert faixa.km_inicio == pytest.approx(5.0)
    assert faixa.km_fim == pytest.approx(5.005)
    assert faixa.imagens_raw == ["frame_0000.png", "frame_0001.png", "frame_0002.png"]


def test_decrescente_inverte_e_amostra_base(tmp_path, monkeypatch) -> None:
    resultado = _rodar_lote(tmp_path, monkeypatch, sentido="decrescente", faixa_alvo=3)
    faixa = resultado.faixas[0]

    salva = cv2.imread(str(faixa.caminho))
    assert salva is not None
    # Decrescente: os primeiros pulsos (maior KM) vão para a base; topo = menor KM.
    assert _pulso_no_extremo(salva, 0) == 2
    assert _pulso_no_extremo(salva, salva.shape[0] - 1) == 0

    # A faixa 3 (base) é o primeiro 5 m da gravação: KM 4,995–5,000.
    assert faixa.km_inicio == pytest.approx(4.995)
    assert faixa.km_fim == pytest.approx(5.0)
    assert faixa.imagens_raw == ["frame_0002.png", "frame_0001.png", "frame_0000.png"]

    meta = json.loads((Path(faixa.caminho).parent / "lote_0000_meta.json").read_text(encoding="utf-8"))
    assert meta["faixas"][0]["km_inicio"] == pytest.approx(4.995)
    assert meta["faixas"][0]["km_fim"] == pytest.approx(5.0)


@pytest.mark.parametrize(
    ("sentido", "km_inicial", "bloco", "faixa", "esperado"),
    [
        ("crescente", 1.0, 0, 0, (1.0, 1.005)),
        ("crescente", 1.0, 1, 3, (1.035, 1.04)),
        ("decrescente", 5.0, 0, 0, (4.98, 4.985)),
        ("decrescente", 5.0, 0, 3, (4.995, 5.0)),
        ("decrescente", 5.0, 1, 3, (4.975, 4.98)),
    ],
)
def test_calcular_km_faixa_casos_conferidos_a_mao(
    sentido, km_inicial, bloco, faixa, esperado
) -> None:
    assert ip.calcular_km_faixa(km_inicial, sentido, bloco, faixa) == pytest.approx(esperado)


def test_calcular_km_faixa_sem_km_inicial() -> None:
    assert ip.calcular_km_faixa(None, "decrescente", 0, 3) == (None, None)


def _base_com_config(tmp_path: Path, **config) -> Path:
    base = tmp_path / "viagem"
    base.mkdir()
    (base / "viagem_config.json").write_text(json.dumps(config), encoding="utf-8")
    return base


def test_montar_estacoes_decrescente_usa_menor_km(tmp_path) -> None:
    base = _base_com_config(tmp_path, km_inicial=5.0, sentido="decrescente")
    deteccoes = [{"patologia": "T"}]
    analise = {"lote_0000_faixa_3.png": {"deteccoes": deteccoes}}

    kms = montar_estacoes_por_km(analise, "viagem", base)

    # KM 4,995 → km 4, estação 49 (4,98–5,00).
    assert "4" in kms
    assert kms["4"]["imagens"] == ["lote_0000_faixa_3.png"]
    assert kms["4"]["estacoes"][49] == deteccoes


def test_montar_estacoes_crescente_usa_menor_km(tmp_path) -> None:
    base = _base_com_config(tmp_path, km_inicial=1.0, sentido="crescente")
    deteccoes = [{"patologia": "J"}]
    analise = {"lote_0001_faixa_0.png": {"deteccoes": deteccoes}}

    kms = montar_estacoes_por_km(analise, "viagem", base)

    # KM 1,020 → km 1, estação 1 (1,02–1,04).
    assert "1" in kms
    assert kms["1"]["estacoes"][1] == deteccoes


def test_processar_pasta_escolhe_faixa_conforme_sentido(monkeypatch, tmp_path) -> None:
    origem = tmp_path / "origem"
    origem.mkdir()
    for pulso in range(10):
        (origem / f"frame_{pulso:04d}.png").write_bytes(b"x")

    monkeypatch.setattr(ip, "process_map", lambda fn, tarefas, **_kw: [fn(t) for t in tarefas])
    capturado: dict[str, int] = {}

    def fake_lote(args):
        capturado["faixa_alvo"] = args[8]
        return None

    monkeypatch.setattr(ip, "_processar_lote", fake_lote)

    ip.processar_pasta(origem, tmp_path / "c", sentido="crescente", tipo_pista="dupla", faixa=2, workers=1)
    assert capturado["faixa_alvo"] == 0

    ip.processar_pasta(origem, tmp_path / "d", sentido="decrescente", tipo_pista="dupla", faixa=2, workers=1)
    assert capturado["faixa_alvo"] == 3
