from __future__ import annotations

from pathlib import Path

import pandas as pd


def encontrar_tabela(arquivo: Path, aba: str | int = 0) -> pd.DataFrame:
    """Localiza a tabela de dados na planilha procurando por 'Latitude' e 'Longitude'."""
    df_raw = pd.read_excel(arquivo, sheet_name=aba, header=None)

    for linha in range(len(df_raw)):
        valores = (
            df_raw.iloc[linha]
            .fillna("")
            .astype(str)
            .str.strip()
            .str.lower()
        )
        if {"latitude", "longitude"}.issubset(set(valores)):
            inicio = valores[valores != ""].index.min()
            tabela = df_raw.iloc[linha:, inicio:].copy()
            tabela.columns = tabela.iloc[0]
            tabela = tabela.iloc[1:].reset_index(drop=True)
            tabela = tabela.dropna(axis=0, how="all")
            tabela = tabela.dropna(axis=1, how="all")
            return tabela

    raise ValueError("Tabela contendo Latitude e Longitude não encontrada.")


def extrair_tri_tre(
    tabela: pd.DataFrame,
    tipo_pista: str = "simples",
    faixa: int | None = None,
) -> tuple[list[float], list[float], list[float]]:
    """Extrai KM, TRI e TRE da planilha, convertendo ATR conforme tipo de pista e faixa.

    Regra:
      - Pista simples ou dupla faixa 2: ATR Esq → TRI, ATR Dir → TRE
      - Pista dupla faixa 1:           ATR Esq → TRE, ATR Dir → TRI
    """
    km_values: list[float] = []
    atr_esq_vals: list[float] = []
    atr_dir_vals: list[float] = []

    km_col = None
    esq_col = None
    dir_col = None

    for col in tabela.columns:
        nome = str(col).lower().strip()
        if km_col is None and nome.startswith("inicio"):
            km_col = col
        if esq_col is None and (nome.startswith("atr esq") or nome == "tri"):
            esq_col = col
        if dir_col is None and (nome.startswith("atr dir") or nome == "tre"):
            dir_col = col

    if not esq_col or not dir_col:
        disponiveis = [str(c) for c in tabela.columns]
        raise ValueError(
            f"Colunas ATR não encontradas. Disponíveis: {disponiveis}"
        )

    for _, row in tabela.iterrows():
        try:
            km_val = float(row.get(km_col, 0))
            esq_val = float(row.get(esq_col, 0))
            dir_val = float(row.get(dir_col, 0))
        except (ValueError, TypeError):
            continue
        km_values.append(km_val)
        atr_esq_vals.append(esq_val)
        atr_dir_vals.append(dir_val)

    # Aplica inversão se necessário
    invertido = tipo_pista == "dupla" and faixa == 1
    if invertido:
        return km_values, atr_dir_vals, atr_esq_vals
    return km_values, atr_esq_vals, atr_dir_vals


def agrupar_por_km(
    km_values: list[float],
    tri_vals: list[float],
    tre_vals: list[float],
) -> dict[str, dict[str, list[str]]]:
    """Agrupa valores por KM inteiro."""
    kms: dict[str, dict[str, list[str]]] = {}

    for km_raw, tri, tre in zip(km_values, tri_vals, tre_vals):
        km_key = str(int(km_raw))
        if km_key not in kms:
            kms[km_key] = {"TRI (mm)": [], "TRE (mm)": []}
        kms[km_key]["TRI (mm)"].append(f"{tri:.1f}")
        kms[km_key]["TRE (mm)"].append(f"{tre:.1f}")

    return kms


def importar_planilha(
    arquivo: Path,
    tipo_pista: str = "simples",
    faixa: int | None = None,
) -> dict[str, dict[str, list[str]]]:
    """Fluxo completo: encontra tabela, extrai TRI/TRE, agrupa por KM."""
    tabela = encontrar_tabela(arquivo)
    km_values, tri_vals, tre_vals = extrair_tri_tre(tabela, tipo_pista, faixa)
    return agrupar_por_km(km_values, tri_vals, tre_vals)
