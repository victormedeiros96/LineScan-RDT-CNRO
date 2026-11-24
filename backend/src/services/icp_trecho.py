"""Cálculo do ICP do trecho (pavimento rígido) — DNIT 062/2004-PRO.

O motor por amostra fica em :mod:`src.services.icp_calculator`
(:func:`calcular_icp_amostra`, até 20 placas) e reproduz as planilhas da
concessionária. Este módulo fecha o nível do **trecho**, conforme a
DNIT 062/2004-PRO:

* item 7.3.1 — inspeção de todo o trecho: ``ICPt`` é a média aritmética dos
  ICPs das amostras;
* item 7.3.2 — inspeção por amostragem sem amostras adicionais: mesma média
  aritmética;
* item 7.3.3 — amostragem com amostras adicionais:
  ``ICPt = ((N - A) / N) * ICP1 + (A / N) * ICP2``, onde ``ICP1`` é a média das
  amostras normais, ``ICP2`` a média das amostras adicionais, ``N`` o número
  total de amostras do trecho e ``A`` o número de amostras adicionais;
* item 6 — desvio padrão ``S`` dos ICPs e recálculo do número mínimo de
  amostras ``n = (4 * N * S^2) / (e^2 * (N - 1) + 4 * S^2)``, com ``e`` o erro
  admissível informado pelo operador.

Decisões registradas no card RDT1-25:

* ``e`` **não tem valor padrão** — a norma não fixa um. Se não for informado,
  o ICP do trecho e ``S`` continuam sendo calculados e ``n`` fica indisponível;
  se for informado, precisa ser um número finito maior que zero.
* ``N`` deriva do inventário/persistência de amostras: é a quantidade de
  amostras com ICP no trecho. Pode ser repetido na entrada apenas para
  conferência (precisa bater com a quantidade de amostras).
* A estratégia de amostras adicionais é explícita: o operador informa quais
  amostras são adicionais (``adicionais``). O método é selecionável; como o
  processamento em contínuo da RDT1-21 inspeciona o trecho inteiro, o padrão é
  a inspeção total.

O conceito usa o Anexo B na mesma tabela de :func:`classificar_icp` (limites
inferiores exclusivos), para não divergir do resultado por amostra.
"""
from __future__ import annotations

import math
import statistics
from collections.abc import Iterable, Mapping
from typing import Any

from src.services.icp_calculator import classificar_icp

NORMA_ICP = "DNIT 062/2004-PRO"

METODO_TOTAL = "total"
METODO_AMOSTRAGEM = "amostragem"
METODO_AMOSTRAGEM_ADICIONAL = "amostragem_adicional"

_METODOS = {
    METODO_TOTAL: METODO_TOTAL,
    "inspecao_total": METODO_TOTAL,
    METODO_AMOSTRAGEM: METODO_AMOSTRAGEM,
    "sem_adicionais": METODO_AMOSTRAGEM,
    METODO_AMOSTRAGEM_ADICIONAL: METODO_AMOSTRAGEM_ADICIONAL,
    "amostragem_com_adicionais": METODO_AMOSTRAGEM_ADICIONAL,
    "amostras_adicionais": METODO_AMOSTRAGEM_ADICIONAL,
}


def normalizar_metodo(metodo: str | None) -> str:
    """Valida e normaliza o nome do método de inspeção do trecho."""
    chave = str(metodo or METODO_TOTAL).strip().lower()
    try:
        return _METODOS[chave]
    except KeyError as exc:
        raise ValueError(
            f"método inválido: {metodo!r}. Use 'total', 'amostragem' ou "
            "'amostragem_adicional' (DNIT 062/2004-PRO, item 7.3)."
        ) from exc


def _icp_de(amostra: Mapping[str, Any] | float, indice: int) -> float:
    if isinstance(amostra, Mapping):
        valor = amostra.get("icp")
        if valor is None:
            valor = amostra.get("valor")
    else:
        valor = amostra
    if valor is None:
        raise ValueError(f"amostra {indice + 1} sem ICP")
    valor = float(valor)
    if not math.isfinite(valor) or valor < 0 or valor > 100:
        raise ValueError(
            f"amostra {indice + 1} tem ICP fora da faixa 0–100: {valor!r}"
        )
    return valor


def _id_de(amostra: Mapping[str, Any] | float, indice: int) -> str:
    if isinstance(amostra, Mapping):
        for chave in ("amostra", "id", "amostra_id"):
            if amostra.get(chave) is not None:
                return str(amostra[chave])
    return str(indice + 1)


def _media(valores: list[float]) -> float:
    return sum(valores) / len(valores)


