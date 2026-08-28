from __future__ import annotations

import hashlib
import shutil
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from pydantic import BaseModel
from rq import Queue

from src.core.config import Settings
from src.core.dependencies import get_settings_dep
from src.api.routes_folders import JOBS_LIST_KEY, JOBS_LIST_MAX
from src.api.routes_folders import _get_queue as _get_uploads_queue  # reuse padrão de fila/redis

router = APIRouter(prefix="/api/uploads", tags=["uploads"])

EXTENSOES_IMAGEM = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff"}
EXTENSAO_ZIP = ".zip"
ALLOWED_EXTS = EXTENSOES_IMAGEM | {EXTENSAO_ZIP}
CHUNK_SIZE = 10 * 1024 * 1024  # 10 MB por parte (seguro para proxy com limite de 100 MB/timeout 100s)
JOB_RESULT_TTL_SECONDS = -1  # manter jobs para sempre no histórico

UPLOADS_DIR_NAME = "uploads"


def _uploads_dir(settings: Settings, viagem: str) -> Path:
    nome = Path(viagem).name.strip().replace("/", "_").replace("\\", "_")
    if not nome:
        raise HTTPException(400, "Nome da viagem inválido")
    return settings.dados_dir / UPLOADS_DIR_NAME / nome


def _sessao_dir(settings: Settings, upload_id: str) -> Path:
    return settings.dados_dir / UPLOADS_DIR_NAME / ".sessoes" / upload_id


def _nome_arquivo_seguro(nome: str) -> str:
    base = Path(nome).name.strip()
    if not base or base in {".", ".."}:
        raise HTTPException(400, f"Nome de arquivo inválido: {nome!r}")
    return base


class SessaoCriar(BaseModel):
    viagem: str
    total_arquivos: int = 0


class ArquivoFinalizado(BaseModel):
    nome: str
    tamanho: int


class SessaoFinalizar(BaseModel):
    arquivos: list[ArquivoFinalizado]


@router.post("/zip")
async def enviar_zip(
    viagem: str = Form(...),
    arquivo: UploadFile = File(...),
    settings: Settings = Depends(get_settings_dep),
):
    """Envia um ZIP para extração em background. Retorna o job_id."""
    if not arquivo.filename or not arquivo.filename.lower().endswith(".zip"):
        raise HTTPException(400, "O arquivo deve ser um ZIP (.zip)")

    dest_dir = _uploads_dir(settings, viagem)
    dest_dir.mkdir(parents=True, exist_ok=True)

    zip_path = dest_dir / f"upload_{uuid.uuid4().hex}.zip"
    tmp = zip_path.with_suffix(".part")
    try:
        with tmp.open("wb") as out:
            while chunk := await arquivo.read(CHUNK_SIZE * 2):
                out.write(chunk)
        tmp.rename(zip_path)
    except Exception as e:
        tmp.unlink(missing_ok=True)
        raise HTTPException(500, f"Erro ao salvar ZIP: {e}")

    queue = _get_uploads_queue(settings)
    job = queue.enqueue(
        "src.jobs.extrair_zip_job",
        viagem=sanitize_viagem(viagem),
        zip_path=str(zip_path),
        job_timeout=7200,
        result_ttl=JOB_RESULT_TTL_SECONDS,
        failure_ttl=JOB_RESULT_TTL_SECONDS,
        meta={"tipo": "upload_zip", "viagem": sanitize_viagem(viagem)},
    )

    from redis import Redis as _Redis
    _Redis.from_url(settings.redis_url).lpush(JOBS_LIST_KEY, job.id)
    _Redis.from_url(settings.redis_url).ltrim(JOBS_LIST_KEY, 0, JOBS_LIST_MAX - 1)

    return {"status": "enviado", "job_id": job.id, "zip_path": str(zip_path)}


@router.post("/sessao")
async def criar_sessao(
    body: SessaoCriar,
    settings: Settings = Depends(get_settings_dep),
):
    """Cria uma sessão de upload chunked. Retorna upload_id e tamanho do chunk."""
    sanitize_viagem(body.viagem)
    upload_id = uuid.uuid4().hex
    sessao = _sessao_dir(settings, upload_id)
    sessao.mkdir(parents=True, exist_ok=True)
    (sessao / "viagem.txt").write_text(sanitize_viagem(body.viagem), encoding="utf-8")
    return {"upload_id": upload_id, "chunk_size": CHUNK_SIZE}


@router.post("/sessao/{upload_id}/chunk")
async def enviar_chunk(
    upload_id: str,
    nome: str = Form(...),
    indice: int = Form(...),
    parte: UploadFile = File(...),
    settings: Settings = Depends(get_settings_dep),
):
    """Envia um chunk de um arquivo. O chunk é salvo separado; a concatenação ocorre no finalizar."""
    sessao = _sessao_dir(settings, upload_id)
    if not sessao.is_dir():
        raise HTTPException(404, "Sessão não encontrada ou expirada")

    nome_seguro = _nome_arquivo_seguro(nome)
    if Path(nome_seguro).suffix.lower() not in ALLOWED_EXTS:
        raise HTTPException(400, f"Extensão não permitida: {nome_seguro}")

    chunk = await parte.read(CHUNK_SIZE * 2)
    if parte.size is not None and parte.size > CHUNK_SIZE * 2:
        raise HTTPException(413, "Chunk maior que o permitido")

    chunk_path = sessao / f"{hashlib.md5(nome_seguro.encode()).hexdigest()}_{indice:08d}.part"
    if chunk_path.exists():
        chunk_path.unlink()
    chunk_path.write_bytes(chunk)

    return {"ok": True, "indice": indice, "bytes": len(chunk)}


