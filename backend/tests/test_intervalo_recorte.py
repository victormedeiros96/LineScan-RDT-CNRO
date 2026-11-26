from __future__ import annotations

import numpy as np

from src.api.routes_folders import _recortar_intervalo


def _faixa_png(tmp_path, nome: str, altura: int, cor: int) -> dict:
    import cv2

    imagem = np.full((altura, 4, 3), cor, dtype=np.uint8)
    cv2.imwrite(str(tmp_path / nome), imagem)
    return {"arquivo": nome, "altura_px": altura}


def test_recorta_intervalo_cruzando_duas_faixas(tmp_path) -> None:
    import cv2

    faixa_a = {**_faixa_png(tmp_path, "lote_0001_faixa_0.png", 10, 10), "km_inicio": 0.000, "km_fim": 0.005}
    faixa_b = {**_faixa_png(tmp_path, "lote_0001_faixa_1.png", 10, 20), "km_inicio": 0.005, "km_fim": 0.010}

    png = _recortar_intervalo(tmp_path, [faixa_b, faixa_a], 0.0025, 0.0075)

    assert png is not None
    bloco = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_COLOR)
    # O intervalo cobre metade de cada faixa (0.0025 km de 0.005 km) → 5 linhas cada.
    assert bloco.shape[0] == 10
    assert int(bloco[0, 0, 0]) == 10  # topo = faixa A (menor KM)
    assert int(bloco[-1, 0, 0]) == 20  # base = faixa B


def test_recorta_intervalo_sem_faixa_no_trecho(tmp_path) -> None:
    faixa = {**_faixa_png(tmp_path, "lote_0001_faixa_0.png", 10, 10), "km_inicio": 0.0, "km_fim": 0.005}
    assert _recortar_intervalo(tmp_path, [faixa], 0.010, 0.020) is None
