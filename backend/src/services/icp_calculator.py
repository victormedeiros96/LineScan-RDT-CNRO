"""Cálculo do ICP de pavimento rígido — método da concessionária.

Fonte: planilhas ``ICP Pedágio (Com Fórmula)`` (Anexo II.1–II.9 e PPV01),
repassadas pelos engenheiros da concessionária no card RDT1-3. As curvas e
correções em :mod:`src.services.icp_curvas` foram transcritas das fórmulas
fechadas dessas planilhas e reproduzem os resultados delas exatamente
(regressão em ``backend/tests/test_icp_calculator.py``).

A base normativa é a DNIT 062/2004-PRO (método CERL, 1979), mas este módulo
implementa de propósito as escolhas da planilha da concessionária, que diferem
da leitura estrita da norma em pontos que mudam o resultado:

* ``q`` conta os tipos (exceto o 6.5) cujo maior valor dedutível é ``>= 5``
  (a norma diz ``> 5``) e não conta o defeito 6.5;
* ``VDC = max(maior correção "q1 por defeito", curva q do VDT)`` — a norma usa
  somente a curva q;
* com ``q = 0`` o ``VDC`` é o próprio ``VDT``; com ``VDT = 0`` o ``ICP`` é 100
  (inclui o efeito de texto "0,00" da planilha);
* o 6.18 usa a curva da planilha, que em A/M é praticamente a 6.4 do PDF.

O cálculo é por **amostra** (até 20 placas). O ICP do trecho não é definido
nesta planilha e fica para uma etapa seguinte.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from src.services.icp_curvas import CURVAS, VDC_CURVAS, _ac

DEFEITOS: dict[int, str] = {
    1: "Alçamento de placas",
    2: "Fissura de canto",
    3: "Placa dividida",
    4: "Degrau de junta (escalonamento)",
    5: "Defeito na selagem das juntas",
    6: "Desnível pavimento-acostamento",
    7: "Fissuras lineares",
    8: "Grandes reparos",
    9: "Pequenos reparos",
    10: "Desgaste superficial",
    11: "Bombeamento",
    12: "Quebras localizadas",
    13: "Passagem de nível",
    14: "Rendilhado e escamação",
    15: "Fissuras de retração plástica",
    16: "Quebra de canto",
    17: "Esborcinamento de juntas",
    18: "Placa bailarina",
}

SEVERIDADES = ("A", "M", "B")
ITENS_SEM_SEVERIDADE = frozenset({10, 11, 15})
ITEM_SELAGEM = 5
VALORES_SELAGEM = {"A": 8.0, "M": 4.0, "B": 2.0}
_ORDEM_SEVERIDADE = {"A": 3, "M": 2, "B": 1, "U": 0}


def densidade(placas_afetadas: float, total_placas: float) -> float:
    """Percentual de placas afetadas na amostra."""
    if total_placas <= 0:
        raise ValueError("total_placas deve ser maior que zero")
    return float(placas_afetadas) / float(total_placas) * 100.0


def valor_deduzivel(tipo: int, severidade: str, densidade_pct: float) -> float:
    """Valor dedutível (VD) de um defeito, já com o teto da curva da planilha.

    ``tipo`` segue a numeração da ficha de inspeção (1–18). Os defeitos 10, 11 e
    15 não têm grau de severidade; o 6.5 usa valor fixo (2/4/8) e ignora a
    densidade.
    """
    tipo = int(tipo)
    severidade = str(severidade).strip().upper()
    if tipo not in DEFEITOS:
        raise ValueError(f"tipo de defeito inválido: {tipo!r}. Use 1 a 18.")
    if tipo == ITEM_SELAGEM:
        if severidade not in VALORES_SELAGEM:
            raise ValueError("o defeito 5 exige severidade B, M ou A")
        return VALORES_SELAGEM[severidade]
    if tipo in ITENS_SEM_SEVERIDADE:
        severidade = "U"
    elif severidade not in SEVERIDADES:
        raise ValueError(f"o defeito {tipo} exige severidade B, M ou A")
    try:
        curva = CURVAS[(tipo, severidade)]
    except KeyError as exc:
        raise ValueError(
            f"sem curva para o defeito {tipo} / severidade {severidade!r}"
        ) from exc
    densidade_pct = min(100.0, max(0.0, float(densidade_pct)))
    return curva(densidade_pct)


def _normalizar_defeitos(
    defeitos: Iterable[Any], total_placas: float | None
) -> list[dict[str, float | int | str]]:
    normalizados: list[dict[str, float | int | str]] = []
    for defeito in defeitos:
        if isinstance(defeito, Mapping):
            tipo = int(defeito["tipo"])
            severidade = str(defeito.get("severidade", "U")).upper()
            if "densidade" in defeito:
                dens = float(defeito["densidade"])
            elif "densidade_pct" in defeito:
                dens = float(defeito["densidade_pct"])
            elif "placas_afetadas" in defeito and total_placas is not None:
                dens = densidade(float(defeito["placas_afetadas"]), total_placas)
            else:
                raise ValueError(
                    "informe densidade ou placas_afetadas (com total_placas)"
                )
        else:
            tipo, severidade, dens = defeito
            tipo = int(tipo)
            severidade = str(severidade).upper()
            dens = float(dens)
        if tipo in ITENS_SEM_SEVERIDADE:
            severidade = "U"
        normalizados.append({"tipo": tipo, "severidade": severidade, "densidade": dens})

    # 6.5: só a maior severidade presente conta (como na planilha).
    sev_selagem = [d for d in normalizados if d["tipo"] == ITEM_SELAGEM]
    if len(sev_selagem) > 1:
        maior = max(sev_selagem, key=lambda d: _ORDEM_SEVERIDADE[str(d["severidade"])])
        normalizados = [d for d in normalizados if d["tipo"] != ITEM_SELAGEM]
        normalizados.append(maior)

    vistos: set[tuple[int, str]] = set()
    for d in normalizados:
        chave = (int(d["tipo"]), str(d["severidade"]))
        if chave in vistos:
            raise ValueError(
                f"defeito {chave[0]} / severidade {chave[1]!r} repetido na amostra"
            )
        vistos.add(chave)
    return normalizados


def corrigir_vdc(vdt: float, q: int) -> float:
    """Valor dedutível corrigido (VDC), conforme a planilha da concessionária."""
    if vdt <= 0:
        return 0.0
    if q <= 0:
        return float(vdt)
    return float(VDC_CURVAS[min(int(q), 6)](float(vdt)))


def classificar_icp(icp: float) -> str:
    """Conceito do Anexo B (limites inferiores exclusivos, como na planilha)."""
    if icp < 0:
        return "Erro"
    if icp < 10:
        return "Destruído"
    if icp < 25:
        return "Muito Ruim"
    if icp < 40:
        return "Ruim"
    if icp < 55:
        return "Razoável"
    if icp < 70:
        return "Bom"
    if icp < 85:
        return "Muito Bom"
    if icp <= 100:
        return "Excelente"
    return "Erro"


def calcular_icp_amostra(
    defeitos: Iterable[Any], total_placas: float | None = None
) -> dict[str, Any]:
    """Calcula o ICP de uma amostra.

    ``defeitos`` é uma sequência de tuplas ``(tipo, severidade, densidade_pct)``
    ou de dicionários com ``tipo``, ``severidade`` e ``densidade``/
    ``densidade_pct`` (ou ``placas_afetadas`` + ``total_placas``).

    Devolve ``vds``, ``vdt``, ``q``, ``vdc``, ``icp`` e ``conceito``.
    """
    normalizados = _normalizar_defeitos(defeitos, total_placas)
    vds = [
        {
            "tipo": d["tipo"],
            "severidade": d["severidade"],
            "densidade": d["densidade"],
            "vd": valor_deduzivel(int(d["tipo"]), str(d["severidade"]), float(d["densidade"])),
        }
        for d in normalizados
    ]
    vdt = sum(float(v["vd"]) for v in vds)

    maior_por_tipo: dict[int, float] = {}
    for v in vds:
        if v["tipo"] == ITEM_SELAGEM:
            continue
        tipo = int(v["tipo"])
        maior_por_tipo[tipo] = max(maior_por_tipo.get(tipo, 0.0), float(v["vd"]))
    q = sum(1 for vd in maior_por_tipo.values() if vd >= 5.0)

    if vdt <= 0:
        vdc = 0.0
    else:
        maior_ac = max(
            (_ac(float(v["vd"])) for v in vds if v["tipo"] != ITEM_SELAGEM),
            default=0.0,
        )
        vdc = max(maior_ac, corrigir_vdc(vdt, q))
    icp = 100.0 - vdc

    return {
        "vds": vds,
        "vdt": vdt,
        "q": q,
        "vdc": vdc,
        "icp": icp,
        "conceito": classificar_icp(icp),
    }
