from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.api import folders_router, models_router, projects_router, reports_router, debug_router, uploads_router
from src.core.config import get_settings

settings = get_settings()

app = FastAPI(
    title="RDT01 - Análise de Patologias de Pavimento",
    version="0.1.0",
    description="API para geração de relatórios e exportação de dados de patologias asfálticas",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(folders_router)
app.include_router(models_router)
app.include_router(projects_router)
app.include_router(reports_router)
app.include_router(debug_router)
app.include_router(uploads_router)


@app.get("/health")
async def health_check():
    return {"status": "ok", "version": "0.1.0"}
