"""Placas de pavimento rígido e amostras de ICP (DNIT 062/2004-PRO).

Esta é a etapa de análise do rígido que aproveita o pacote ``juntas-detector``
(``github.com/victormedeiros96/juntas-detector``, v0.2.0). O pacote não embute o
modelo de segmentação: ele recebe a calibração das juntas (período ``P`` e fase)
e materializa a **rede periódica** de juntas transversais, a grade de placas e
as ROIs. Aqui usamos essa geometria sem depender de GPU:

* ``periodo.estimar`` / ``Calibracao`` — calibração a partir de juntas conhecidas;
* ``placas.posicoes_rede`` — posições ``fase + k*P`` no strip da viagem;
* ``placas.poligonos`` — retângulos (ROIs) de cada placa;
* ``placas.distancia_transversais`` — comprimento da placa em metros.

Convenções de coordenadas usadas pelo sistema:

* cada faixa de 5 m é uma imagem de display de 4096×5120 px (1024 px/m);
* o topo da faixa (``y = 0``) é o menor KM e o KM cresce para baixo;
* o strip global da viagem é a faixa de referência ordenada por KM, com origem
  no menor ``km_inicio`` das faixas lidas dos ``lote_*_meta.json``.

A saída é o *inventário de placas*: uma placa por intervalo entre juntas
consecutivas, com id, KM, faixa, bloco, polígono em pixels do strip, polígono em
metros/KM e os pontos projetados nas imagens originais das faixas. O inventário é
agrupado em amostras de até 20 placas e persistido em
``dados/<viagem>/icp_placas.json``.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import numpy as np
from juntas_detector import periodo as jd_periodo
from juntas_detector import placas as jd_placas
from juntas_detector.longitudinais import Reta

NORMA_ICP = "DNIT 062/2004-PRO"

# Escala do display do LineScan: 4096 px = 4 m, 5120 px = 5 m (1024 px/m).
LARGURA_FAIXA_PX = 4096
ALTURA_FAIXA_PX = 5120
PX_POR_METRO = 1024.0
MM_POR_PX = 1000.0 / PX_POR_METRO  # 0.9765625 mm/px (isotrópico)

TAMANHO_AMOSTRA = 20
COMPRIMENTO_MAX_M = 9.0
LARGURA_PLACA_M = 3.6


@dataclass(frozen=True)
class FaixaInfo:
    """Faixa de 5 m do levantamento, ordenada por KM."""

    arquivo: str
    bloco_indice: int
    faixa_index: int
    km_inicio: float
    km_fim: float
    altura_px: int = ALTURA_FAIXA_PX


def listar_faixas(base: Path) -> list[FaixaInfo]:
    """Lê as faixas dos ``lote_*_meta.json`` da viagem, ordenadas por KM."""
    faixas: list[FaixaInfo] = []
    for meta_path in sorted(Path(base).glob("lote_*_meta.json")):
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        bloco = meta.get("bloco", {})
        bloco_indice = int(bloco.get("indice", 0))
        for faixa in meta.get("faixas", []):
            km_inicio = faixa.get("km_inicio")
            km_fim = faixa.get("km_fim")
            if km_inicio is None or km_fim is None or km_fim <= km_inicio:
                continue
            faixas.append(
                FaixaInfo(
                    arquivo=str(faixa["arquivo"]),
                    bloco_indice=bloco_indice,
                    faixa_index=int(faixa.get("faixa_index", 0)),
                    km_inicio=float(km_inicio),
                    km_fim=float(km_fim),
                    altura_px=int(faixa.get("altura_px") or ALTURA_FAIXA_PX),
                )
            )
    faixas.sort(key=lambda f: (f.km_inicio, f.bloco_indice, f.faixa_index))
    return faixas


def _reta_vertical(x: float, altura_px: float) -> Reta:
    return Reta(
        a=0.0,
        b=float(x),
        n=1,
        comp=float(altura_px),
        y0=0.0,
        y1=float(altura_px),
        cobertura=1.0,
        residuo=0.0,
        obs=[(0.0, float(altura_px))],
    )


def _km0(faixas: list[FaixaInfo]) -> float:
    return min(f.km_inicio for f in faixas)


def _intervalo_coberto(faixas: list[FaixaInfo], inicio: float, fim: float) -> bool:
    """Confere se ``[inicio, fim]`` está inteiramente coberto pelas faixas.

    Faixas amostradas (blocos pulados) deixam lacunas; uma placa que caia numa
    lacuna não tem imagem original e é descartada.
    """
    cursor = inicio
    for faixa in faixas:
        if faixa.km_fim <= cursor:
            continue
        if faixa.km_inicio > cursor + 1e-9:
            return False
        cursor = max(cursor, faixa.km_fim)
        if cursor >= fim - 1e-9:
            return True
    return False


def _faixa_em_km(faixas: list[FaixaInfo], km: float) -> FaixaInfo | None:
    for faixa in faixas:
        if faixa.km_inicio <= km < faixa.km_fim:
            return faixa
    if faixas and math.isclose(km, faixas[-1].km_fim, abs_tol=1e-9):
        return faixas[-1]
    return None


def _faixas_do_intervalo(
    faixas: list[FaixaInfo], inicio: float, fim: float
) -> list[FaixaInfo]:
    return [f for f in faixas if f.km_fim > inicio + 1e-9 and f.km_inicio < fim - 1e-9]


def _y_na_faixa(faixa: FaixaInfo, km: float) -> float:
    fracao = (km - faixa.km_inicio) / (faixa.km_fim - faixa.km_inicio)
    return fracao * faixa.altura_px


def _indice_faixa_por_km(faixas: list[FaixaInfo], km: float) -> int:
    """Índice da faixa que cobre ``km``; fora do intervalo usa a mais próxima.

    O KM do polígono é arredondado a milímetro e o último ponto pode cair
    exatamente no ``km_fim`` da última faixa (fora do intervalo meio-aberto).
    Sem o ajuste ele cairia na faixa 0, extrapolando ``y`` para fora do strip.
    """
    for i, faixa in enumerate(faixas):
        if faixa.km_inicio <= km < faixa.km_fim:
            return i
    return 0 if km < faixas[0].km_inicio else len(faixas) - 1


def calibrar_por_juntas(
    juntas_km: Iterable[float],
    *,
    km0: float | None = None,
    mm_por_px_y: float = MM_POR_PX,
    faixa_m: tuple[float, float] = (3.5, 6.5),
) -> dict[str, Any]:
    """Calibra período/fase a partir de juntas transversais em KM absoluto.

    Usa a estimativa por rede periódica do ``juntas-detector`` — robusta a
    juntas perdidas e duplicadas. A fase devolvida é a distância em metros da
    primeira junta até ``km0`` (ou até a menor junta, quando ``km0`` é omitido).
    """
    valores = sorted(float(km) for km in juntas_km)
    if len(valores) < 3:
        raise ValueError("São necessárias ao menos 3 juntas para calibrar o período.")
    origem = float(km0) if km0 is not None else valores[0]
    ys_px = [(km - origem) * 1_000_000.0 / mm_por_px_y for km in valores]
    estimativa = jd_periodo.estimar(
        ys_px, mm_por_px_y=mm_por_px_y, faixa_m=faixa_m
    )
    fase_m = float(estimativa["fase_px"]) * mm_por_px_y / 1000.0
    return {
        "origem_km": round(origem, 6),
        "periodo_m": round(float(estimativa["periodo_m"]), 6),
        "fase_m": round(fase_m, 6),
        "R": round(float(estimativa["R"]), 6),
        "n_juntas": len(valores),
        "n_na_rede": int(estimativa["n_na_rede"]),
        "mm_por_px_x": mm_por_px_y,
        "mm_por_px_y": mm_por_px_y,
        "norma": NORMA_ICP,
    }


def gerar_placas(
    faixas: list[FaixaInfo],
    *,
    periodo_m: float,
    fase_m: float = 0.0,
    largura_placa_m: float = LARGURA_PLACA_M,
    comprimento_max_m: float = COMPRIMENTO_MAX_M,
    angulo_graus: float = 0.0,
    mm_por_px_x: float = MM_POR_PX,
    mm_por_px_y: float = MM_POR_PX,
    largura_px: int = LARGURA_FAIXA_PX,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Gera as placas do trecho e devolve ``(placas, descartadas)``.

    A rede de juntas é calculada no strip global (uma faixa de referência
    contínua) e cada placa é projetada de volta nas imagens originais das
    faixas. Placas sem cobertura de faixa (lacuna de amostragem) ou mais longas
    que ``comprimento_max_m`` vão para ``descartadas``.
    """
    if not faixas:
        raise ValueError("Nenhuma faixa disponível para gerar as placas.")
    if periodo_m <= 0:
        raise ValueError("O período da placa deve ser positivo.")

    faixas_ordenadas = sorted(faixas, key=lambda f: (f.km_inicio, f.faixa_index))
    km0 = _km0(faixas_ordenadas)
    km_fim_trecho = max(f.km_fim for f in faixas_ordenadas)

    periodo_px = periodo_m * 1000.0 / mm_por_px_y
    fase_px = fase_m * 1000.0 / mm_por_px_y
    altura_strip = (km_fim_trecho - km0) * 1_000_000.0 / mm_por_px_y

    yref = jd_placas.posicoes_rede(
        fase_px, periodo_px, int(math.ceil(altura_strip)), margem=0
    )
    if len(yref) < 2:
        raise ValueError(
            "A malha de juntas não produziu duas juntas no trecho; "
            "confira período, fase e a extensão das faixas."
        )

    meia_largura_px = largura_placa_m * 1000.0 / mm_por_px_x / 2.0
    x_centro = largura_px / 2.0
    reta_esq = _reta_vertical(x_centro - meia_largura_px, altura_strip)
    reta_dir = _reta_vertical(x_centro + meia_largura_px, altura_strip)
    angulos = [float(angulo_graus)] * len(yref)

    placas: list[dict[str, Any]] = []
    descartadas: list[dict[str, Any]] = []

    for ya, yb in zip(yref, yref[1:]):
        km_ini = km0 + ya * mm_por_px_y / 1_000_000.0
        km_fim = km0 + yb * mm_por_px_y / 1_000_000.0
        comprimento = jd_placas.distancia_transversais(
            ya, angulo_graus, yb, angulo_graus, mm_x=mm_por_px_x, mm_y=mm_por_px_y
        )
        comprimento = round(float(comprimento), 3)

        base = {"km_inicio": round(km_ini, 3), "km_fim": round(km_fim, 3)}
        if not _intervalo_coberto(faixas_ordenadas, km_ini, km_fim):
            descartadas.append({**base, "motivo": "sem cobertura de faixa"})
            continue
        if comprimento <= 0 or comprimento > comprimento_max_m:
            descartadas.append(
                {**base, "comprimento_m": comprimento, "motivo": "comprimento fora do limite"}
            )
            continue

        saida = jd_placas.poligonos(
            [ya, yb], [angulos[0], angulos[0]], reta_esq, reta_dir, largura_min=0.01
        )
        if not saida:
            descartadas.append({**base, "motivo": "polígono inválido"})
            continue

        poligono_px = [
            [round(float(x), 2), round(float(y), 2)] for x, y in saida[0]["poligono"]
        ]
        faixas_da_placa = _faixas_do_intervalo(faixas_ordenadas, km_ini, km_fim)
        faixa_principal = _faixa_em_km(faixas_ordenadas, km_ini) or faixas_da_placa[0]

        pontos_faixa = []
        poligono_m = []
        for x_px, y_px in saida[0]["poligono"]:
            km = km0 + float(y_px) * mm_por_px_y / 1_000_000.0
            faixa = _faixa_em_km(faixas_ordenadas, km) or faixa_principal
            pontos_faixa.append(
                {
                    "arquivo": faixa.arquivo,
                    "faixa_index": faixa.faixa_index,
                    "x": round(float(x_px), 2),
                    "y": round(float(_y_na_faixa(faixa, km)), 2),
                }
            )
            poligono_m.append(
                {
                    "x_m": round(float(x_px) * mm_por_px_x / 1000.0, 3),
                    "km": round(float(km), 3),
                }
            )

        placas.append(
            {
                "id": f"P{len(placas) + 1:04d}",
                "km": round((km_ini + km_fim) / 2.0, 3),
                "km_inicio": round(km_ini, 3),
                "km_fim": round(km_fim, 3),
                "comprimento_m": comprimento,
                "largura_m": round(largura_placa_m, 3),
                "bloco_indice": faixa_principal.bloco_indice,
                "faixa": faixa_principal.arquivo,
                "faixa_index": faixa_principal.faixa_index,
                "faixas": [f.arquivo for f in faixas_da_placa],
                "poligono": poligono_px,
                "poligono_m": poligono_m,
                "pontos_faixa": pontos_faixa,
                "amostra_id": None,
                "roi": None,
            }
        )

    return placas, descartadas


