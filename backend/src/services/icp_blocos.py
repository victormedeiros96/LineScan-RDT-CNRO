"""Fechamento dos blocos do ICP pelo cruzamento das juntas detectadas.

Uma placa (bloco) de pavimento rígido é a região fechada entre **duas juntas
transversais consecutivas** e **duas juntas longitudinais** (DNIT 062/2004-PRO).
O ``juntas-detector`` entrega as duas famílias separadamente: as transversais
como retas ``y = a*x + b`` (orientação ``"h"``) e as longitudinais como retas
``x = a*y + b`` (orientação ``"v"``). O pacote também já traz o cruzamento em
``placas.poligonos`` — este módulo é a cola do LineScan entre a detecção e esse
cruzamento, no plano do *strip* concatenado da viagem (``y`` crescente no
sentido do KM e ``x`` lateral).

Além do cruzamento, aplica a regra de completar juntas faltantes definida no
card RDT1-33:

1. mede o **tamanho médio** dos blocos já fechados;
2. para o vão entre duas juntas detectadas, estima
   ``k = round(vão / tamanho_médio)``;
3. valida ``vão ≈ k × tamanho_médio`` (dentro de uma tolerância) e, se valer,
   divide o vão em ``k`` partes iguais inserindo juntas **virtuais**;
4. se o vão não for um múltiplo coerente, **não força**: o trecho fica sem
   máscara e gera aviso.

As linhas virtuais são marcadas em ``origem`` (``"detectada"``/``"virtual"``) e
propagadas ao bloco (``origem_juntas``/``origem_largura``) para a UI sinalizar
cada caso.

Convenções: coordenadas em pixels do strip; ``mm_por_px_y`` é a escala do eixo
longitudinal (no display do LineScan, 0,9765625 mm/px = 1024 px/m).
"""
from __future__ import annotations

import math
from typing import Any, Iterable

from juntas_detector import placas as jd_placas
from juntas_detector.longitudinais import Reta

NORMA_ICP = "DNIT 062/2004-PRO"

# Mesma tolerância de deduplicação usada em ``icp_juntas`` (15 cm).
TOLERANCIA_DUPLICADA_M = 0.15
# Fração do tamanho médio aceita como erro ao validar um vão como múltiplo.
TOLERANCIA_MULTIPLO = 0.35
# Fração do período aceita como desvio de uma junta detectada para uma linha da
# rede periódica. Acima disso a detecção é considerada espúria (RDT1-37).
TOLERANCIA_REDE_FRACAO = 0.25
LARGURA_MIN_M = 3.0
LARGURA_MAX_M = 4.5


def reta_vertical(x: float, altura_px: float) -> Reta:
    """Reta longitudinal sintética (vertical) em ``x`` ao longo do strip."""
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


def reta_para_global(reta: Reta, offset_y: float) -> Reta:
    """Converte ``x = a*y_local + b`` para o ``y`` global do strip da viagem."""
    return Reta(
        a=reta.a,
        b=reta.b - reta.a * offset_y,
        n=reta.n,
        comp=reta.comp,
        y0=reta.y0 + offset_y,
        y1=reta.y1 + offset_y,
        cobertura=reta.cobertura,
        residuo=reta.residuo,
        obs=[(a + offset_y, b + offset_y) for a, b in reta.obs],
    )


def _metro(mm_por_px_y: float, altura_px: float) -> float:
    return float(altura_px) * mm_por_px_y / 1000.0


def _deduplicar(
    transversais: list[dict[str, Any]], mm_por_px_y: float
) -> list[dict[str, Any]]:
    """Remove juntas repetidas (blocos vizinhos, janelas sobrepostas)."""
    ordenadas = sorted(transversais, key=lambda t: float(t["y"]))
    saida: list[dict[str, Any]] = []
    for junta in ordenadas:
        if saida and _metro(mm_por_px_y, float(junta["y"]) - float(saida[-1]["y"])) < TOLERANCIA_DUPLICADA_M:
            # Mantém a de maior cobertura lateral.
            if _cobertura(junta) > _cobertura(saida[-1]):
                saida[-1] = junta
            continue
        saida.append(junta)
    return saida


