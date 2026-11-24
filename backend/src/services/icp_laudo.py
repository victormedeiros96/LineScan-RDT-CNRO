"""Laudo do ICP (pavimento rígido) — DNIT 062/2004-PRO, item 5.5.

O cálculo por amostra (:mod:`src.services.icp_calculator`) e por trecho
(:mod:`src.services.icp_trecho`) já existem. Este módulo monta o **laudo**
exigido pelo item 5.5 da norma, reunindo as seções que não aparecem na tabela
plana por amostra:

* parecer/resumo do trecho (ICPt, N, ``n`` mínimo, desvio padrão, conceito);
* distribuição das amostras por conceito (Anexo B);
* juntas e selante (defeito 5) e sua extensão estimada;
* defeitos atípicos (registros fora das curvas 6.1–6.18, sem valor dedutível);
* assentamentos e buracos (itens 19 e 20, registros complementares);
* amostras com **ICP ≤ 40** (Anexo B: "Ruim" e pior), que o laudo destaca.

Decisões de leitura da norma:

* Amostra **em branco** (sem defeitos, ICP 100) entra no resumo por amostra e
  na distribuição de conceitos, mas **não** polui as seções de defeitos —
  regra registrada no card RDT1-26.
* A extensão das juntas é uma **estimativa** (nº de juntas/placas afetadas ×
  largura da placa do inventário), porque a ficha mede a selagem por
  severidade/densidade, não por comprimento. Quando a largura não é conhecida,
  ``extensao_m`` fica ``None`` e o laudo registra a observação.
* Item 19 (assentamento) e 20 (buracos) não são dedutíveis (Anexo A); entram
  como registro complementar, separados dos defeitos 1–18.
* Nenhuma fórmula, peso ou faixa de conceito é recalculada aqui: o laudo apenas
  agrega o que veio de :mod:`icp_calculator`/:mod:`icp_trecho`.
"""
from __future__ import annotations

import statistics
from collections import Counter
from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from typing import Any

from src.services.icp_calculator import DEFEITOS, ITEM_SELAGEM
from src.services.icp_trecho import METODO_TOTAL, calcular_icp_trecho

NORMA_ICP = "DNIT 062/2004-PRO"

LIMITE_ICP_CRITICO = 40.0

ITEM_ASSENTAMENTO = 19
ITEM_BURACO = 20

CONCEITOS_ORDEM = (
    "Destruído",
    "Muito Ruim",
    "Ruim",
    "Razoável",
    "Bom",
    "Muito Bom",
    "Excelente",
)

SECOES = (
    "juntas_selante",
    "defeitos_atipicos",
    "assentamentos",
    "buracos",
)


def _icp(item: Mapping[str, Any]) -> float:
    valor = item.get("icp")
    if valor is None:
        valor = item.get("valor")
    return float(valor or 0.0)


def _numero(valor: Any) -> float | None:
    if valor is None:
        return None
    try:
        return float(valor)
    except (TypeError, ValueError):
        return None


def _amostra_id(item: Mapping[str, Any]) -> str:
    for chave in ("amostra", "amostra_id", "id"):
        valor = item.get(chave)
        if valor is not None and str(valor).strip():
            return str(valor)
    return "?"


def _eh_trecho(item: Mapping[str, Any]) -> bool:
    if str(item.get("nivel") or "").lower() == "trecho":
        return True
    return item.get("amostra") is None and item.get("tipo_analise") == "icp"


def _em_branco(item: Mapping[str, Any]) -> bool:
    """Amostra sem defeitos registrados (ICP 100, VDT 0)."""
    if item.get("defeitos"):
        return False
    vdt = _numero(item.get("vdt"))
    return vdt is None or vdt <= 0


def _base_item(item: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "amostra": _amostra_id(item),
        "rodovia": item.get("rodovia"),
        "km": item.get("km"),
        "km_inicial": item.get("km_inicial"),
        "km_final": item.get("km_final"),
        "faixa": item.get("faixa"),
    }


def _item_defeito(
    item: Mapping[str, Any],
    defeito: Mapping[str, Any],
    *,
    largura_m: float | None,
) -> dict[str, Any]:
    tipo = int(defeito.get("tipo", 0))
    placas = _numero(defeito.get("placas_afetadas"))
    linha = {
        **_base_item(item),
        "tipo": tipo,
        "nome": DEFEITOS.get(tipo, f"Defeito {tipo}"),
        "severidade": defeito.get("severidade"),
        "densidade": _numero(defeito.get("densidade")),
        "placas_afetadas": placas,
        "vd": _numero(defeito.get("vd")),
        "largura_placa_m": largura_m,
    }
    if tipo == ITEM_SELAGEM:
        linha["juntas_afetadas"] = placas
        linha["extensao_m"] = (
            round(placas * largura_m, 2)
            if placas is not None and largura_m
            else None
        )
    return linha


