from __future__ import annotations

TIPOS_PISTA_VALIDOS = ("simples", "dupla")

# Vocabulário legado do frontend e de `viagem_config.json` antigos.
_ALIASES = {"duplicada": "dupla"}


def normalizar_tipo_pista(valor: str | None) -> str:
    """Normaliza o tipo de pista para o vocabulário canônico (``simples``/``dupla``).

    Aceita o valor legado ``duplicada`` e o traduz para ``dupla``. Um valor
    omitido equivale a ``simples``. Qualquer outro valor levanta ``ValueError``.
    """
    if valor is None:
        return "simples"
    chave = str(valor).strip().lower()
    chave = _ALIASES.get(chave, chave)
    if chave not in TIPOS_PISTA_VALIDOS:
        aceitos = ", ".join(TIPOS_PISTA_VALIDOS)
        raise ValueError(f"tipo_pista inválido: {valor!r}. Valores aceitos: {aceitos}.")
    return chave
