"""Curvas e correções do ICP — método da concessionária (planilhas ICP Pedágio).

Gerado a partir das fórmulas fechadas das planilhas "Com Fórmula" (Anexo II.1–II.9).
Não editar à mão: os coeficientes são transcritos das fórmulas originais.
"""
# ruff: noqa: E501  (código gerado: expressões longas das curvas)
from __future__ import annotations

from collections.abc import Callable


def _if(cond: bool, a: float, b: float = 0.0) -> float:
    return a if cond else b


def _vd_1_A(u: float) -> float:
    if u <= 0:
        return 0.0
    aa = _if(u < 15, 0.0075 * u ** 3 - 0.4162 * u ** 2 + 8.9154 * u + 0.047, _if(u >= 15, 0.0004 * u ** 3 - 0.0545 * u ** 2 + 2.9207 * u + 32.251, 0))
    return float(_if(aa <= 100, aa, 100))


def _vd_1_M(u: float) -> float:
    if u <= 0:
        return 0.0
    aa = 4 * 10 ** (-5) * u ** 3 - 0.0122 * u ** 2 + 1.6916 * u + 0.086
    return float(_if(aa <= 82.7, aa, 82.7))


def _vd_1_B(u: float) -> float:
    if u <= 0:
        return 0.0
    aa = -0.0029 * u ** 2 + 0.728 * u + 0.3972
    return float(_if(aa <= 45, aa, 45))


def _vd_2_A(u: float) -> float:
    if u <= 0:
        return 0.0
    aa = 0.0001 * u ** 3 - 0.0258 * u ** 2 + 2.3227 * u + 1.2261
    return float(_if(aa <= 78, aa, 78))


def _vd_2_M(u: float) -> float:
    if u <= 0:
        return 0.0
    aa = 7 * 10 ** (-5) * u ** 3 - 0.0187 * u ** 2 + 1.7801 * u + 0.8167
    return float(_if(aa <= 63, aa, 63))


def _vd_2_B(u: float) -> float:
    if u <= 0:
        return 0.0
    aa = 2 * 10 ** (-5) * u ** 3 - 0.006 * u ** 2 + 0.9352 * u + 0.2012
    return float(_if(aa <= 50, aa, 50))


def _vd_3_A(u: float) -> float:
    if u <= 0:
        return 0.0
    aa = _if(u < 25, 0.0038 * u ** 3 - 0.21 * u ** 2 + 4.9542 * u, _if(u >= 25, -0.0044 * u ** 2 + 1.0504 * u + 29.319, 0))
    return float(_if(aa <= 100, aa, 100))


def _vd_3_M(u: float) -> float:
    if u <= 0:
        return 0.0
    aa = 6 * 10 ** (-5) * u ** 3 - 0.0155 * u ** 2 + 1.7012 * u + 0.9597
    return float(_if(aa <= 100, aa, 100))


def _vd_3_B(u: float) -> float:
    if u <= 0:
        return 0.0
    aa = 3 * 10 ** (-5) * u ** 3 - 0.0094 * u ** 2 + 1.1266 * u + 0.022
    return float(_if(aa <= 100, aa, 100))


def _vd_4_A(u: float) -> float:
    if u <= 0:
        return 0.0
    aa = 6 * 10 ** (-5) * u ** 3 - 0.0137 * u ** 2 + 1.6382 * u + 0.6909
    return float(_if(u <= 95, aa, 84.1))


def _vd_4_M(u: float) -> float:
    if u <= 0:
        return 0.0
    aa = -0.0032 * u ** 2 + 0.8482 * u + 0.72
    return float(_if(aa <= 100, aa, 100))


def _vd_4_B(u: float) -> float:
    if u <= 0:
        return 0.0
    aa = -3 * 10 ** (-5) * u ** 3 + 0.0019 * u ** 2 + 0.3939 * u - 0.4098
    return float(_if(u <= 91, aa, 30))


def _vd_6_A(u: float) -> float:
    if u <= 0:
        return 0.0
    aa = 4 * 10 ** (-5) * u ** 3 - 0.0098 * u ** 2 + 0.8973 * u + 0.3476
    return float(_if(aa <= 100, aa, 100))