def _cobertura(junta: dict[str, Any]) -> float:
    return abs(float(junta.get("x1", 0.0)) - float(junta.get("x0", 0.0)))


def _tamanho_medio(
    transversais: list[dict[str, Any]], mm_por_px_y: float, comprimento_max_m: float
) -> float | None:
    vãos = [
        _metro(mm_por_px_y, float(b["y"]) - float(a["y"]))
        for a, b in zip(transversais, transversais[1:])
    ]
    validos = [v for v in vãos if 0 < v < comprimento_max_m]
    if not validos:
        return None
    validos.sort()
    meio = len(validos) // 2
    if len(validos) % 2:
        return validos[meio]
    return (validos[meio - 1] + validos[meio]) / 2.0


def aplicar_rede_periodica(
    transversais: Iterable[dict[str, Any]],
    *,
    periodo_px: float,
    fase_px: float,
    y_inicio: float,
    y_fim: float,
    mm_por_px_y: float,
    tolerancia_frac: float = TOLERANCIA_REDE_FRACAO,
    largura_px: float = 4096.0,
) -> dict[str, Any]:
    """Filtra as juntas pelo **filtro de espaçamento + rede periódica** (RDT1-37).

    A detecção automática do ``trinca-detector`` produz juntas demais (duplicatas
    da mesma linha e falsos positivos nas costuras de frame). Em vez de usar
    diretamente cada transversal detectada, ajusta-se a **rede periódica**
    ``fase + k·período`` (período e fase vindos de ``calibrar_por_juntas``):

    * cada linha da rede recebe no máximo **uma** junta — a detectada de maior
      cobertura lateral dentro de ``tolerancia_frac × período`` (resolve as
      duplicatas, mantendo a de maior extensão lateral);
    * detecções fora da tolerância de qualquer linha são **descartadas**
      (falsos positivos / costuras);
    * linhas da rede sem detecção viram juntas **virtuais**, com ângulo e
      extensão lateral herdados da detecção mais próxima.

    As juntas resultantes ficam alinhadas ao período (placas com comprimento
    coerente, 3,5–5,1 m na BR-364) e o ``y`` é **snapado** à linha da rede.
    Cada item mantém ``origem`` (``"detectada"``/``"virtual"``).

    Devolve o mesmo formato de :func:`completar_transversais`: ``transversais``,
    ``avisos``, ``tamanho_medio_m``, ``vaos_incoerentes`` e
    ``n_detectadas_fora_rede``.
    """
    if periodo_px <= 0:
        raise ValueError("O período da rede deve ser positivo.")

    brutas = [dict(t) for t in transversais]
    periodo_m = float(periodo_px) * float(mm_por_px_y) / 1000.0

    tol = max(float(tolerancia_frac) * float(periodo_px), 0.0)
    y0 = float(min(y_inicio, y_fim))
    y1 = float(max(y_inicio, y_fim))

    k_min = int(math.ceil((y0 - float(fase_px)) / float(periodo_px)))
    k_max = int(math.floor((y1 - float(fase_px)) / float(periodo_px)))
    linhas = [float(fase_px) + k * float(periodo_px) for k in range(k_min, k_max + 1)]

    escolhidas: list[dict[str, Any] | None] = []
    for linha in linhas:
        candidatas = [
            t for t in brutas if abs(float(t["y"]) - linha) <= tol
        ]
        if not candidatas:
            escolhidas.append(None)
            continue
        # Maior cobertura lateral; desempate pela menor distância à linha.
        melhor = max(
            candidatas,
            key=lambda t: (_cobertura(t), -abs(float(t["y"]) - linha)),
        )
        escolhidas.append(melhor)

    # Fora da rede = detecção que não cai na tolerância de nenhuma linha. Uma
    # duplicata absorvida por uma linha **não** conta como descartada.
    fora_rede = sum(
        1
        for junta in brutas
        if all(abs(float(junta["y"]) - linha) > tol for linha in linhas)
    )

    # Ângulo/cobertura para as virtuais: herda da detecção mais próxima.
    detectadas_idx = [i for i, e in enumerate(escolhidas) if e is not None]

    def _herdar(indice: int) -> dict[str, Any]:
        if not detectadas_idx:
            return {"angulo_graus": 0.0, "x0": 0.0, "x1": float(largura_px) - 1.0}
        mais_proximo = min(detectadas_idx, key=lambda i: abs(i - indice))
        base = escolhidas[mais_proximo] or {}
        return {
            "angulo_graus": float(base.get("angulo_graus") or 0.0),
            "x0": float(base.get("x0", 0.0)),
            "x1": float(base.get("x1", float(largura_px) - 1.0)),
        }

    finais: list[dict[str, Any]] = []
    for indice, linha in enumerate(linhas):
        escolhida = escolhidas[indice]
        if escolhida is not None:
            junta = dict(escolhida)
            junta["y"] = linha
            junta["origem"] = "detectada"
        else:
            junta = {"y": linha, "origem": "virtual", **_herdar(indice)}
        finais.append(junta)

    avisos: list[str] = []
    if fora_rede:
        avisos.append(
            f"{fora_rede} junta(s) detectada(s) fora da rede periódica "
            f"(±{tolerancia_frac * 100:.0f}% do período) descartada(s) como espúria(s)."
        )

    return {
        "transversais": finais,
        "avisos": avisos,
        "tamanho_medio_m": round(periodo_m, 3),
        "vaos_incoerentes": [],
        "n_detectadas_fora_rede": fora_rede,
    }


