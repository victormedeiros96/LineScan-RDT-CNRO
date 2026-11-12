"""Construtor de planilhas ICP no formato da concessionária para os testes."""

from __future__ import annotations

from io import BytesIO
from typing import Any

from openpyxl import Workbook

SLOTS = [(linha, coluna) for linha in range(7, 55) for coluna in (3, 4, 5, 9, 10, 11)]


def _tabela_defeitos(ws: Any) -> None:
    linha = 19
    for tipo in range(1, 19):
        if tipo in {10, 11, 15}:
            ws.cell(linha, 12, tipo)
            ws.cell(linha, 15, "-")
            linha += 1
            continue
        for severidade in ("A", "M", "B"):
            ws.cell(linha, 12, tipo)
            ws.cell(linha, 15, severidade)
            linha += 1


def _codigos_para_defeitos(defeitos: list[list[Any]], total: float) -> list[Any]:
    codigos: list[Any] = []
    for tipo, severidade, densidade in defeitos:
        quantidade = round(float(densidade) / 100.0 * total)
        codigo: Any = int(tipo) if int(tipo) in {10, 11, 15} else f"{tipo}{severidade}"
        codigos.extend([codigo] * quantidade)
    return codigos


def montar_workbook(
    amostras: list[dict[str, Any]],
    *,
    rodovia: str = "BR-163/MT",
    km_inicial: str = "33+600",
    km_final: str = "33+700",
    pedagio: int | str = 1,
) -> bytes:
    """Monta um XLSX com uma aba ICP_n por amostra.

    Cada amostra é ``{"amostra": "ICP_1", "total_placas": 12, "defeitos":
    [[tipo, severidade, densidade], ...]}``. Os defeitos são gravados como
    códigos C/I; use ``entradas_manuais`` para testar o caminho R/U.
    """
    wb = Workbook()
    wb.remove(wb.active)
    wb.create_sheet("lista")

    for amostra in amostras:
        ws = wb.create_sheet(amostra["amostra"])
        total = float(amostra["total_placas"])
        ws["C3"] = rodovia
        ws["I3"] = km_inicial
        ws["I4"] = km_final
        ws["O3"] = pedagio
        ws["L4"] = amostra.get("faixa", "Carga Esp. 1")
        ws["C4"] = "Maio/2025"
        ws["W3"] = "Operador Teste"
        ws["W4"] = total
        ws["C60"] = total
        _tabela_defeitos(ws)

        if "entradas_manuais" in amostra:
            for tipo, severidade, placas in amostra["entradas_manuais"]:
                linha = _linha_da_entrada(ws, int(tipo), str(severidade))
                ws.cell(linha, 18, float(placas))
                ws.cell(linha, 21, float(placas) / total * 100.0)
            continue

        codigos = _codigos_para_defeitos(amostra.get("defeitos", []), total)
        if len(codigos) > len(SLOTS):
            raise ValueError("amostra tem mais códigos do que células disponíveis")
        for (linha, coluna), codigo in zip(SLOTS, codigos):
            ws.cell(linha, coluna, codigo)

    buffer = BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


def _linha_da_entrada(ws: Any, tipo: int, severidade: str) -> int:
    for linha in range(19, 67):
        if ws.cell(linha, 12).value == tipo and ws.cell(linha, 15).value == severidade:
            return linha
    raise ValueError(f"linha não encontrada para {tipo}/{severidade}")