def _itens_extras(
    item: Mapping[str, Any],
    chave: str,
    *,
    tipo_padrao: int | None = None,
) -> list[dict[str, Any]]:
    """Lê registros complementares (``atipicos``/``assentamentos``/``buracos``)."""
    brutos = item.get(chave) or []
    if isinstance(brutos, Mapping):
        brutos = [brutos]
    itens: list[dict[str, Any]] = []
    for bruto in brutos:
        if isinstance(bruto, Mapping):
            linha = {**_base_item(item), **bruto}
            linha.setdefault("tipo", tipo_padrao)
            linha.setdefault("nome", bruto.get("descricao") or bruto.get("nome"))
            for campo in ("densidade", "vd", "placas_afetadas", "extensao_m"):
                if campo in linha:
                    linha[campo] = _numero(linha[campo])
        else:
            linha = {
                **_base_item(item),
                "tipo": tipo_padrao,
                "nome": str(bruto),
                "severidade": None,
                "densidade": None,
                "placas_afetadas": None,
                "vd": None,
                "extensao_m": None,
            }
        itens.append(linha)
    return itens


def _largura(item: Mapping[str, Any], larguras: Mapping[str, float]) -> float | None:
    return larguras.get(_amostra_id(item))


def _resumo_juntas(itens: list[dict[str, Any]]) -> dict[str, Any]:
    severidades = Counter(str(item.get("severidade") or "—") for item in itens)
    juntas = sum(int(item.get("juntas_afetadas") or 0) for item in itens)
    extensoes = [
        float(item["extensao_m"])
        for item in itens
        if item.get("extensao_m") is not None
    ]
    return {
        "amostras_afetadas": len({item["amostra"] for item in itens}),
        "juntas_afetadas": juntas,
        "extensao_total_m": round(sum(extensoes), 2) if extensoes else None,
        "por_severidade": dict(sorted(severidades.items())),
        "extensao_observacao": (
            "Extensão estimada pelo nº de juntas afetadas × largura da placa."
            if extensoes
            else "Extensão indisponível: largura da placa não conhecida no inventário."
        ),
    }


def _resumo_secao(itens: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "amostras_afetadas": len({item["amostra"] for item in itens}),
        "total": len(itens),
    }


def _metadados(itens: list[Mapping[str, Any]], norma: str) -> dict[str, Any]:
    def primeiro(campo: str) -> Any:
        for item in itens:
            valor = item.get(campo)
            if valor not in (None, ""):
                return valor
        return None

    def distintos(campo: str) -> list[Any]:
        vistos: list[Any] = []
        for item in itens:
            valor = item.get(campo)
            if valor not in (None, "") and valor not in vistos:
                vistos.append(valor)
        return vistos

    kms_iniciais = [
        float(km) for km in (_numero(item.get("km_inicial")) for item in itens) if km is not None
    ]
    kms_finais = [
        float(km) for km in (_numero(item.get("km_final")) for item in itens) if km is not None
    ]
    return {
        "rodovia": primeiro("rodovia"),
        "km_inicio": min(kms_iniciais) if kms_iniciais else None,
        "km_fim": max(kms_finais) if kms_finais else None,
        "faixas": distintos("faixa"),
        "datas": distintos("data"),
        "operadores": distintos("operador"),
        "norma": norma,
        "limite_icp_critico": LIMITE_ICP_CRITICO,
    }


def _resumo_amostra(item: Mapping[str, Any], largura: float | None) -> dict[str, Any]:
    return {
        "amostra": _amostra_id(item),
        "rodovia": item.get("rodovia"),
        "km_inicial": item.get("km_inicial"),
        "km_final": item.get("km_final"),
        "faixa": item.get("faixa"),
        "total_placas": _numero(item.get("total_placas")),
        "vdt": _numero(item.get("vdt")),
        "q": item.get("q"),
        "vdc": _numero(item.get("vdc")),
        "icp": _icp(item),
        "conceito": item.get("conceito"),
        "em_branco": _em_branco(item),
        "critico": _icp(item) <= LIMITE_ICP_CRITICO,
        "largura_placa_m": largura,
    }


