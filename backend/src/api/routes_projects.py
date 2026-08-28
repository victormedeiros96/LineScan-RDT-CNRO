from fastapi import APIRouter

router = APIRouter(prefix="/api/projetos", tags=["projetos"])


@router.get("/")
async def listar_projetos():
    return {"projetos": []}


@router.get("/{projeto_id}")
async def obter_projeto(projeto_id: str):
    return {"id": projeto_id, "nome": "", "modelos": []}


@router.delete("/{projeto_id}")
async def remover_projeto(projeto_id: str):
    return {"ok": True}
