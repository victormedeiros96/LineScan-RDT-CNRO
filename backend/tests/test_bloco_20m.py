from __future__ import annotations

import numpy as np

from src.api.routes_folders import _montar_bloco_20m


def _faixa(tmp_path, indice_bloco: int, indice_faixa: int, altura: int, cor: int):
    import cv2

    imagem = np.full((altura, 4, 3), cor, dtype=np.uint8)
    caminho = tmp_path / f"lote_{indice_bloco:04d}_faixa_{indice_faixa}.png"
    cv2.imwrite(str(caminho), imagem)
    return caminho


def test_monta_bloco_20m_empilhando_as_faixas(tmp_path) -> None:
    import cv2

    # Faixa 0 é o topo (menor KM); cor identifica a ordem no empilhamento.
    for faixa in range(4):
        _faixa(tmp_path, 1, faixa, altura=5, cor=10 * (faixa + 1))

    caminho = _montar_bloco_20m(tmp_path, "lote_0001_20m.png")

    assert caminho is not None
    assert caminho.is_file()
    bloco = cv2.imread(str(caminho))
    assert bloco.shape[0] == 4 * 5  # 4 faixas de 5 linhas empilhadas
    # Primeiras linhas = faixa 0 (cor 10), últimas = faixa 3 (cor 40).
    assert int(bloco[0, 0, 0]) == 10
    assert int(bloco[-1, 0, 0]) == 40


def test_nao_monta_para_nome_que_nao_e_bloco(tmp_path) -> None:
    _faixa(tmp_path, 2, 0, altura=5, cor=99)

    assert _montar_bloco_20m(tmp_path, "lote_0002_faixa_0.png") is None
    assert _montar_bloco_20m(tmp_path, "lote_0002_20m.png") is not None
