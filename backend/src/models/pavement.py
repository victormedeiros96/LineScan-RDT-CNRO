from __future__ import annotations

from pydantic import BaseModel, Field


class EstacaoData(BaseModel):
    numero: int = Field(..., ge=1, le=25)
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
    total_estacoes: int = 25
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
