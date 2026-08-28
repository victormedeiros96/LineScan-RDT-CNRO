from __future__ import annotations

from pydantic import BaseModel, Field


class PathologyDetection(BaseModel):
    classe: str = Field(..., description="Nome da classe (ex: trinca, panela, remendo)")
    confidence: float = Field(..., ge=0.0, le=1.0)
    area_pixels: int = Field(..., ge=0)
    area_m2: float | None = None
    bbox: list[float] | None = None
    polygon: list[list[float]] | None = None
    linha: int | None = Field(default=None, ge=0, le=19)
    coluna: int | None = Field(default=None, ge=0, le=2)


class ImageDetection(BaseModel):
    arquivo_imagem: str
    km: float
    quadrante: int
    faixa: str | None = None
    deteccoes: list[PathologyDetection] = Field(default_factory=list)


class AnalysisResult(BaseModel):
    projeto: str
    modelo: str
    imagens: list[ImageDetection] = Field(default_factory=list)