def completar_transversais(
    transversais: Iterable[dict[str, Any]],
    *,
    mm_por_px_y: float,
    comprimento_max_m: float,
    tolerancia: float = TOLERANCIA_MULTIPLO,
) -> dict[str, Any]:
    """Insere juntas virtuais nos vãos que forem múltiplos do tamanho médio.

    Devolve ``transversais`` (detectadas + virtuais, ordenadas), ``avisos`` e o
    ``tamanho_medio_m`` usado. Vãos incoerentes **não** são preenchidos; ficam
    registrados em ``vaos_incoerentes`` (pares de ``y``) para o fechamento.
    """
    detectadas = _deduplicar([dict(t) for t in transversais], mm_por_px_y)
    for junta in detectadas:
        junta.setdefault("origem", "detectada")

    medio = _tamanho_medio(detectadas, mm_por_px_y, comprimento_max_m)
    completas = list(detectadas)
    avisos: list[str] = []
    vaos_incoerentes: list[tuple[float, float]] = []

    if medio and medio > 0:
        for a, b in zip(detectadas, detectadas[1:]):
            vão = _metro(mm_por_px_y, float(b["y"]) - float(a["y"]))
            if vão < comprimento_max_m:
                continue
            k = int(round(vão / medio))
            if k >= 2 and abs(vão - k * medio) <= tolerancia * medio:
                for i in range(1, k):
                    fracao = i / k
                    completas.append(
                        {
                            "y": float(a["y"]) + (float(b["y"]) - float(a["y"])) * fracao,
                            "angulo_graus": float(a.get("angulo_graus") or 0.0)
                            + (
                                float(b.get("angulo_graus") or 0.0)
                                - float(a.get("angulo_graus") or 0.0)
                            )
                            * fracao,
                            "origem": "virtual",
                            "x0": min(float(a.get("x0", 0.0)), float(b.get("x0", 0.0))),
                            "x1": max(float(a.get("x1", 0.0)), float(b.get("x1", 0.0))),
                        }
                    )
            else:
                vaos_incoerentes.append((float(a["y"]), float(b["y"])))
                avisos.append(
                    "Vão incoerente de "
                    f"{vão:.2f} m entre juntas (tamanho médio {medio:.2f} m): "
                    "sem máscara neste trecho."
                )

    completas.sort(key=lambda t: float(t["y"]))
    return {
        "transversais": completas,
        "avisos": avisos,
        "tamanho_medio_m": round(medio, 3) if medio else None,
        "vaos_incoerentes": vaos_incoerentes,
    }