def gerar_placas_por_juntas(
    faixas: list[FaixaInfo],
    juntas: Iterable[dict[str, Any]],
    *,
    comprimento_max_m: float = COMPRIMENTO_MAX_M,
    mm_por_px_x: float = MM_POR_PX,
    mm_por_px_y: float = MM_POR_PX,
    largura_px: int = LARGURA_FAIXA_PX,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Gera as placas a partir das juntas transversais **detectadas na imagem**.

    Diferente de :func:`gerar_placas` — que materializa uma rede periódica a
    partir de ``periodo_m``/``largura_placa_m`` — aqui cada placa é o intervalo
    entre duas juntas consecutivas realmente segmentadas pelo modelo
    (``juntas-detector`` + ``trinca-detector``). O comprimento de cada placa é a
    distância medida entre juntas vizinhas, então ele pode variar dentro da mesma
    viagem sem que se declare um tamanho fixo.

    Cada item de ``juntas`` descreve uma junta na imagem: ``km`` (posição no eixo
    da via no centro da faixa), ``angulo_graus`` (inclinação da transversal) e,
    opcionalmente, ``x0``/``x1`` (extensão em px; padrão: largura inteira da
    faixa, que é a geometria real da imagem).

    Devolve ``(placas, descartadas)`` no mesmo formato de :func:`gerar_placas`.
    """
    if not faixas:
        raise ValueError("Nenhuma faixa disponível para gerar as placas.")

    faixas_ordenadas = sorted(faixas, key=lambda f: (f.km_inicio, f.faixa_index))
    km0 = _km0(faixas_ordenadas)
    x_ref = float(largura_px) / 2.0
    largura_max_px = float(largura_px - 1)

    def _y_no_strip(km: float) -> float:
        return (float(km) - km0) * 1_000_000.0 / mm_por_px_y

    def _y_da_junta(junta: dict[str, Any], x: float) -> float:
        y_centro = _y_no_strip(junta["km"])
        angulo = float(junta.get("angulo_graus") or 0.0)
        return y_centro + (x - x_ref) * math.tan(math.radians(angulo))

    def _x_limites(junta: dict[str, Any]) -> tuple[float, float]:
        x0 = float(junta.get("x0", 0.0))
        x1 = float(junta.get("x1", largura_max_px))
        return max(0.0, min(x0, x1)), min(largura_max_px, max(x0, x1))

    ordenadas = sorted(juntas, key=lambda j: float(j["km"]))
    placas: list[dict[str, Any]] = []
    descartadas: list[dict[str, Any]] = []

    for ja, jb in zip(ordenadas, ordenadas[1:]):
        km_ini = min(float(ja["km"]), float(jb["km"]))
        km_fim = max(float(ja["km"]), float(jb["km"]))
        base = {"km_inicio": round(km_ini, 3), "km_fim": round(km_fim, 3)}

        if not _intervalo_coberto(faixas_ordenadas, km_ini, km_fim):
            descartadas.append({**base, "motivo": "sem cobertura de faixa"})
            continue

        xa0, xa1 = _x_limites(ja)
        xb0, xb1 = _x_limites(jb)
        x_esq = max(xa0, xb0)
        x_dir = min(xa1, xb1)
        if x_dir - x_esq <= 0:
            descartadas.append({**base, "motivo": "juntas sem sobreposição lateral"})
            continue

        angulo_a = float(ja.get("angulo_graus") or 0.0)
        angulo_b = float(jb.get("angulo_graus") or 0.0)
        comprimento = jd_placas.distancia_transversais(
            _y_no_strip(km_ini), angulo_a, _y_no_strip(km_fim), angulo_b,
            mm_x=mm_por_px_x, mm_y=mm_por_px_y,
        )
        comprimento = round(float(comprimento), 3)
        if comprimento <= 0 or comprimento > comprimento_max_m:
            descartadas.append(
                {**base, "comprimento_m": comprimento, "motivo": "comprimento fora do limite"}
            )
            continue

        poligono_px = [
            [round(x_esq, 2), round(_y_da_junta(ja, x_esq), 2)],
            [round(x_dir, 2), round(_y_da_junta(ja, x_dir), 2)],
            [round(x_dir, 2), round(_y_da_junta(jb, x_dir), 2)],
            [round(x_esq, 2), round(_y_da_junta(jb, x_esq), 2)],
        ]

        faixas_da_placa = _faixas_do_intervalo(faixas_ordenadas, km_ini, km_fim)
        faixa_principal = _faixa_em_km(faixas_ordenadas, km_ini) or faixas_da_placa[0]

        pontos_faixa = []
        poligono_m = []
        for x_px, y_px in poligono_px:
            km = km0 + float(y_px) * mm_por_px_y / 1_000_000.0
            faixa = _faixa_em_km(faixas_ordenadas, km) or faixa_principal
            pontos_faixa.append(
                {
                    "arquivo": faixa.arquivo,
                    "faixa_index": faixa.faixa_index,
                    "x": round(float(x_px), 2),
                    "y": round(float(_y_na_faixa(faixa, km)), 2),
                }
            )
            poligono_m.append(
                {
                    "x_m": round(float(x_px) * mm_por_px_x / 1000.0, 3),
                    "km": round(float(km), 3),
                }
            )

        placas.append(
            {
                "id": f"P{len(placas) + 1:04d}",
                "km": round((km_ini + km_fim) / 2.0, 3),
                "km_inicio": round(km_ini, 3),
                "km_fim": round(km_fim, 3),
                "comprimento_m": comprimento,
                "largura_m": round((x_dir - x_esq) * mm_por_px_x / 1000.0, 3),
                "bloco_indice": faixa_principal.bloco_indice,
                "faixa": faixa_principal.arquivo,
                "faixa_index": faixa_principal.faixa_index,
                "faixas": [f.arquivo for f in faixas_da_placa],
                "poligono": poligono_px,
                "poligono_m": poligono_m,
                "pontos_faixa": pontos_faixa,
                "amostra_id": None,
                "roi": None,
            }
        )

    return placas, descartadas


def agrupar_amostras(
    placas: list[dict[str, Any]], *, tamanho: int = TAMANHO_AMOSTRA
) -> list[dict[str, Any]]:
    """Agrupa as placas em amostras consecutivas de até ``tamanho`` placas.

    Atribui ``amostra_id`` em cada placa (mutação no lugar) e devolve as amostras.
    """
    if tamanho <= 0:
        raise ValueError("O tamanho da amostra deve ser positivo.")

    ordenadas = sorted(placas, key=lambda p: (p["km_inicio"], p["id"]))
    amostras: list[dict[str, Any]] = []
    for indice, inicio in enumerate(range(0, len(ordenadas), tamanho), start=1):
        grupo = ordenadas[inicio : inicio + tamanho]
        amostra_id = f"A{indice:03d}"
        for placa in grupo:
            placa["amostra_id"] = amostra_id
        amostras.append(
            {
                "id": amostra_id,
                "indice": indice,
                "km_inicio": grupo[0]["km_inicio"],
                "km_fim": grupo[-1]["km_fim"],
                "comprimento_m": round(grupo[-1]["km_fim"] - grupo[0]["km_inicio"], 3),
                "n_placas": len(grupo),
                "placas": [p["id"] for p in grupo],
            }
        )
    return amostras


def _placa_de_bloco(
    bloco: dict[str, Any],
    faixas_ordenadas: list[FaixaInfo],
    *,
    km0: float,
    indice: int,
    mm_por_px_x: float,
    mm_por_px_y: float,
) -> dict[str, Any]:
    """Converte um bloco fechado (polígono no strip) no formato de placa do inventário."""
    km_ini = float(bloco["km_inicio"])
    km_fim = float(bloco["km_fim"])
    faixas_da_placa = _faixas_do_intervalo(faixas_ordenadas, km_ini, km_fim)
    faixa_principal = _faixa_em_km(faixas_ordenadas, km_ini) or faixas_da_placa[0]

    poligono_px: list[list[float]] = []
    pontos_faixa = []
    poligono_m = []
    for x_px, y_px in bloco["poligono"]:
        km = km0 + float(y_px) * mm_por_px_y / 1_000_000.0
        faixa = _faixa_em_km(faixas_ordenadas, km) or faixa_principal
        poligono_px.append([round(float(x_px), 2), round(float(y_px), 2)])
        pontos_faixa.append(
            {
                "arquivo": faixa.arquivo,
                "faixa_index": faixa.faixa_index,
                "x": round(float(x_px), 2),
                "y": round(float(_y_na_faixa(faixa, km)), 2),
            }
        )
        poligono_m.append(
            {
                "x_m": round(float(x_px) * mm_por_px_x / 1000.0, 3),
                "km": round(float(km), 3),
            }
        )

    return {
        "id": f"P{indice:04d}",
        "km": round((km_ini + km_fim) / 2.0, 3),
        "km_inicio": round(km_ini, 3),
        "km_fim": round(km_fim, 3),
        "comprimento_m": round(float(bloco["comprimento_m"]), 3),
        "largura_m": round(float(bloco["largura_m"]), 3),
        "bloco_indice": bloco.get("bloco_indice") or faixa_principal.bloco_indice,
        "faixa": faixa_principal.arquivo,
        "faixa_index": faixa_principal.faixa_index,
        "faixas": [f.arquivo for f in faixas_da_placa],
        "poligono": poligono_px,
        "poligono_m": poligono_m,
        "pontos_faixa": pontos_faixa,
        "amostra_id": None,
        "roi": None,
        "fechado": True,
        "origem_juntas": bloco.get("origem_juntas", "detectada"),
        "origem_largura": bloco.get("origem_largura", "detectada"),
    }


def montar_inventario(
    faixas: list[FaixaInfo],
    *,
    periodo_m: float | None = None,
    fase_m: float = 0.0,
    largura_placa_m: float = LARGURA_PLACA_M,
    comprimento_max_m: float = COMPRIMENTO_MAX_M,
    tamanho_amostra: int = TAMANHO_AMOSTRA,
    angulo_graus: float = 0.0,
    mm_por_px_x: float = MM_POR_PX,
    mm_por_px_y: float = MM_POR_PX,
    calibracao: dict[str, Any] | None = None,
    juntas: Iterable[dict[str, Any]] | None = None,
    estrutura: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Gera placas + amostras e devolve o inventário pronto para persistir.

    Três origens de geometria:

    * ``estrutura`` — saída de ``icp_juntas.detectar_estrutura``: transversais +
      longitudinais detectadas na imagem concatenada. Cada bloco é o cruzamento
      das duas famílias, com linhas virtuais nos vãos múltiplos do tamanho médio.
      É o caminho preferido do ICP (RDT1-33).
    * ``juntas`` — somente as transversais **detectadas na imagem**; cada placa é
      o intervalo entre duas juntas consecutivas (sem as longitudinais reais).
    * ``periodo_m``/``fase_m`` — rede periódica (calibração manual ou por
      ``juntas_km``), mantida como fallback.
    """
    from src.services import icp_blocos

    blocos_sem_mascara: list[dict[str, Any]] = []
    avisos: list[str] = []
    descartadas: list[dict[str, Any]] = []

    if estrutura is not None:
        faixas_ordenadas = sorted(faixas, key=lambda f: (f.km_inicio, f.faixa_index))
        saida = icp_blocos.montar_blocos(
            estrutura.get("transversais") or [],
            km0=float(estrutura["km0"]),
            mm_por_px_y=mm_por_px_y,
            mm_por_px_x=mm_por_px_x,
            retas_por_bloco=estrutura.get("retas_por_bloco"),
            blocos_ranges=estrutura.get("blocos_ranges"),
            comprimento_max_m=comprimento_max_m,
            periodo_m=periodo_m,
            fase_m=fase_m,
            largura_px=float(LARGURA_FAIXA_PX),
        )
        placas = [
            _placa_de_bloco(
                bloco,
                faixas_ordenadas,
                km0=float(estrutura["km0"]),
                indice=indice,
                mm_por_px_x=mm_por_px_x,
                mm_por_px_y=mm_por_px_y,
            )
            for indice, bloco in enumerate(saida["blocos"], start=1)
        ]
        blocos_sem_mascara = saida["blocos_sem_mascara"]
        avisos = list(saida.get("avisos") or [])
        n_virtuais = sum(
            1 for t in saida["transversais"] if t.get("origem") == "virtual"
        )
        if calibracao is None:
            calibracao = {
                "fonte": "detector_longitudinais",
                "mm_por_px_x": mm_por_px_x,
                "mm_por_px_y": mm_por_px_y,
                "norma": NORMA_ICP,
            }
        # A geometria usada nos blocos é a que passou pelo filtro/rede periódica
        # (RDT1-37); sem isso a calibração continuaria mostrando as detecções
        # brutas, que não são as juntas efetivamente aplicadas.
        calibracao.setdefault("fonte", "detector_longitudinais")
        calibracao["juntas_geometria"] = saida["transversais"]
        calibracao["n_juntas"] = len(saida["transversais"])
        calibracao["n_juntas_virtuais"] = n_virtuais
        calibracao["n_juntas_fora_rede"] = int(
            saida.get("n_detectadas_fora_rede") or 0
        )
        calibracao["n_blocos_sem_mascara"] = len(blocos_sem_mascara)
        calibracao["tamanho_medio_m"] = saida.get("tamanho_medio_m")
        if not placas:
            avisos.append(
                "Nenhum bloco fechado pelo cruzamento transversais × longitudinais; "
                "confira a detecção de juntas e o par de longitudinais."
            )
    elif (juntas_detectadas := list(juntas or [])):
        placas, descartadas = gerar_placas_por_juntas(
            faixas,
            juntas_detectadas,
            comprimento_max_m=comprimento_max_m,
            mm_por_px_x=mm_por_px_x,
            mm_por_px_y=mm_por_px_y,
        )
        calibracao = calibracao or {
            "fonte": "juntas_detectadas",
            "n_juntas": len(juntas_detectadas),
            "mm_por_px_x": mm_por_px_x,
            "mm_por_px_y": mm_por_px_y,
            "norma": NORMA_ICP,
        }
    else:
        if periodo_m is None or periodo_m <= 0:
            raise ValueError(
                "Informe 'juntas' (detectadas na imagem) ou 'periodo_m' (rede periódica)."
            )
        placas, descartadas = gerar_placas(
            faixas,
            periodo_m=periodo_m,
            fase_m=fase_m,
            largura_placa_m=largura_placa_m,
            comprimento_max_m=comprimento_max_m,
            angulo_graus=angulo_graus,
            mm_por_px_x=mm_por_px_x,
            mm_por_px_y=mm_por_px_y,
        )
        calibracao = calibracao or {
            "periodo_m": round(periodo_m, 6),
            "fase_m": round(fase_m, 6),
            "largura_placa_m": largura_placa_m,
            "mm_por_px_x": mm_por_px_x,
            "mm_por_px_y": mm_por_px_y,
            "norma": NORMA_ICP,
        }

    amostras = agrupar_amostras(placas, tamanho=tamanho_amostra)
    return {
        "norma": NORMA_ICP,
        "gerado_em": datetime.now(timezone.utc).isoformat(),
        "calibracao": calibracao,
        "trecho": {
            "km_inicio": round(min(f.km_inicio for f in faixas), 3),
            "km_fim": round(max(f.km_fim for f in faixas), 3),
            "n_faixas": len(faixas),
        },
        "amostras": amostras,
        "placas": placas,
        "descartadas": descartadas,
        "blocos_sem_mascara": blocos_sem_mascara,
        "avisos": avisos,
    }


def salvar_inventario(base: Path, inventario: dict[str, Any]) -> Path:
    destino = Path(base) / "icp_placas.json"
    destino.write_text(
        json.dumps(inventario, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return destino


def carregar_inventario(base: Path) -> dict[str, Any] | None:
    caminho = Path(base) / "icp_placas.json"
    if not caminho.is_file():
        return None
    return json.loads(caminho.read_text(encoding="utf-8"))


def faixas_por_arquivo(faixas: Iterable[FaixaInfo]) -> dict[str, FaixaInfo]:
    return {faixa.arquivo: faixa for faixa in faixas}


def salvar_rois(
    base: Path,
    faixas: list[FaixaInfo],
    placas: list[dict[str, Any]],
    *,
    destino: Path | None = None,
    prefixo: str = "icp",
    folga: int = 12,
    mm_por_px_x: float = MM_POR_PX,
    mm_por_px_y: float = MM_POR_PX,
) -> int:
    """Recorta e grava um PNG por placa, quando as imagens das faixas existem.

    Retorna quantas ROIs foram gravadas. O recorte usa ``placas.recorta_roi`` do
    ``juntas-detector`` e a origem é registrada em ``placa['roi']``.
    """
    import cv2

    if not placas:
        return 0
    base = Path(base)
    destino = Path(destino) if destino else base / "icp_rois"
    destino.mkdir(parents=True, exist_ok=True)
    por_arquivo = faixas_por_arquivo(faixas)
    gravadas = 0

    for placa in placas:
        arquivos = placa.get("faixas") or []
        faixas_placa = [por_arquivo[a] for a in arquivos if a in por_arquivo]
        if len(faixas_placa) != len(arquivos) or not faixas_placa:
            continue

        alturas = [faixa.altura_px for faixa in faixas_placa]
        deslocamentos = np.cumsum([0] + alturas[:-1])
        poligono: list[list[float]] = []
        for ponto in placa.get("poligono_m") or []:
            km = float(ponto["km"])
            indice = _indice_faixa_por_km(faixas_placa, km)
            faixa = faixas_placa[indice]
            topo = float(deslocamentos[indice])
            y = topo + _y_na_faixa(faixa, km)
            # Mantém o ponto dentro da faixa (o KM pode vir do arredondamento).
            if faixa.altura_px > 0:
                y = min(max(y, topo), topo + faixa.altura_px - 1)
            x = float(ponto["x_m"]) * 1000.0 / mm_por_px_x
            poligono.append([x, y])

        if len(poligono) < 3:
            continue

        # Lê só a região vertical da placa em cada faixa, evitando montar o
        # strip inteiro do bloco em memória.
        total = int(deslocamentos[-1]) + alturas[-1]
        ys = [ponto[1] for ponto in poligono]
        y0 = max(0, int(min(ys)) - folga)
        y1 = min(total, int(max(ys)) + folga + 1)
        blocos: list[np.ndarray] = []
        for faixa, deslocamento, altura in zip(faixas_placa, deslocamentos, alturas):
            inicio = max(int(deslocamento), y0)
            fim = min(int(deslocamento) + altura, y1)
            if fim <= inicio:
                continue
            caminho = base / faixa.arquivo
            if not caminho.is_file():
                blocos = []
                break
            imagem = cv2.imread(str(caminho))
            if imagem is None:
                blocos = []
                break
            blocos.append(imagem[inicio - int(deslocamento) : fim - int(deslocamento)])
        if not blocos:
            continue

        strip = np.vstack(blocos)
        poligono_local = [[x, y - y0] for x, y in poligono]
        # ``folga=0`` gera máscara de 0 linhas quando o polígono é degenerado em
        # y (pontos no mesmo KM); o OpenCV 5 recusa ``fillPoly`` nesse caso.
        # Uma folga mínima mantém a ROI com ao menos 1 px de altura.
        try:
            recorte, origem, _mascara = jd_placas.recorta_roi(
                strip, poligono_local, folga=1
            )
        except Exception:
            # Uma placa problemática não pode zerar as ROIs de toda a viagem.
            continue
        if recorte.size == 0:
            continue
        nome = f"{prefixo}_placa{placa['id'].lstrip('P')}.png"
        cv2.imwrite(str(destino / nome), recorte)
        placa["roi"] = {
            "arquivo": nome,
            "faixa": faixas_placa[0].arquivo,
            "x": int(origem[0]),
            "y": int(y0 + origem[1]),
            "largura": int(recorte.shape[1]),
            "altura": int(recorte.shape[0]),
        }
        gravadas += 1

    return gravadas


def resumo_inventario(inventario: dict[str, Any]) -> dict[str, Any]:
    """Resumo enxuto para logs/UI, sem a lista completa de placas."""
    return {
        "trecho": inventario.get("trecho"),
        "norma": inventario.get("norma"),
        "total_placas": len(inventario.get("placas", [])),
        "total_amostras": len(inventario.get("amostras", [])),
        "total_descartadas": len(inventario.get("descartadas", [])),
        "total_blocos_sem_mascara": len(inventario.get("blocos_sem_mascara", [])),
        "avisos": inventario.get("avisos", []),
        "amostras": inventario.get("amostras", []),
    }
