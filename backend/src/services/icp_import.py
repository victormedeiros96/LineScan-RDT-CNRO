"""Importação das fichas de inspeção de ICP (planilhas ICP Pedágio).

Lê o arquivo XLSX/XLSM no formato das planilhas **ICP Pedágio (Com Fórmula)**
(Anexo II.1–II.10 / PPV01) e devolve uma amostra por aba ``ICP_n`` já calculada
por :func:`src.services.icp_calculator.calcular_icp_amostra`.

A planilha aceita dois caminhos de entrada, que aqui são lidos na seguinte
ordem para cada linha da tabela de defeitos (linhas 19–66):

1. **R/U manuais** — quando o usuário digita o nº de placas afetadas (``R``) ou
   o percentual (``U``), o valor exibido é usado como a contagem daquele
   defeito/severidade;
2. **códigos C/I** — quando ``R``/``U`` estão vazios (ou a planilha foi gerada
   sem a memória de cálculo do Excel), conta as ocorrências dos códigos
   ``tipo + severidade`` (ex.: ``2B``, ``10``) nas colunas ``C:E`` e ``I:K``.

O CSV aceito é uma versão achatada da ficha: uma linha por defeito, com as
colunas ``amostra``, ``total_placas``, ``tipo``, ``severidade`` e
``placas_afetadas`` ou ``densidade``; metadados como ``rodovia``, ``km``,
``km_inicial``, ``km_final``, ``faixa``, ``data`` e ``operador`` são opcionais.

Fonte normativa: DNIT 062/2004-PRO. O cálculo segue, de propósito, as escolhas
das planilhas da concessionária (ver :mod:`src.services.icp_calculator`).
"""
from __future__ import annotations

import csv
import re
import unicodedata
from collections import Counter, OrderedDict
from pathlib import Path
from typing import Any

from openpyxl import load_workbook

from src.services.icp_calculator import (
    DEFEITOS,
    ITENS_SEM_SEVERIDADE,
    calcular_icp_amostra,
)

NORMA_ICP = "DNIT 062/2004-PRO"
EXTENSOES = {".xlsx", ".xlsm", ".csv"}

_ABA_ICP = re.compile(r"^ICP_(\d+)$", re.IGNORECASE)
_CODIGO = re.compile(r"^(\d{1,2})\s*([AMB])?$")
_KM = re.compile(r"^(\d+)\+(\d{1,3})$")
_NUMERO = re.compile(r"^-?\d+(?:[.,]\d+)?$")

_LINHA_INICIAL = 19
_LINHA_FINAL = 66
_TOTAL_PLACAS = "C60"
_PLACAS_CABECALHO = "W4"


def _texto(valor: Any) -> str | None:
    if valor is None:
        return None
    texto = str(valor).strip()
    return texto or None


def _numero(valor: Any) -> float | None:
    if isinstance(valor, bool):
        return None
    if isinstance(valor, (int, float)):
        return float(valor)
    texto = _texto(valor)
    if texto is None:
        return None
    if not _NUMERO.fullmatch(texto):
        return None
    return float(texto.replace(",", "."))


def _inteiro(valor: Any) -> int | None:
    numero = _numero(valor)
    if numero is None:
        return None
    return int(numero)


def parse_km(valor: Any) -> float | None:
    """Converte ``33+600`` (ou ``33,6``) em km decimal (``33.6``)."""
    texto = _texto(valor)
    if texto is None:
        return None
    texto = texto.upper().replace(" ", "")
    if texto in {"-", "--", "—"}:
        return None
    casado = _KM.fullmatch(texto)
    if casado:
        metros = casado.group(2)
        return int(casado.group(1)) + int(metros) / (10 ** len(metros))
    numero = _numero(texto)
    return numero


def _km_int(km: float | None) -> int | None:
    return int(km) if km is not None else None


def _faixa(valor: Any) -> str | None:
    texto = _texto(valor)
    if texto is None:
        return None
    if ":" in texto:
        texto = texto.split(":", 1)[1].strip()
    return texto or None