def _vd_6_M(u: float) -> float:
    if u <= 0:
        return 0.0
    aa = -0.0019 * u ** 2 + 0.3907 * u + 0.2795
    return float(_if(aa <= 20, aa, 20))


def _vd_6_B(u: float) -> float:
    if u <= 0:
        return 0.0
    aa = -0.0002 * u ** 2 + 0.0726 * u - 0.0006
    return float(_if(aa <= 5, aa, 5))


def _vd_7_A(u: float) -> float:
    if u <= 0:
        return 0.0
    aa = _if(u < 20, -0.0333 * u ** 2 + 2.1667 * u, _if(u >= 20, -0.0035 * u ** 2 + 0.8481 * u + 14.805, 0))
    return float(_if(aa <= 65, aa, 65))


def _vd_7_M(u: float) -> float:
    if u <= 0:
        return 0.0
    aa = 2 * 10 ** (-5) * u ** 3 - 0.007 * u ** 2 + 0.8241 * u + 0.512
    return float(_if(aa < 32.9, aa, 37))


def _vd_7_B(u: float) -> float:
    if u <= 0:
        return 0.0
    aa = -0.0024 * u ** 2 + 0.4498 * u + 0.4492
    return float(_if(u <= 90, aa, 21.5))


def _vd_8_A(u: float) -> float:
    if u <= 0:
        return 0.0
    aa = 0.0001 * u ** 3 - 0.0214 * u ** 2 + 1.8113 * u + 0.7712
    return float(_if(aa <= 100, aa, 100))


def _vd_8_M(u: float) -> float:
    if u <= 0:
        return 0.0
    aa = -0.0029 * u ** 2 + 0.7666 * u - 0.2173
    return float(_if(aa <= 100, aa, 100))


def _vd_8_B(u: float) -> float:
    if u <= 0:
        return 0.0
    aa = -0.0017 * u ** 2 + 0.4627 * u - 0.7082
    return float(_if(aa <= 100, aa, 100))


def _vd_9_A(u: float) -> float:
    if u <= 0:
        return 0.0
    aa = 2 * 10 ** (-5) * u ** 3 - 0.0054 * u ** 2 + 0.5884 * u - 0.0119
    return float(_if(aa <= 100, aa, 100))


def _vd_9_M(u: float) -> float:
    if u <= 0:
        return 0.0
    aa = -0.0013 * u ** 2 + 0.2793 * u + 0.0417
    return float(_if(aa <= 100, aa, 100))


def _vd_9_B(u: float) -> float:
    if u <= 0:
        return 0.0
    aa = _if(u <= 15, 0, -0.0005 * u ** 2 + 0.1136 * u - 1.6018)
    return float(_if(aa <= 100, aa, 100))


def _vd_10_U(u: float) -> float:
    if u <= 0:
        return 0.0
    aa = -0.0012 * u ** 2 + 0.221 * u - 0.1081
    return float(_if(aa < 10, aa, 10))


def _vd_11_U(u: float) -> float:
    if u <= 0:
        return 0.0
    aa = -0.0023 * u ** 2 + 0.5949 * u + 0.5711
    return float(_if(aa <= 100, aa, 100))


def _vd_12_A(u: float) -> float:
    if u <= 0:
        return 0.0
    aa = _if(u < 20, -0.0759 * u ** 2 + 3.8391 * u + 0.5519, _if(u >= 20, -0.0069 * u ** 2 + 1.3025 * u + 24.591, 0))
    return float(_if(u <= 90, aa, 86.1))


def _vd_12_M(u: float) -> float:
    if u <= 0:
        return 0.0
    aa = _if(u < 35, 0.001 * u ** 3 - 0.0841 * u ** 2 + 3.0862 * u + 0.1621, _if(u >= 35, -0.0036 * u ** 2 + 0.8102 * u + 26.185, 0))
    return float(_if(aa <= 100, aa, 100))


def _vd_12_B(u: float) -> float:
    if u <= 0:
        return 0.0
    aa = 5 * 10 ** (-5) * u ** 3 - 0.0124 * u ** 2 + 1.2889 * u + 0.624
    return float(_if(aa <= 100, aa, 100))


