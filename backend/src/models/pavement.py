from __future__ import annotations

from pydantic import BaseModel, Field


class EstacaoData(BaseModel):
    # n_estacoes é 25 (modo de 40 m) ou 50 (modo de 20 m) — ver
    # `image_processor.parametros_amostragem`. O limite reflete o máximo.
    numero: int = Field(..., ge=1, le=50)
    km: float
    fc1: int = 0
    fc1_cons: int = 0
    fc2: int = 0
    fc2_cons: int = 0
    fc3: int = 0
    atp_alp: int = 0
    ope: int = 0
    ex: int = 0
    d: int = 0
    r: int = 0
    tri_mm: float | None = None
    tre_mm: float | None = None


class IGGResult(BaseModel):
    km_inicial: float
    km_final: float
    # Varia com o modo de amostragem (25 ou 50); 50 é apenas o valor padrão do modo de 20 m.
    total_estacoes: int = 50
    igg: float = 0.0
    conceito: str = ""
    estacoes: list[EstacaoData] = Field(default_factory=list)


class RetigraficoParams(BaseModel):
    km_inicial: float
    km_final: float
    sentido_negativo: bool = False
    base_imagens_url: str = ""
    rodovia: str = ""
    data: str = ""