@router.post("/sessao/{upload_id}/finalizar")
async def finalizar_sessao(
    upload_id: str,
    body: SessaoFinalizar,
    settings: Settings = Depends(get_settings_dep),
):
    """Concatena os chunks de cada arquivo, valida e move para dados/uploads/<viagem>/."""
    sessao = _sessao_dir(settings, upload_id)
    if not sessao.is_dir():
        raise HTTPException(404, "Sessão não encontrada ou expirada")

    viagem = sessao.joinpath("viagem.txt").read_text(encoding="utf-8").strip()
    dest_dir = _uploads_dir(settings, viagem)
    dest_dir.mkdir(parents=True, exist_ok=True)

    total_imagens = 0
    zips_recebidos: list[Path] = []
    erros: list[str] = []

    for item in body.arquivos:
        nome_seguro = _nome_arquivo_seguro(item.nome)
        if Path(nome_seguro).suffix.lower() not in ALLOWED_EXTS:
            erros.append(f"{nome_seguro}: extensão não permitida")
            continue

        prefixo = hashlib.md5(nome_seguro.encode()).hexdigest()
        partes = sorted(sessao.glob(f"{prefixo}_*.part"))
        if not partes:
            erros.append(f"{nome_seguro}: nenhum chunk recebido")
            continue

        final = dest_dir / nome_seguro
        with final.open("wb") as out:
            for parte in partes:
                out.write(parte.read_bytes())
                parte.unlink()

        if item.tamanho > 0 and final.stat().st_size != item.tamanho:
            final.unlink(missing_ok=True)
            erros.append(f"{nome_seguro}: tamanho final {final.stat().st_size if final.exists() else 0} != {item.tamanho}")
            continue

        if Path(nome_seguro).suffix.lower() == EXTENSAO_ZIP:
            zips_recebidos.append(final)
        else:
            total_imagens += 1

    shutil.rmtree(sessao, ignore_errors=True)

    if erros:
        raise HTTPException(400, {"erros": erros, "total_imagens": total_imagens})

    jobs: list[dict] = []
    if zips_recebidos:
        queue = _get_uploads_queue(settings)
        job = queue.enqueue(
            "src.jobs.extrair_zip_job",
            viagem=viagem,
            zip_path=str(zips_recebidos[0]),
            job_timeout=7200,
            result_ttl=JOB_RESULT_TTL_SECONDS,
            failure_ttl=JOB_RESULT_TTL_SECONDS,
            meta={"tipo": "upload_zip", "viagem": viagem},
        )
        from redis import Redis as _Redis
        _Redis.from_url(settings.redis_url).lpush(JOBS_LIST_KEY, job.id)
        _Redis.from_url(settings.redis_url).ltrim(JOBS_LIST_KEY, 0, JOBS_LIST_MAX - 1)
        jobs.append({"arquivo": zips_recebidos[0].name, "job_id": job.id})

    return {
        "status": "ok",
        "viagem": viagem,
        "pasta": str(dest_dir),
        "total_imagens": total_imagens,
        "jobs": jobs,
    }


@router.delete("/sessao/{upload_id}")
async def cancelar_sessao(
    upload_id: str,
    settings: Settings = Depends(get_settings_dep),
):
    sessao = _sessao_dir(settings, upload_id)
    shutil.rmtree(sessao, ignore_errors=True)
    return {"status": "cancelado"}


@router.get("/listar")
async def listar_uploads(settings: Settings = Depends(get_settings_dep)):
    """Lista pastas enviadas por upload (prontas para processamento)."""
    base = settings.dados_dir / UPLOADS_DIR_NAME
    if not base.is_dir():
        return {"uploads": []}

    uploads: list[dict] = []
    for pasta in sorted(base.iterdir(), key=lambda p: p.name.lower()):
        if not pasta.is_dir() or pasta.name.startswith("."):
            continue
        imagens = [
            e for e in pasta.iterdir()
            if e.is_file() and e.suffix.lower() in EXTENSOES_IMAGEM
        ]
        if not imagens:
            continue
        uploads.append({
            "viagem": pasta.name,
            "pasta": str(pasta),
            "total_imagens": len(imagens),
            "tamanho_bytes": sum(e.stat().st_size for e in imagens),
        })
    return {"uploads": uploads}


def sanitize_viagem(nome: str) -> str:
    limpo = Path(nome).name.strip().replace("/", "_").replace("\\", "_")
    if not limpo:
        raise HTTPException(400, "Nome da viagem inválido")
    return limpo