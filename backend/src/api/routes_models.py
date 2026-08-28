from fastapi import APIRouter, Depends

from src.core.config import Settings
from src.core.dependencies import get_settings_dep
from src.models.project import ModeloInfo
from src.services.model_loader import listar_modelos

router = APIRouter(prefix="/api/modelos", tags=["modelos"])


@router.get("")
async def listar_modelos_endpoint(settings: Settings = Depends(get_settings_dep)) -> list[ModeloInfo]:
    return listar_modelos(settings.modelos_dir)


@router.get("/{tipo}")
async def obter_modelo_por_tipo(tipo: str, settings: Settings = Depends(get_settings_dep)) -> ModeloInfo | None:
    modelos = listar_modelos(settings.modelos_dir)
    for m in modelos:
        if m.tipo == tipo:
            return m
    return None