def _mapa_linhas(ws: Any) -> dict[int, tuple[int, str]]:
    """Mapeia cada linha da tabela de defeitos para (tipo, severidade)."""
    mapa: dict[int, tuple[int, str]] = {}
    tipo: int | None = None
    for linha in range(_LINHA_INICIAL, _LINHA_FINAL + 1):
        valor_tipo = ws.cell(linha, 12).value
        if valor_tipo is not None:
            try:
                tipo = int(float(valor_tipo))
            except (TypeError, ValueError):
                tipo = None
        if tipo is None:
            continue
        severidade = (_texto(ws.cell(linha, 15).value) or "").upper()
        if severidade in {"A", "M", "B"}:
            mapa[linha] = (tipo, severidade)
        elif tipo in ITENS_SEM_SEVERIDADE:
            mapa[linha] = (tipo, "U")
    return mapa


def _contar_codigos(ws: Any, amostra: str) -> Counter[tuple[int, str]]:
    """Conta os códigos ``tipo+severidade`` das colunas C:E e I:K."""
    contagem: Counter[tuple[int, str]] = Counter()
    for linha in range(7, 55):
        for coluna in (3, 4, 5, 9, 10, 11):
            valor = ws.cell(linha, coluna).value
            if valor is None:
                continue
            codigo = str(valor).strip().upper()
            if not codigo:
                continue
            casado = _CODIGO.fullmatch(codigo)
            if not casado:
                celula = ws.cell(linha, coluna).coordinate
                raise ValueError(
                    f"{amostra}!{celula}: código de defeito inválido: {codigo!r}"
                )
            tipo = int(casado.group(1))
            if tipo not in DEFEITOS:
                celula = ws.cell(linha, coluna).coordinate
                raise ValueError(
                    f"{amostra}!{celula}: tipo de defeito inválido: {tipo}"
                )
            severidade = casado.group(2)
            if tipo in ITENS_SEM_SEVERIDADE:
                severidade = "U"
            elif severidade is None:
                celula = ws.cell(linha, coluna).coordinate
                raise ValueError(
                    f"{amostra}!{celula}: código {codigo!r} sem severidade A/M/B"
                )
            contagem[(tipo, severidade)] += 1
    return contagem


def _defeitos_da_aba(ws: Any, total_placas: float, amostra: str) -> list[dict[str, Any]]:
    codigos = _contar_codigos(ws, amostra)
    defeitos: list[dict[str, Any]] = []
    for linha, (tipo, severidade) in _mapa_linhas(ws).items():
        placas = _numero(ws.cell(linha, 18).value)
        percentual = _numero(ws.cell(linha, 21).value)

        if placas is not None:
            if placas <= 0:
                continue
            densidade = placas / total_placas * 100.0
        elif codigos[(tipo, severidade)] > 0:
            placas = float(codigos[(tipo, severidade)])
            densidade = placas / total_placas * 100.0
        elif percentual is not None and percentual > 0:
            densidade = percentual
            placas = None
        else:
            continue

        defeitos.append(
            {
                "tipo": tipo,
                "severidade": severidade,
                "placas_afetadas": placas,
                "densidade": densidade,
            }
        )
    return defeitos


