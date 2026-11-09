from __future__ import annotations

import pandas as pd
import pytest

from src.services.image_processor import deve_pular_bloco
from src.services.planilha_import import extrair_tri_tre
from src.utils.tipo_pista import normalizar_tipo_pista


def _tabela() -> pd.DataFrame:
    """Tabela mínima com uma linha, no formato esperado por extrair_tri_tre."""
    return pd.DataFrame(
        {
            "Inicio (km)": [1.0],
            "ATR Esq": [10.0],
            "ATR Dir": [20.0],
        }
    )


# ── Vocabulário canônico ──


def test_normalizar_aceita_simples() -> None:
    assert normalizar_tipo_pista("simples") == "simples"


def test_normalizar_aceita_dupla() -> None:
    assert normalizar_tipo_pista("dupla") == "dupla"


def test_normalizar_converte_legado_duplicada() -> None:
    assert normalizar_tipo_pista("duplicada") == "dupla"


def test_normalizar_omisso_equivale_a_simples() -> None:
    assert normalizar_tipo_pista(None) == "simples"


def test_normalizar_rejeita_valor_invalido() -> None:
    with pytest.raises(ValueError):
        normalizar_tipo_pista("tripla")


# ── Amostragem (image_processor) ──


def test_amostragem_simples_pula_blocos_impares() -> None:
    assert deve_pular_bloco("simples", None) is True
    assert deve_pular_bloco("simples", 1) is True


def test_amostragem_dupla_faixa_1_pula_blocos_impares() -> None:
    assert deve_pular_bloco("dupla", 1) is True


def test_amostragem_dupla_faixa_2_processa_todos() -> None:
    assert deve_pular_bloco("dupla", 2) is False


# ── Inversão TRI/TRE na importação da planilha ──


def test_extrair_tri_tre_simples_nao_inverte() -> None:
    _, tri, tre = extrair_tri_tre(_tabela(), "simples", None)
    assert tri == [10.0]
    assert tre == [20.0]


def test_extrair_tri_tre_dupla_faixa_1_inverte() -> None:
    _, tri, tre = extrair_tri_tre(_tabela(), "dupla", 1)
    assert tri == [20.0]
    assert tre == [10.0]


def test_extrair_tri_tre_dupla_faixa_2_nao_inverte() -> None:
    _, tri, tre = extrair_tri_tre(_tabela(), "dupla", 2)
    assert tri == [10.0]
    assert tre == [20.0]


def test_extrair_tri_tre_aceita_legado_duplicada_faixa_1() -> None:
    _, tri, tre = extrair_tri_tre(_tabela(), "duplicada", 1)
    assert tri == [20.0]
    assert tre == [10.0]