def _distribuicao(amostras: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    contagem: Counter[str] = Counter()
    faixas: dict[str, tuple[float, float]] = {}
    for item in amostras:
        conceito = str(item.get("conceito") or "—")
        contagem[conceito] += 1
        icp = _icp(item)
        minimo, maximo = faixas.get(conceito, (icp, icp))
        faixas[conceito] = (min(minimo, icp), max(maximo, icp))
    total = len(amostras) or 1
    distribuicao = []
    for conceito in CONCEITOS_ORDEM:
        if conceito not in contagem:
            continue
        minimo, maximo = faixas[conceito]
        distribuicao.append(
            {
                "conceito": conceito,
                "quantidade": contagem[conceito],
                "percentual": round(contagem[conceito] / total * 100, 2),
                "icp_min": minimo,
                "icp_max": maximo,
            }
        )
    for conceito, quantidade in contagem.items():
        if conceito in CONCEITOS_ORDEM:
            continue
        minimo, maximo = faixas[conceito]
        distribuicao.append(
            {
                "conceito": conceito,
                "quantidade": quantidade,
                "percentual": round(quantidade / total * 100, 2),
                "icp_min": minimo,
                "icp_max": maximo,
            }
        )
    return distribuicao


def montar_laudo(
    resultados: Iterable[Mapping[str, Any]],
    *,
    larguras: Mapping[str, float] | None = None,
    trecho: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Monta o laudo estruturado do ICP a partir dos resultados persistidos.

    ``resultados`` pode incluir o consolidado do trecho (ignorado aqui e usado
    via ``trecho`` ou detectado pela marca ``nivel='trecho'``). ``larguras``
    mapeia amostra → largura da placa (m) para estimar a extensão das juntas.
    """
    larguras = dict(larguras or {})
    itens = list(resultados)
    if not itens:
        raise ValueError("Nenhum resultado de ICP para montar o laudo.")
    if trecho is None:
        trecho = next((item for item in itens if _eh_trecho(item)), None)
    amostras = [item for item in itens if not _eh_trecho(item)]
    if not amostras:
        raise ValueError("Nenhuma amostra de ICP para montar o laudo.")

    if trecho is None:
        trecho = calcular_icp_trecho(
            [{"amostra": _amostra_id(item), "icp": _icp(item)} for item in amostras],
            metodo=METODO_TOTAL,
        )

    juntas: list[dict[str, Any]] = []
    atipicos: list[dict[str, Any]] = []
    assentamentos: list[dict[str, Any]] = []
    buracos: list[dict[str, Any]] = []
    defeitos: list[dict[str, Any]] = []

    for item in amostras:
        largura = _largura(item, larguras)
        critico = _icp(item) <= LIMITE_ICP_CRITICO
        # Amostra em branco não tem defeitos; a guarda evita ruído nas seções.
        for defeito in item.get("defeitos") or []:
            tipo = int(defeito.get("tipo", 0))
            if tipo in DEFEITOS:
                linha = _item_defeito(item, defeito, largura_m=largura)
                linha["critico"] = critico
                defeitos.append(linha)
            if tipo == ITEM_SELAGEM:
                juntas.append(_item_defeito(item, defeito, largura_m=largura))
            elif tipo == ITEM_ASSENTAMENTO:
                assentamentos.append(_item_defeito(item, defeito, largura_m=largura))
            elif tipo == ITEM_BURACO:
                buracos.append(_item_defeito(item, defeito, largura_m=largura))
            elif tipo not in DEFEITOS:
                atipicos.append(_item_defeito(item, defeito, largura_m=largura))
        atipicos.extend(_itens_extras(item, "atipicos"))
        assentamentos.extend(
            _itens_extras(item, "assentamentos", tipo_padrao=ITEM_ASSENTAMENTO)
        )
        buracos.extend(
            _itens_extras(item, "buracos", tipo_padrao=ITEM_BURACO)
        )

    criticas = [
        _resumo_amostra(item, _largura(item, larguras))
        for item in amostras
        if _icp(item) <= LIMITE_ICP_CRITICO
    ]
    criticas.sort(key=lambda item: item["icp"])

    resumos = [
        _resumo_amostra(item, _largura(item, larguras)) for item in amostras
    ]
    resumos.sort(key=lambda item: (item["km_inicial"] is None, item["km_inicial"] or 0, item["amostra"]))

    return {
        "norma": NORMA_ICP,
        "gerado_em": datetime.now(timezone.utc).isoformat(),
        "metadados": _metadados(amostras, NORMA_ICP),
        "resumo_trecho": dict(trecho),
        "total_amostras": len(amostras),
        "amostras_em_branco": [
            _amostra_id(item) for item in amostras if _em_branco(item)
        ],
        "distribuicao_conceitos": _distribuicao(amostras),
        "amostras": resumos,
        "defeitos": defeitos,
        "amostras_criticas": criticas,
        "limite_icp_critico": LIMITE_ICP_CRITICO,
        "secoes": {
            "juntas_selante": {
                "resumo": _resumo_juntas(juntas),
                "itens": juntas,
            },
            "defeitos_atipicos": {
                "resumo": _resumo_secao(atipicos),
                "itens": atipicos,
            },
            "assentamentos": {
                "resumo": _resumo_secao(assentamentos),
                "itens": assentamentos,
            },
            "buracos": {
                "resumo": _resumo_secao(buracos),
                "itens": buracos,
            },
        },
    }


def larguras_por_amostra(inventario: Mapping[str, Any] | None) -> dict[str, float]:
    """Largura mediana das placas por amostra, a partir do inventário."""
    if not inventario:
        return {}
    por_amostra: dict[str, list[float]] = {}
    for placa in inventario.get("placas") or []:
        amostra = placa.get("amostra_id")
        largura = _numero(placa.get("largura_m"))
        if amostra is None or largura is None or largura <= 0:
            continue
        por_amostra.setdefault(str(amostra), []).append(largura)
    return {
        amostra: float(statistics.median(valores))
        for amostra, valores in por_amostra.items()
        if valores
    }
