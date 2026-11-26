from __future__ import annotations

import math

from src.services import icp_blocos

MM_POR_PX = 0.9765625  # 1000 / 1024 (display 1024 px/m)
PX_POR_M = 1024.0
KM0 = 12.345678


def test_borda_do_bloco_coincide_com_a_junta_detectada() -> None:
    """A borda do bloco deve cair exatamente sobre a junta que a fechou.

    Regressão: o km do bloco era arredondado a 3 casas (grade de 1 m), então a
    borda deslocava até 0,5 m da linha detectada no overlay do ICP.
    """
    ys = [i * 5.0 * PX_POR_M for i in range(6)]  # juntas a cada 5 m
    transversais = [
        {
            "y": y,
            "angulo_graus": 0.0,
            "x0": 0.0,
            "x1": 4095.0,
            "origem": "detectada",
            "km": round(KM0 + y * MM_POR_PX / 1_000_000.0, 6),
        }
        for y in ys
    ]
    largura = 4.0 * PX_POR_M
    retas = (
        icp_blocos.reta_vertical(200.0, ys[-1]),
        icp_blocos.reta_vertical(200.0 + largura, ys[-1]),
    )

    resultado = icp_blocos.montar_blocos(
        transversais,
        km0=KM0,
        mm_por_px_y=MM_POR_PX,
        retas_por_bloco={0: retas},
        blocos_ranges=[(0, ys[0], ys[-1])],
        comprimento_max_m=6.0,
        comprimento_min_m=3.0,
    )

    juntas = resultado["transversais"]
    blocos = resultado["blocos"]
    assert blocos
    for bloco, junta_inicio, junta_fim in zip(blocos, juntas, juntas[1:]):
        assert math.isclose(bloco["km_inicio"], junta_inicio["km"], abs_tol=1e-4)
        assert math.isclose(bloco["km_fim"], junta_fim["km"], abs_tol=1e-4)