def filtrar_juntas_espurias(
    transversais: Iterable[dict[str, Any]],
    *,
    mm_por_px_y: float,
    comprimento_min_m: float = 3.0,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Descarta juntas detectadas que gerariam uma placa curta demais (espúrias).

    Regra de campo (RDT1-41): percorre as juntas em ordem e, ancorando na última
    junta **mantida**, descarta qualquer detecção que fique a menos de
    ``comprimento_min_m`` dela. Uma junta falsa dentro de uma placa é removida e
    a placa se funde com a vizinha (ex.: 0,92 m + 4,14 m → 5,06 m), em vez de
    gerar um bloco minúsculo. Devolve ``(mantidas, descartadas)``.
    """
    ordenadas = sorted((dict(t) for t in transversais), key=lambda t: float(t["y"]))
    if not ordenadas:
        return [], []
    mantidas = [ordenadas[0]]
    descartadas: list[dict[str, Any]] = []
    for junta in ordenadas[1:]:
        distancia_m = (
            float(junta["y"]) - float(mantidas[-1]["y"])
        ) * float(mm_por_px_y) / 1000.0
        if distancia_m < float(comprimento_min_m):
            descartadas.append(junta)
            continue
        mantidas.append(junta)
    return mantidas, descartadas


def _bloco_em(y: float, blocos_ranges: list[tuple[int, float, float]]) -> int | None:
    for indice, y0, y1 in blocos_ranges:
        if y0 <= y < y1:
            return indice
    if blocos_ranges:
        return min(blocos_ranges, key=lambda b: abs((b[1] + b[2]) / 2.0 - y))[0]
    return None


def _retas_do_bloco(
    y: float,
    retas_por_bloco: dict[int, tuple[Reta, Reta]],
    blocos_ranges: list[tuple[int, float, float]],
) -> tuple[tuple[Reta, Reta] | None, int | None, str | None]:
    """Escolhe o par de longitudinais que fecha o bloco em ``y``.

    Usa o par do bloco que contém ``y``; se ele não tiver par, reaproveita o do
    bloco mais próximo (a longitudinal atravessa blocos) e marca a largura como
    virtual. Devolve ``(par, indice_origem, origem_largura)``.
    """
    if not retas_por_bloco or not blocos_ranges:
        return None, None, None
    for indice, y0, y1 in blocos_ranges:
        if y0 <= y < y1 and indice in retas_por_bloco:
            return retas_por_bloco[indice], indice, "detectada"
    # Nenhum par no bloco: usa o mais próximo em y (largura virtual).
    indice, _y0, _y1 = min(blocos_ranges, key=lambda b: abs((b[1] + b[2]) / 2.0 - y))
    if indice in retas_por_bloco:
        return retas_por_bloco[indice], indice, "virtual"
    candidatos = [i for i in retas_por_bloco]
    if not candidatos:
        return None, None, None
    mais_proximo = min(
        candidatos,
        key=lambda i: min(abs((b[1] + b[2]) / 2.0 - y) for b in blocos_ranges if b[0] == i),
    )
    return retas_por_bloco[mais_proximo], mais_proximo, "virtual"


def montar_blocos(
    transversais: Iterable[dict[str, Any]],
    *,
    km0: float,
    mm_por_px_y: float,
    mm_por_px_x: float | None = None,
    retas_por_bloco: dict[int, tuple[Reta, Reta]] | None = None,
    blocos_ranges: list[tuple[int, float, float]] | None = None,
    comprimento_max_m: float = 6.0,
    comprimento_min_m: float = 3.0,
    tolerancia: float = TOLERANCIA_MULTIPLO,
    largura_min_m: float = LARGURA_MIN_M,
    largura_max_m: float = LARGURA_MAX_M,
    x_ref: float = 2048.0,
    largura_virtual_m: float | None = None,
    periodo_m: float | None = None,
    fase_m: float = 0.0,
    tolerancia_rede: float = TOLERANCIA_REDE_FRACAO,
    largura_px: float = 4096.0,
) -> dict[str, Any]:
    """Fecha os blocos cruzando transversais (detectadas/virtuais) com longitudinais.

    Quando ``periodo_m`` é informado, aplica o **filtro + rede periódica**
    (RDT1-37): as juntas detectadas são consolidadas nas linhas de uma rede
    ``fase + k·período`` e os vãos sem detecção viram juntas virtuais. Sem
    ``periodo_m`` mantém o comportamento anterior (dedup 15 cm + completar vãos
    múltiplos do tamanho médio).

    Devolve ``blocos`` (fechados, com polígono no strip), ``blocos_sem_mascara``
    (vão incoerente ou sem par de longitudinais), ``transversais`` usadas e
    ``avisos``. Cada bloco traz ``origem_juntas`` e ``origem_largura``
    (``"detectada"``/``"virtual"``) e ``km_inicio``/``km_fim`` em km absolutos.
    """
    mm_por_px_x = mm_por_px_y if mm_por_px_x is None else float(mm_por_px_x)
    retas_por_bloco = retas_por_bloco or {}
    blocos_ranges = blocos_ranges or []

    brutas = list(transversais)
    juntas_espurias: list[dict[str, Any]] = []

    if periodo_m is not None and float(periodo_m) > 0:
        if blocos_ranges:
            y0_rede = min(float(b[1]) for b in blocos_ranges)
            y1_rede = max(float(b[2]) for b in blocos_ranges)
        elif brutas:
            ys = [float(t["y"]) for t in brutas]
            y0_rede, y1_rede = min(ys), max(ys)
        else:
            y0_rede = y1_rede = 0.0
        periodo_px = float(periodo_m) * 1000.0 / mm_por_px_y
        fase_px = float(fase_m or 0.0) * 1000.0 / mm_por_px_y
        completado = aplicar_rede_periodica(
            brutas,
            periodo_px=periodo_px,
            fase_px=fase_px,
            y_inicio=y0_rede,
            y_fim=y1_rede,
            mm_por_px_y=mm_por_px_y,
            tolerancia_frac=tolerancia_rede,
            largura_px=largura_px,
        )
    else:
        # Descarta juntas que gerariam placas curtas demais antes de consolidar
        # (RDT1-41). Sem isso uma detecção falsa a poucos centímetros de outra
        # vira um bloco minúsculo em vez de fundir com a placa vizinha.
        brutas, juntas_espurias = filtrar_juntas_espurias(
            brutas,
            mm_por_px_y=mm_por_px_y,
            comprimento_min_m=comprimento_min_m,
        )
        completado = completar_transversais(
            brutas,
            mm_por_px_y=mm_por_px_y,
            comprimento_max_m=comprimento_max_m,
            tolerancia=tolerancia,
        )
    juntas = completado["transversais"]
    # Expoe o km de cada junta efetivamente usada (útil p/ diagnóstico e UI); as
    # juntas derivadas de completar/rede não trazem ``km`` por padrão.
    for junta in juntas:
        junta.setdefault(
            "km", round(km0 + float(junta["y"]) * mm_por_px_y / 1_000_000.0, 6)
        )
    avisos = list(completado["avisos"])
    vaos_incoerentes = set(completado["vaos_incoerentes"])
    if juntas_espurias:
        avisos.insert(
            0,
            f"{len(juntas_espurias)} junta(s) descartada(s) por gerar "
            f"placa < {comprimento_min_m:g} m.",
        )

    if largura_virtual_m is None:
        larguras = [
            abs(esq.x(0.0) - dir.x(0.0)) * mm_por_px_y / 1000.0
            for esq, dir in retas_por_bloco.values()
        ]
        largas = [v for v in larguras if largura_min_m <= v <= largura_max_m]
        if largas:
            largas.sort()
            largura_virtual_m = largas[len(largas) // 2]

    blocos: list[dict[str, Any]] = []
    sem_mascara: list[dict[str, Any]] = []

    for a, b in zip(juntas, juntas[1:]):
        y0, y1 = float(a["y"]), float(b["y"])
        km_ini = km0 + y0 * mm_por_px_y / 1_000_000.0
        km_fim = km0 + y1 * mm_por_px_y / 1_000_000.0
        # km com 6 casas (precisão de ~1 mm). Arredondar em 3 casas (=1 m)
        # deslocava a borda do bloco em até 0,5 m em relação à junta detectada.
        base = {
            "km_inicio": round(km_ini, 6),
            "km_fim": round(km_fim, 6),
            "y_inicio": round(y0, 2),
            "y_fim": round(y1, 2),
        }
        origem_juntas = (
            "virtual"
            if a.get("origem") == "virtual" or b.get("origem") == "virtual"
            else "detectada"
        )
        base["origem_juntas"] = origem_juntas

        if (y0, y1) in vaos_incoerentes:
            sem_mascara.append(
                {
                    **base,
                    "fechado": False,
                    "motivo": "vão incoerente (não é múltiplo do tamanho médio)",
                }
            )
            continue

        meio = (y0 + y1) / 2.0
        par, indice_bloco, origem_largura = _retas_do_bloco(
            meio, retas_por_bloco, blocos_ranges
        )
        if indice_bloco is None:
            indice_bloco = _bloco_em(meio, blocos_ranges)
        if par is None and largura_virtual_m:
            meia = largura_virtual_m * 1000.0 / mm_por_px_y / 2.0
            par = (
                reta_vertical(x_ref - meia, y1),
                reta_vertical(x_ref + meia, y1),
            )
            origem_largura = "virtual"
        if par is None:
            sem_mascara.append(
                {**base, "fechado": False, "motivo": "sem par de juntas longitudinais"}
            )
            continue

        reta_esq, reta_dir = par
        saida = jd_placas.poligonos(
            [y0, y1],
            [float(a.get("angulo_graus") or 0.0), float(b.get("angulo_graus") or 0.0)],
            reta_esq,
            reta_dir,
            largura_min=0.01,
        )
        if not saida:
            sem_mascara.append(
                {**base, "fechado": False, "motivo": "polígono inválido"}
            )
            continue

        poligono = [[float(x), float(y)] for x, y in saida[0]["poligono"]]
        comprimento = round(
            float(
                jd_placas.distancia_transversais(
                    y0,
                    float(a.get("angulo_graus") or 0.0),
                    y1,
                    float(b.get("angulo_graus") or 0.0),
                    mm_x=mm_por_px_x,
                    mm_y=mm_por_px_y,
                )
            ),
            3,
        )
        largura = round(abs(poligono[1][0] - poligono[0][0]) * mm_por_px_x / 1000.0, 3)
        if comprimento > comprimento_max_m:
            sem_mascara.append(
                {
                    **base,
                    "comprimento_m": comprimento,
                    "fechado": False,
                    "motivo": "comprimento fora do limite",
                }
            )
            continue

        blocos.append(
            {
                **base,
                "fechado": True,
                "comprimento_m": comprimento,
                "largura_m": largura,
                "poligono": poligono,
                "origem_largura": origem_largura,
                "bloco_indice": indice_bloco,
                "aviso": None,
            }
        )

    return {
        "blocos": blocos,
        "blocos_sem_mascara": sem_mascara,
        "transversais": juntas,
        "tamanho_medio_m": completado["tamanho_medio_m"],
        "avisos": avisos,
        "n_detectadas_fora_rede": int(completado.get("n_detectadas_fora_rede") or 0),
        "n_juntas_espurias": len(juntas_espurias),
    }