def _vd_13_A(u: float) -> float:
    if u <= 0:
        return 0.0
    aa = _if(u < 2.5, -4.6667 * u ** 2 + 23.513 * u + 0.1923, _if(u < 20, -0.0891 * u ** 2 + 4.2382 * u + 20.474, _if(u >= 20, -0.008 * u ** 2 + 1.2357 * u + 48.766, 0)))
    return float(_if(u <= 63, aa, 95))


def _vd_13_M(u: float) -> float:
    if u <= 0:
        return 0.0
    aa = -3 * 10 ** (-6) * u ** 4 + 0.0006 * u ** 3 - 0.0469 * u ** 2 + 2.1862 * u + 0.0587
    return float(_if(u <= 53, aa, 50))


def _vd_13_B(u: float) -> float:
    if u <= 0:
        return 0.0
    aa = 9 * 10 ** (-5) * u ** 3 - 0.0141 * u ** 2 + 0.9968 * u + 0.1274
    return float(_if(u <= 60, aa, 30))


def _vd_14_A(u: float) -> float:
    if u <= 0:
        return 0.0
    aa = 7 * 10 ** (-5) * u ** 3 - 0.0156 * u ** 2 + 1.5416 * u + 0.3695
    return float(_if(u < 92.5, aa, 65))


def _vd_14_M(u: float) -> float:
    if u <= 0:
        return 0.0
    aa = _if(u < 30, -0.0155 * u ** 2 + 1.0518 * u + 0.0455, _if(u >= 30, -0.0016 * u ** 2 + 0.3765 * u + 7.8816, 0))
    return float(_if(aa <= 100, aa, 100))


def _vd_14_B(u: float) -> float:
    if u <= 0:
        return 0.0
    aa = -0.0012 * u ** 2 + 0.2228 * u + 0.0702
    return float(_if(aa <= 10, aa, 10))


def _vd_15_U(u: float) -> float:
    if u <= 0:
        return 0.0
    aa = -0.0005 * u ** 2 + 0.1083 * u - 1.2517
    return float(_if(u <= 13, 0, _if(aa < 5, aa, 5)))


def _vd_16_A(u: float) -> float:
    if u <= 0:
        return 0.0
    aa = -0.0035 * u ** 2 + 0.6367 * u + 0.7722
    return float(_if(u <= 90, aa, 30))


def _vd_16_M(u: float) -> float:
    if u <= 0:
        return 0.0
    aa = -0.0025 * u ** 2 + 0.469 * u + 0.2713
    return float(_if(aa <= 100, aa, 100))


def _vd_16_B(u: float) -> float:
    if u <= 0:
        return 0.0
    aa = -0.0007 * u ** 2 + 0.2206 * u - 0.2198
    return float(_if(aa <= 100, aa, 100))


def _vd_17_A(u: float) -> float:
    if u <= 0:
        return 0.0
    aa = -0.002 * u ** 2 + 0.7648 * u + 0.3432
    return float(_if(aa <= 100, aa, 100))


def _vd_17_M(u: float) -> float:
    if u <= 0:
        return 0.0
    aa = -0.0011 * u ** 2 + 0.3644 * u - 0.0272
    return float(_if(aa <= 100, aa, 100))


def _vd_17_B(u: float) -> float:
    if u <= 0:
        return 0.0
    aa = -0.0014 * u ** 2 + 0.2567 * u - 0.0288
    return float(_if(u < 90, aa, 12))


def _vd_18_A(u: float) -> float:
    if u <= 0:
        return 0.0
    aa = _if(u < 30, -0.0144 * u ** 2 + 1.7358 * u + 0.3249, _if(u >= 30, -0.0031 * u ** 2 + 1.0378 * u + 11.076, 0))
    return float(_if(aa <= 100, aa, 100))


def _vd_18_M(u: float) -> float:
    if u <= 0:
        return 0.0
    aa = -0.0032 * u ** 2 + 0.8572 * u + 0.3274
    return float(_if(aa <= 100, aa, 100))


