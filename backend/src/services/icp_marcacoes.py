"""Marcações manuais de patologias do ICP (RDT1-23).

A ficha de inspeção (DNIT 062/2004-PRO, Anexo A) entra por arquivo. Este módulo
permite marcar defeitos **por placa** direto no inventário do rígido e somar as
marcações aos defeitos já importados da ficha antes de recalcular a amostra com
:func:`src.services.icp_calculator.calcular_icp_amostra`.

Regras (decididas no card RDT1-23):

* a granularidade é **por placa**; a densidade do defeito na amostra é
  ``placas_afetadas / total_placas``;
* a edição manual é **adicional** à ficha: o recálculo parte de
  ``defeitos_ficha`` e soma as marcações ativas (sem dupla contagem);
* cada marcação guarda autor/data e a remoção é **soft delete** (auditoria).

As marcações vivem em ``dados/<viagem>/icp_marcacoes.json`` (fonte para
edição/consulta, funciona sem banco) e são espelhadas no ``detalhe`` do
resultado persistido em ``resultado_analise`` quando o banco está disponível.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.services import resultado_store
from src.services.icp_calculator import (
    DEFEITOS,
    ITENS_SEM_SEVERIDADE,
    calcular_icp_amostra,
    valor_deduzivel,
)

NORMA_ICP = "DNIT 062/2004-PRO"
ARQUIVO_MARCACOES = "icp_marcacoes.json"


def _agora() -> str:
    return datetime.now(timezone.utc).isoformat()


def _caminho(base: Path) -> Path:
    return Path(base) / ARQUIVO_MARCACOES


def _ler(base: Path) -> list[dict[str, Any]]:
    caminho = _caminho(base)
    if not caminho.is_file():
        return []
    try:
        dados = json.loads(caminho.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if isinstance(dados, dict):
        dados = dados.get("marcacoes", [])
    return [item for item in dados if isinstance(item, dict)]


def _escrever(base: Path, marcacoes: list[dict[str, Any]]) -> None:
    _caminho(base).write_text(
        json.dumps(marcacoes, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _chave(marcacao: dict[str, Any]) -> tuple[int, str]:
    return (int(marcacao["tipo"]), str(marcacao.get("severidade", "U")).upper())


def listar(
    base: Path,
    amostra: str | None = None,
    *,
    incluir_removidas: bool = False,
) -> list[dict[str, Any]]:
    """Marcações da viagem (ou de uma amostra), mais recentes no fim."""
    itens = _ler(base)
    if amostra is not None:
        itens = [item for item in itens if item.get("amostra") == amostra]
    if not incluir_removidas:
        itens = [item for item in itens if item.get("ativo", True)]
    return sorted(itens, key=lambda item: str(item.get("criado_em") or ""))


def obter(base: Path, marcacao_id: str) -> dict[str, Any] | None:
    for item in _ler(base):
        if item.get("id") == marcacao_id:
            return item
    return None


def validar_defeito(tipo: int, severidade: str) -> tuple[int, str]:
    """Valida tipo (1–18) e severidade (B/M/A) contra o motor do ICP."""
    try:
        tipo = int(tipo)
    except (TypeError, ValueError) as exc:
        raise ValueError("tipo de defeito inválido") from exc
    if tipo not in DEFEITOS:
        raise ValueError(f"tipo de defeito inválido: {tipo!r}. Use 1 a 18.")
    severidade = str(severidade or "").strip().upper()
    if tipo in ITENS_SEM_SEVERIDADE:
        severidade = "U"
    elif severidade not in ("B", "M", "A"):
        raise ValueError(f"o defeito {tipo} exige severidade B, M ou A")
    # Levanta ValueError para combinações sem curva (ex.: 6.5 sem severidade).
    valor_deduzivel(tipo, severidade, 0.0)
    return tipo, severidade


def _marcacao_ativa_duplicada(
    marcacoes: list[dict[str, Any]], placa_id: str, tipo: int, severidade: str
) -> dict[str, Any] | None:
    for item in marcacoes:
        if not item.get("ativo", True):
            continue
        if (
            item.get("placa_id") == placa_id
            and int(item.get("tipo")) == tipo
            and str(item.get("severidade")) == severidade
        ):
            return item
    return None


def _resultado_da_amostra(
    viagem_nome: str, amostra: str
) -> dict[str, Any] | None:
    resultados = resultado_store.carregar_resultados(viagem_nome, "icp") or []
    for resultado in resultados:
        if resultado.get("amostra") == amostra:
            return dict(resultado)
    return None


def _defeitos_ficha(resultado: dict[str, Any]) -> list[dict[str, Any]]:
    base = resultado.get("defeitos_ficha")
    if base is None:
        base = resultado.get("defeitos") or []
    return [dict(item) for item in base if isinstance(item, dict)]


def _recalcular(
    resultado: dict[str, Any],
    marcacoes: list[dict[str, Any]],
    *,
    total_placas: float,
) -> dict[str, Any]:
    if total_placas <= 0:
        raise ValueError("A amostra não tem placas para calcular o ICP.")

    base = _defeitos_ficha(resultado)
    combinado: dict[tuple[int, str], dict[str, float | int | str]] = {}
    for defeito in base:
        tipo = int(defeito["tipo"])
        severidade = str(defeito.get("severidade", "U")).upper()
        placas = defeito.get("placas_afetadas")
        if placas is None and defeito.get("densidade") is not None:
            placas = float(defeito["densidade"]) * total_placas / 100.0
        item = combinado.setdefault(
            (tipo, severidade),
            {
                "tipo": tipo,
                "severidade": severidade,
                "placas_ficha": 0.0,
                "placas_manual": 0.0,
            },
        )
        item["placas_ficha"] = float(item["placas_ficha"]) + float(placas or 0.0)

    for marcacao in marcacoes:
        if not marcacao.get("ativo", True):
            continue
        chave = _chave(marcacao)
        item = combinado.setdefault(
            chave,
            {
                "tipo": chave[0],
                "severidade": chave[1],
                "placas_ficha": 0.0,
                "placas_manual": 0.0,
            },
        )
        item["placas_manual"] = float(item["placas_manual"]) + 1.0

    if not combinado:
        return {
            **resultado,
            "defeitos": [],
            "defeitos_ficha": base,
            "vds": [],
            "vdt": 0.0,
            "q": 0,
            "vdc": 0.0,
            "icp": 100.0,
            "conceito": "Excelente",
            "composicao": {
                "defeitos_ficha": 0,
                "marcacoes_manuais": 0,
                "total_placas": total_placas,
            },
            "marcacoes": marcacoes,
        }

    entradas = [
        {
            "tipo": int(item["tipo"]),
            "severidade": str(item["severidade"]),
            "placas_afetadas": float(item["placas_ficha"]) + float(item["placas_manual"]),
        }
        for item in combinado.values()
    ]
    calculo = calcular_icp_amostra(entradas, total_placas)

    defeitos: list[dict[str, Any]] = []
    for vd in calculo["vds"]:
        chave = (int(vd["tipo"]), str(vd["severidade"]))
        origem = combinado[chave]
        item_vd = dict(vd)
        item_vd["placas_afetadas"] = float(origem["placas_ficha"]) + float(
            origem["placas_manual"]
        )
        item_vd["placas_afetadas_ficha"] = float(origem["placas_ficha"])
        item_vd["placas_afetadas_manual"] = float(origem["placas_manual"])
        defeitos.append(item_vd)

    return {
        **resultado,
        "total_placas": total_placas,
        "defeitos": defeitos,
        "defeitos_ficha": base,
        "marcacoes": [item for item in marcacoes if item.get("ativo", True)],
        "composicao": {
            "defeitos_ficha": len(base),
            "marcacoes_manuais": sum(1 for m in marcacoes if m.get("ativo", True)),
            "total_placas": total_placas,
        },
        **{
            chave: calculo[chave]
            for chave in ("vdt", "q", "vdc", "icp", "conceito")
        },
    }


def aplicar_amostra(
    base: Path,
    viagem_nome: str,
    amostra: str,
    *,
    total_placas: float,
    km: int | None = None,
    faixa: str | None = None,
) -> dict[str, Any]:
    """Recalcula a amostra (ficha + marcações) e persiste no banco (best-effort)."""
    marcacoes = listar(base, amostra)
    resultado = _resultado_da_amostra(viagem_nome, amostra)
    if resultado is None:
        resultado = {
            "amostra": amostra,
            "km": int(km) if km is not None else 0,
            "km_informado": km is not None,
            "faixa": faixa,
            "total_placas": total_placas,
            "defeitos": [],
            "norma": NORMA_ICP,
        }
    resultado = _recalcular(resultado, marcacoes, total_placas=total_placas)
    resultado.setdefault("norma", NORMA_ICP)
    resultado_store.persistir_resultados(
        viagem_nome=viagem_nome,
        tipo_analise="icp",
        resultados=[resultado],
        norma=NORMA_ICP,
        reconciliar=False,
    )
    return resultado


def adicionar(
    base: Path,
    viagem_nome: str,
    *,
    placa_id: str,
    amostra: str,
    total_placas: float,
    tipo: int,
    severidade: str,
    km: float | None = None,
    faixa: str | None = None,
    geometria: dict[str, Any] | None = None,
    autor: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    tipo, severidade = validar_defeito(tipo, severidade)
    marcacoes = _ler(base)
    duplicada = _marcacao_ativa_duplicada(marcacoes, placa_id, tipo, severidade)
    if duplicada is not None:
        raise PermissionError(
            f"A placa {placa_id} já tem o defeito {tipo} / severidade {severidade}."
        )

    agora = _agora()
    marcacao = {
        "id": uuid.uuid4().hex,
        "viagem": viagem_nome,
        "amostra": amostra,
        "placa_id": placa_id,
        "km": round(float(km), 3) if km is not None else None,
        "faixa": faixa,
        "tipo": tipo,
        "severidade": severidade,
        "placas_afetadas": 1,
        "geometria": geometria,
        "origem": "manual",
        "ativo": True,
        "criado_por": autor,
        "criado_em": agora,
        "atualizado_por": autor,
        "atualizado_em": agora,
        "removido_por": None,
        "removido_em": None,
    }
    marcacoes.append(marcacao)
    _escrever(base, marcacoes)
    resultado = aplicar_amostra(
        base,
        viagem_nome,
        amostra,
        total_placas=total_placas,
        km=int(km) if km is not None else None,
        faixa=faixa,
    )
    return marcacao, resultado


def atualizar(
    base: Path,
    viagem_nome: str,
    marcacao_id: str,
    *,
    tipo: int | None = None,
    severidade: str | None = None,
    geometria: dict[str, Any] | None = None,
    autor: str | None = None,
    total_placas: float,
) -> tuple[dict[str, Any], dict[str, Any]]:
    marcacoes = _ler(base)
    alvo = next((item for item in marcacoes if item.get("id") == marcacao_id), None)
    if alvo is None:
        raise LookupError(f"marcação não encontrada: {marcacao_id}")
    if not alvo.get("ativo", True):
        raise LookupError(f"marcação removida: {marcacao_id}")

    novo_tipo, nova_sev = validar_defeito(
        tipo if tipo is not None else int(alvo["tipo"]),
        severidade if severidade is not None else str(alvo["severidade"]),
    )
    duplicada = _marcacao_ativa_duplicada(
        [item for item in marcacoes if item.get("id") != marcacao_id],
        str(alvo["placa_id"]),
        novo_tipo,
        nova_sev,
    )
    if duplicada is not None:
        raise PermissionError(
            f"A placa {alvo['placa_id']} já tem o defeito {novo_tipo} / severidade {nova_sev}."
        )

    alvo["tipo"] = novo_tipo
    alvo["severidade"] = nova_sev
    if geometria is not None:
        alvo["geometria"] = geometria
    alvo["atualizado_por"] = autor
    alvo["atualizado_em"] = _agora()
    _escrever(base, marcacoes)
    resultado = aplicar_amostra(
        base,
        viagem_nome,
        str(alvo["amostra"]),
        total_placas=total_placas,
        km=int(alvo["km"]) if alvo.get("km") is not None else None,
        faixa=alvo.get("faixa"),
    )
    return alvo, resultado


def remover(
    base: Path,
    viagem_nome: str,
    marcacao_id: str,
    *,
    autor: str | None = None,
    total_placas: float,
) -> tuple[dict[str, Any], dict[str, Any]]:
    marcacoes = _ler(base)
    alvo = next((item for item in marcacoes if item.get("id") == marcacao_id), None)
    if alvo is None:
        raise LookupError(f"marcação não encontrada: {marcacao_id}")
    if not alvo.get("ativo", True):
        raise LookupError(f"marcação já removida: {marcacao_id}")
    alvo["ativo"] = False
    alvo["removido_por"] = autor
    alvo["removido_em"] = _agora()
    alvo["atualizado_por"] = autor
    alvo["atualizado_em"] = alvo["removido_em"]
    _escrever(base, marcacoes)
    resultado = aplicar_amostra(
        base,
        viagem_nome,
        str(alvo["amostra"]),
        total_placas=total_placas,
        km=int(alvo["km"]) if alvo.get("km") is not None else None,
        faixa=alvo.get("faixa"),
    )
    return alvo, resultado


def reaplicar_apos_importacao(
    base: Path, viagem_nome: str, totais: dict[str, float]
) -> int:
    """Recalcula as amostras que têm marcações após reimportar a ficha."""
    amostras = {item.get("amostra") for item in listar(base)}
    aplicadas = 0
    for amostra in amostras:
        if amostra is None:
            continue
        total = totais.get(str(amostra))
        if not total:
            continue
        aplicar_amostra(base, viagem_nome, str(amostra), total_placas=total)
        aplicadas += 1
    return aplicadas
