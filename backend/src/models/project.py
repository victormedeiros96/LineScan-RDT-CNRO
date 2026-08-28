from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field


class SubModelo(BaseModel):
    nome: str = Field(..., description="Nome interno do sub-modelo (ex: trincas, panelas)")
    arquivo: str = Field(..., description="Nome do arquivo .onnx")
    classes: dict[int, str] = Field(..., description="Mapping id -> nome da classe")
    janela: list[int] = Field(default=[1024, 1024], description="Tamanho da janela [largura, altura] em pixels")
    stride: list[int] = Field(default=[512, 512], description="Passo do sliding [x, y] em pixels")


class ModelConfig(BaseModel):
    nome: str = Field(..., description="Nome de exibição do modelo na interface")
    arquivo: str = Field(default="", description="Nome do arquivo .onnx (para modelo único)")
    classes: dict[int, str] = Field(default_factory=dict, description="Mapping id -> nome da classe")
    modelos: list[SubModelo] = Field(default_factory=list, description="Sub-modelos (para multi-modelo tipo IGG)")
    cores: dict[str, str] = Field(default_factory=dict, description="Cor hex por classe")
    input_shape: list[int] = Field(default=[1, 3, 640, 640])
    area_minima: dict[str, int] = Field(default_factory=dict, description="Área mínima em pixels por classe")


class ModeloInfo(BaseModel):
    tipo: str  # "igg" ou "icp"
    pasta: Path
    config: ModelConfig


class Projeto(BaseModel):
    nome: str
    pasta: Path
    modelos_selecionados: list[str] = Field(default_factory=list)