def _vd_18_B(u: float) -> float:
    if u <= 0:
        return 0.0
    aa = -4 * 10 ** (-5) * u ** 3 + 0.0037 * u ** 2 + 0.335 * u - 0.1965
    return float(_if(u <= 30, aa, 30))


def _ac(vd: float) -> float:
    return float(_if(vd < 100, _if(vd < 41, -0.0025 * vd ** 2 + 1.075 * vd + 1, _if(vd >= 41, 0.0009 * vd ** 2 + 0.869 * vd + 3.9857)), 100))


def _vdc_q1(vdt: float) -> float:
    return float(_if(vdt < 100, _if(vdt < 41, -0.0025 * vdt ** 2 + 1.075 * vdt + 1, _if(vdt >= 41, 0.0009 * vdt ** 2 + 0.869 * vdt + 3.9857)), 100))


def _vdc_q2(vdt: float) -> float:
    return float(_if(vdt < 157.4, 4 * 10 ** (-6) * vdt ** 3 - 0.0022 * vdt ** 2 + 0.8876 * vdt - 0.845, 100))


def _vdc_q3(vdt: float) -> float:
    return float(_if(vdt < 177.4, -0.0008 * vdt ** 2 + 0.7176 * vdt - 2.1473, 100))


def _vdc_q4(vdt: float) -> float:
    return float(_if(vdt < 195.85, -0.0007 * vdt ** 2 + 0.6577 * vdt - 3.0473, 99))


def _vdc_q5(vdt: float) -> float:
    return float(_if(vdt < 198, 2 * 10 ** (-6) * vdt ** 3 - 0.0013 * vdt ** 2 + 0.6893 * vdt - 7.1375, 94))


def _vdc_q6(vdt: float) -> float:
    return float(_if(vdt < 195.54, -0.0005 * vdt ** 2 + 0.5609 * vdt - 5.6713, 85))


CURVAS: dict[tuple[int, str], Callable[[float], float]] = {
    (1, 'A'): _vd_1_A,
    (1, 'M'): _vd_1_M,
    (1, 'B'): _vd_1_B,
    (2, 'A'): _vd_2_A,
    (2, 'M'): _vd_2_M,
    (2, 'B'): _vd_2_B,
    (3, 'A'): _vd_3_A,
    (3, 'M'): _vd_3_M,
    (3, 'B'): _vd_3_B,
    (4, 'A'): _vd_4_A,
    (4, 'M'): _vd_4_M,
    (4, 'B'): _vd_4_B,
    (6, 'A'): _vd_6_A,
    (6, 'M'): _vd_6_M,
    (6, 'B'): _vd_6_B,
    (7, 'A'): _vd_7_A,
    (7, 'M'): _vd_7_M,
    (7, 'B'): _vd_7_B,
    (8, 'A'): _vd_8_A,
    (8, 'M'): _vd_8_M,
    (8, 'B'): _vd_8_B,
    (9, 'A'): _vd_9_A,
    (9, 'M'): _vd_9_M,
    (9, 'B'): _vd_9_B,
    (10, 'U'): _vd_10_U,
    (11, 'U'): _vd_11_U,
    (12, 'A'): _vd_12_A,
    (12, 'M'): _vd_12_M,
    (12, 'B'): _vd_12_B,
    (13, 'A'): _vd_13_A,
    (13, 'M'): _vd_13_M,
    (13, 'B'): _vd_13_B,
    (14, 'A'): _vd_14_A,
    (14, 'M'): _vd_14_M,
    (14, 'B'): _vd_14_B,
    (15, 'U'): _vd_15_U,
    (16, 'A'): _vd_16_A,
    (16, 'M'): _vd_16_M,
    (16, 'B'): _vd_16_B,
    (17, 'A'): _vd_17_A,
    (17, 'M'): _vd_17_M,
    (17, 'B'): _vd_17_B,
    (18, 'A'): _vd_18_A,
    (18, 'M'): _vd_18_M,
    (18, 'B'): _vd_18_B,
}

VDC_CURVAS: dict[int, Callable[[float], float]] = {1: _vdc_q1, 2: _vdc_q2, 3: _vdc_q3, 4: _vdc_q4, 5: _vdc_q5, 6: _vdc_q6}