def _mesclar_vds(
    defeitos: list[dict[str, Any]], vds: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    placas_por_chave = {
        (int(d["tipo"]), str(d["severidade"])): d.get("placas_afetadas")
        for d in defeitos
    }
    resultado = []
    for vd in vds:
        item = dict(vd)
        item["placas_afetadas"] = placas_por_chave.get(
            (int(vd["tipo"]), str(vd["severidade"]))
        )
        resultado.append(item)
    return resultado


def _resultado_amostra(
    *,
    amostra: str,
    total_placas: float,
    defeitos: list[dict[str, Any]],
    metadados: dict[str, Any],
) -> dict[str, Any]:
    calculo = calcular_icp_amostra(defeitos)
    km_inicial = metadados.get("km_inicial")
    return {
        "amostra": amostra,
        "rodovia": metadados.get("rodovia"),
        "km_inicial": km_inicial,
        "km_final": metadados.get("km_final"),
        "km": _km_int(km_inicial),
        "km_informado": km_inicial is not None,
        "local": metadados.get("local"),
        "pedagio": metadados.get("pedagio"),
        "faixa": metadados.get("faixa"),
        "data": metadados.get("data"),
        "operador": metadados.get("operador"),
        "total_placas": total_placas,
        "defeitos": _mesclar_vds(defeitos, calculo["vds"]),
        "vdt": calculo["vdt"],
        "q": calculo["q"],
        "vdc": calculo["vdc"],
        "icp": calculo["icp"],
        "conceito": calculo["conceito"],
        "norma": NORMA_ICP,
    }


def _metadados_aba(ws: Any) -> dict[str, Any]:
    local = _texto(ws["O3"].value)
    pedagio = _inteiro(ws["O3"].value)
    if pedagio is not None:
        local = None
    return {
        "rodovia": _texto(ws["C3"].value),
        "km_inicial": parse_km(ws["I3"].value),
        "km_final": parse_km(ws["I4"].value),
        "local": local,
        "pedagio": pedagio,
        "faixa": _faixa(ws["L4"].value),
        "data": _texto(ws["C4"].value),
        "operador": _texto(ws["W3"].value),
    }


def _importar_xlsx(arquivo: Path) -> list[dict[str, Any]]:
    workbook = load_workbook(arquivo, data_only=True, read_only=False)
    abas = sorted(
        (nome for nome in workbook.sheetnames if _ABA_ICP.fullmatch(nome)),
        key=lambda nome: int(_ABA_ICP.fullmatch(nome).group(1)),  # type: ignore[union-attr]
    )
    if not abas:
        raise ValueError(
            "Nenhuma aba de amostra ICP_n encontrada na planilha "
            f"{arquivo.name!r}."
        )

    resultados: list[dict[str, Any]] = []
    for nome in abas:
        ws = workbook[nome]
        total = _numero(ws[_TOTAL_PLACAS].value) or _numero(
            ws[_PLACAS_CABECALHO].value
        )
        if total is None or total <= 0:
            raise ValueError(
                f"{nome}: número de placas inválido ({ws[_TOTAL_PLACAS].value!r})"
            )
        resultados.append(
            _resultado_amostra(
                amostra=nome,
                total_placas=total,
                defeitos=_defeitos_da_aba(ws, total, nome),
                metadados=_metadados_aba(ws),
            )
        )
    return resultados


def _normalizar_cabecalho(nome: str) -> str:
    sem_acento = (
        unicodedata.normalize("NFKD", nome or "")
        .encode("ascii", "ignore")
        .decode()
    )
    return re.sub(r"\s+", "_", sem_acento.strip().lower())


_ALIASES = {
    "amostra": ("amostra", "sample", "ficha"),
    "total_placas": ("total_placas", "placas", "n_placas"),
    "tipo": ("tipo", "tipo_defeito", "defeito", "codigo"),
    "severidade": ("severidade", "gravidade", "grau", "severidade_grau"),
    "placas_afetadas": (
        "placas_afetadas",
        "n_placas_afetadas",
        "numero_placas_afetadas",
        "quantidade",
    ),
    "densidade": ("densidade", "densidade_pct", "percentual", "percentual_placas"),
    "km": ("km",),
    "km_inicial": ("km_inicial",),
    "km_final": ("km_final",),
    "rodovia": ("rodovia",),
    "faixa": ("faixa",),
    "data": ("data",),
    "operador": ("operador",),
    "local": ("local",),
}


def _mapear_colunas(cabecalho: list[str]) -> dict[str, str]:
    normalizado = {_normalizar_cabecalho(nome): nome for nome in cabecalho}
    mapa: dict[str, str] = {}
    for campo, aliases in _ALIASES.items():
        for alias in aliases:
            if alias in normalizado:
                mapa[campo] = normalizado[alias]
                break
    return mapa


def _importar_csv(arquivo: Path) -> list[dict[str, Any]]:
    with arquivo.open(encoding="utf-8-sig", newline="") as handle:
        amostra_texto = handle.read(4096)
        handle.seek(0)
        try:
            dialeto = csv.Sniffer().sniff(amostra_texto, delimiters=",;")
        except csv.Error:
            dialeto = csv.excel
        leitor = csv.DictReader(handle, dialect=dialeto)
        if not leitor.fieldnames:
            raise ValueError("CSV sem cabeçalho.")
        colunas = _mapear_colunas(list(leitor.fieldnames))

        for obrigatoria in ("amostra", "total_placas", "tipo"):
            if obrigatoria not in colunas:
                raise ValueError(
                    f"CSV sem a coluna obrigatória {obrigatoria!r}. "
                    f"Colunas lidas: {leitor.fieldnames}"
                )

        grupos: OrderedDict[str, dict[str, Any]] = OrderedDict()
        for numero_linha, linha in enumerate(leitor, start=2):
            amostra = _texto(linha.get(colunas["amostra"]))
            if amostra is None:
                raise ValueError(f"Linha {numero_linha}: amostra vazia.")
            grupo = grupos.setdefault(
                amostra, {"linhas": [], "metadados": {}}
            )
            grupo["linhas"].append((numero_linha, linha))
            for campo in (
                "km",
                "km_inicial",
                "km_final",
                "rodovia",
                "faixa",
                "data",
                "operador",
                "local",
            ):
                if campo in colunas and campo not in grupo["metadados"]:
                    valor = _texto(linha.get(colunas[campo]))
                    if valor is not None:
                        grupo["metadados"][campo] = valor

    resultados: list[dict[str, Any]] = []
    for amostra, grupo in grupos.items():
        defeitos: list[dict[str, Any]] = []
        total: float | None = None
        for numero_linha, linha in grupo["linhas"]:
            total_linha = _numero(linha.get(colunas["total_placas"]))
            if total_linha is None or total_linha <= 0:
                raise ValueError(
                    f"Linha {numero_linha}: total_placas inválido "
                    f"({linha.get(colunas['total_placas'])!r})."
                )
            if total is None:
                total = total_linha
            elif abs(total - total_linha) > 1e-9:
                raise ValueError(
                    f"Amostra {amostra!r}: total_placas divergente entre linhas."
                )

            tipo = _inteiro(linha.get(colunas["tipo"]))
            if tipo is None or tipo not in DEFEITOS:
                raise ValueError(
                    f"Linha {numero_linha}: tipo de defeito inválido "
                    f"({linha.get(colunas['tipo'])!r})."
                )
            severidade = (
                _texto(linha.get(colunas["severidade"])) if "severidade" in colunas else None
            )
            if tipo in ITENS_SEM_SEVERIDADE:
                severidade = "U"
            elif severidade is None:
                raise ValueError(
                    f"Linha {numero_linha}: defeito {tipo} exige severidade A/M/B."
                )
            else:
                severidade = severidade.upper()

            densidade = (
                _numero(linha.get(colunas["densidade"]))
                if "densidade" in colunas
                else None
            )
            placas = (
                _numero(linha.get(colunas["placas_afetadas"]))
                if "placas_afetadas" in colunas
                else None
            )
            if densidade is None:
                if placas is None:
                    raise ValueError(
                        f"Linha {numero_linha}: informe placas_afetadas ou densidade."
                    )
                densidade = placas / total * 100.0
            elif placas is not None:
                densidade = placas / total * 100.0

            defeitos.append(
                {
                    "tipo": tipo,
                    "severidade": severidade,
                    "placas_afetadas": placas,
                    "densidade": densidade,
                }
            )

        assert total is not None
        metadados = dict(grupo["metadados"])
        if "km" in metadados and "km_inicial" not in metadados:
            metadados["km_inicial"] = metadados["km"]
        metadados["km_inicial"] = parse_km(metadados.get("km_inicial"))
        metadados["km_final"] = parse_km(metadados.get("km_final"))
        resultados.append(
            _resultado_amostra(
                amostra=amostra,
                total_placas=total,
                defeitos=defeitos,
                metadados=metadados,
            )
        )
    return resultados


def importar_icp(arquivo: Path | str) -> list[dict[str, Any]]:
    """Importa uma ficha de ICP e devolve uma amostra calculada por aba/grupo."""
    caminho = Path(arquivo)
    extensao = caminho.suffix.lower()
    if extensao in {".xlsx", ".xlsm"}:
        return _importar_xlsx(caminho)
    if extensao == ".csv":
        return _importar_csv(caminho)
    if extensao == ".xls":
        raise ValueError(
            "Formato .xls não suportado; salve a planilha como .xlsx e reenvie."
        )
    raise ValueError(
        f"Formato não suportado: {extensao or '(sem extensão)'}. "
        "Use .xlsx, .xlsm ou .csv."
    )
