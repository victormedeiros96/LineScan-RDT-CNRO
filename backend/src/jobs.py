from __future__ import annotations

import shutil
from pathlib import Path

from rq import get_current_job

from src.core.config import get_settings
from src.services.image_processor import processar_pasta, LARGURA_PX
from src.services.inference_pipeline import InferencePipeline
from src.services.model_loader import listar_modelos


def _set_progress(current: int, total: int, message: str) -> None:
    job = get_current_job()
    if job is not None:
        meta = job.get_meta()
        meta["current_lote"] = current
        meta["total_lotes"] = total
        meta["progress_msg"] = message
        job.meta = meta
        job.save()


def processar_imagens_job(
    pasta_origem: str,
    viagem_nome: str,
    km_inicial: float | None = None,
    km_final: float | None = None,
    tipo_pista: str = "simples",
    sentido: str = "crescente",
    faixa: int | None = None,
) -> dict:
    import json as _json
    settings = get_settings()
    origem = Path(pasta_origem)
    viagem_nome = viagem_nome.strip()
    destino = settings.dados_dir / viagem_nome

    config = {
        "nome": viagem_nome,
        "km_inicial": km_inicial,
        "km_final": km_final,
        "tipo_pista": tipo_pista,
        "sentido": sentido,
        "faixa": faixa,
    }
    destino.mkdir(parents=True, exist_ok=True)
    (destino / "viagem_config.json").write_text(_json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")

    max_batches = None
    if km_inicial is not None and km_final is not None:
        distancia_km = abs(float(km_final) - float(km_inicial))
        distancia_m = distancia_km * 1000
        max_batches = max(1, int(distancia_m // 20))

    lotes = processar_pasta(
        origem, destino,
        km_inicial=km_inicial,
        sentido=sentido,
        tipo_pista=tipo_pista,
        faixa=faixa,
        max_batches=max_batches,
    )

    return {
        "total_lotes": len(lotes),
        "lotes": lotes,
        "destino": str(destino),
    }


def processar_inferencia_job(
    viagem_nome: str,
    tipo_modelo: str = "igg",
    tipo_pista: str = "simples",
    sentido: str = "crescente",
    faixa: int | None = None,
) -> dict:
    settings = get_settings()
    viagem_nome = viagem_nome.strip()
    destino = settings.dados_dir / viagem_nome
    modelos = listar_modelos(settings.modelos_dir)
    modelo_info = next((m for m in modelos if m.tipo == tipo_modelo), None)
    if modelo_info is None:
        raise ValueError(f"Modelo '{tipo_modelo}' não encontrado")

    cfg = modelo_info.config
    pipeline = InferencePipeline(
        modelos_dir=modelo_info.pasta,
        input_folder=destino,
        output_folder=destino,
        sub_modelos=[m.model_dump() for m in cfg.modelos] if cfg.modelos else [],
        area_minima=cfg.area_minima,
    )
    resultado = pipeline.run(
        tipo_pista=tipo_pista,
        sentido=sentido,
        faixa=faixa,
        progress_callback=_set_progress,
    )

    return {
        "viagem": viagem_nome,
        "total_imagens": len(resultado),
        "arquivo_saida": str(destino / "analise_completa.json"),
    }


def extrair_zip_job(viagem: str, zip_path: str) -> dict:
    """Extrai um ZIP enviado por upload para dados/uploads/<viagem>/ de forma segura."""
    import zipfile

    from src.api.routes_uploads import ALLOWED_EXTS, EXTENSOES_IMAGEM

    settings = get_settings()
    zip_file = Path(zip_path)
    origem = zip_file.parent
    destino = origem
    if zip_file.parent.parent.name == "uploads":
        destino = origem

    destino.mkdir(parents=True, exist_ok=True)

    total_extraidos = 0
    ignorados: list[str] = []
    bloqueados = 0

    with zipfile.ZipFile(zip_file) as zf:
        for info in zf.infolist():
            nome = Path(info.filename).name
            if not nome:
                continue
            suffix = Path(nome).suffix.lower()
            if info.is_dir() or suffix not in EXTENSOES_IMAGEM or info.filename.startswith((".", "__")):
                continue
            if suffix not in ALLOWED_EXTS:
                bloqueados += 1
                continue
            if info.file_size > 200 * 1024 * 1024:
                ignorados.append(nome)
                continue
            alvo = destino / nome
            if alvo.exists():
                alvo.unlink()
            with zf.open(info) as src, alvo.open("wb") as out:
                shutil.copyfileobj(src, out)
            total_extraidos += 1

    zip_file.unlink(missing_ok=True)

    return {
        "viagem": viagem,
        "total_imagens": total_extraidos,
        "ignorados": ignorados[:100],
        "bloqueados": bloqueados,
        "pasta": str(destino),
    }
