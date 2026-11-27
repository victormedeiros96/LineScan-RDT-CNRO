from __future__ import annotations

import numpy as np

from src.services import icp_display


def test_thumbnail_respeita_largura_maxima_e_e_jpeg() -> None:
    imagem = np.zeros((20, 10, 3), dtype=np.uint8)
    jpeg = icp_display.thumbnail_jpeg(imagem, largura_max=10, qualidade=70)

    assert jpeg[:2] == b"\xff\xd8"  # assinatura JPEG
    import cv2

    volta = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
    assert max(volta.shape[:2]) <= 10
    assert volta.shape[0] == 10  # 20x10 -> 10x5 (maior lado = 10)


def test_tile_display_rotaciona_para_orientacao_do_viewer() -> None:
    # Fonte: linhas = KM (v), colunas = lateral (u).
    altura_km, largura_lateral = 4, 6
    valores = np.arange(altura_km * largura_lateral, dtype=np.uint8).reshape(
        altura_km, largura_lateral
    )
    imagem = np.stack([valores, valores, valores], axis=2)

    x, y, tamanho = 1, 2, 2
    tile = icp_display.tile_display(imagem, x, y, tamanho)

    assert tile is not None
    assert tile.shape[:2] == (tamanho, tamanho)
    # display[a][b] = fonte[H-1-(x+b)][y+a]
    esperado = np.array(
        [
            [
                valores[altura_km - 1 - (x + b), y + a]
                for b in range(tamanho)
            ]
            for a in range(tamanho)
        ],
        dtype=np.uint8,
    )
    assert np.array_equal(tile[:, :, 0], esperado)


def test_tile_fora_dos_limites_retorna_none() -> None:
    imagem = np.zeros((4, 6, 3), dtype=np.uint8)
    assert icp_display.tile_display(imagem, 10, 0, 2) is None
    assert icp_display.tile_display(imagem, 0, 10, 2) is None


def test_imagem_fonte_monta_bloco_de_20m(tmp_path) -> None:
    import cv2

    altura, largura = 5, 4
    for faixa in range(4):
        imagem = np.full((altura, largura, 3), 10 * (faixa + 1), dtype=np.uint8)
        cv2.imwrite(str(tmp_path / f"lote_0001_faixa_{faixa}.png"), imagem)

    fonte = icp_display.imagem_fonte(tmp_path, [], arquivo="lote_0001_20m.png")

    assert fonte is not None
    assert fonte.shape[0] == 4 * altura
    assert int(fonte[0, 0, 0]) == 10  # topo = faixa 0 (menor KM)
    assert int(fonte[-1, 0, 0]) == 40