def calcular_icp_trecho(
    amostras: Iterable[Mapping[str, Any] | float],
    *,
    metodo: str | None = METODO_TOTAL,
    adicionais: Iterable[str] | None = None,
    n: int | None = None,
    e: float | None = None,
) -> dict[str, Any]:
    """Calcula o ICP do trecho e, quando ``e`` é informado, o ``n`` mínimo.

    ``amostras`` pode ser uma sequência de dicionários com ``amostra``/``id`` e
    ``icp`` (ou ``valor``) ou uma sequência de ICPs numéricos. Amostra em branco
    (sem defeitos, VDT 0) chega aqui com ``icp = 100``, como no cálculo por
    amostra.

    Devolve ``metodo``, ``icp``, ``conceito``, ``N``, ``n_amostras``, ``ICP1``,
    ``ICP2``, ``A``, ``desvio_padrao``, ``e``, ``n_minimo`` e metadados da
    conta. ``n_minimo`` é ``None`` quando ``e`` não é informado.
    """
    metodo = normalizar_metodo(metodo)
    itens = list(amostras)
    if not itens:
        raise ValueError("O trecho não tem amostras de ICP para consolidar.")

    ids = [_id_de(amostra, indice) for indice, amostra in enumerate(itens)]
    valores = [
        _icp_de(amostra, indice) for indice, amostra in enumerate(itens)
    ]
    total_informado = len(valores)

    if n is None:
        n_total = total_informado
    else:
        if isinstance(n, bool):
            raise ValueError("N deve ser um número inteiro positivo")
        try:
            n_total = int(n)
        except (TypeError, ValueError) as exc:
            raise ValueError("N deve ser um número inteiro positivo") from exc
        if n_total != n:
            raise ValueError("N deve ser um número inteiro")
        if n_total <= 0:
            raise ValueError("N deve ser um número inteiro positivo")
        if n_total != total_informado:
            raise ValueError(
                f"N ({n_total}) difere do número de amostras com ICP "
                f"({total_informado}); N deriva do inventário de amostras."
            )

    if metodo == METODO_AMOSTRAGEM_ADICIONAL:
        if not adicionais:
            raise ValueError(
                "O método de amostragem com adicionais exige a lista "
                "'adicionais' com os ids das amostras adicionais."
            )
        ids_adicionais = {str(item).strip() for item in adicionais}
        if "" in ids_adicionais:
            raise ValueError("A lista 'adicionais' tem um id vazio.")
        desconhecidos = ids_adicionais.difference(ids)
        if desconhecidos:
            raise ValueError(
                "Amostras adicionais não encontradas no trecho: "
                + ", ".join(sorted(desconhecidos))
            )
        indices = {indice for indice, chave in enumerate(ids) if chave in ids_adicionais}
        if not indices or len(indices) == total_informado:
            raise ValueError(
                "A amostragem com adicionais exige ao menos uma amostra "
                "normal e uma amostra adicional."
            )
        normais = [valor for indice, valor in enumerate(valores) if indice not in indices]
        adicionais_valores = [
            valor for indice, valor in enumerate(valores) if indice in indices
        ]
        a = len(adicionais_valores)
        icp1 = _media(normais)
        icp2 = _media(adicionais_valores)
        icp = ((n_total - a) / n_total) * icp1 + (a / n_total) * icp2
        ids_do_grupo = sorted(ids_adicionais)
    else:
        a = 0
        icp1 = _media(valores)
        icp2 = None
        icp = icp1
        ids_do_grupo = []

    desvio_padrao = (
        float(statistics.stdev(valores)) if len(valores) > 1 else 0.0
    )

    n_minimo: int | None = None
    n_minimo_exato: float | None = None
    if e is not None:
        try:
            erro = float(e)
        except (TypeError, ValueError) as exc:
            raise ValueError("e deve ser um número maior que zero") from exc
        if not math.isfinite(erro) or erro <= 0:
            raise ValueError("e deve ser um número finito maior que zero")
        numerador = 4.0 * n_total * desvio_padrao**2
        denominador = erro**2 * (n_total - 1) + 4.0 * desvio_padrao**2
        n_minimo_exato = 0.0 if denominador == 0 else numerador / denominador
        # O número de amostras é inteiro; arredonda para cima. Sem variabilidade
        # (S = 0) a fórmula zera, mas o trecho já tem ao menos uma amostra.
        n_minimo = max(1, math.ceil(n_minimo_exato)) if n_minimo_exato > 0 else 1
    else:
        erro = None

    return {
        "nivel": "trecho",
        "metodo": metodo,
        "norma": NORMA_ICP,
        "icp": icp,
        "conceito": classificar_icp(icp),
        "N": n_total,
        "n_amostras": total_informado,
        "amostras": ids,
        "A": a,
        "ICP1": icp1,
        "ICP2": icp2,
        "adicionais": ids_do_grupo,
        "desvio_padrao": desvio_padrao,
        "e": erro,
        "n_minimo": n_minimo,
        "n_minimo_exato": n_minimo_exato,
        "n_disponivel": n_minimo is not None,
    }


def para_persistencia(
    resultado: Mapping[str, Any],
    *,
    km_inicio: float | None = None,
    km_fim: float | None = None,
) -> dict[str, Any]:
    """Prepara o resultado do trecho para `resultado_store`.

    O trecho ocupa a chave ``(viagem, km=0, tipo_analise='icp', amostra=NULL)``:
    ``amostra`` nula o distingue das amostras (``ICP_n``), e o KM sentinela 0
    mantém a gravação idempotente. O intervalo real fica em ``km_inicio``/
    ``km_fim``.
    """
    dados = dict(resultado)
    dados["nivel"] = "trecho"
    dados["km"] = 0
    if km_inicio is not None:
        dados["km_inicio"] = km_inicio
    if km_fim is not None:
        dados["km_fim"] = km_fim
    return dados


def eh_trecho(resultado: Mapping[str, Any]) -> bool:
    """Identifica um resultado de trecho lido da persistência."""
    if str(resultado.get("nivel") or "").lower() == "trecho":
        return True
    return resultado.get("amostra") is None and resultado.get("tipo_analise") == "icp"
