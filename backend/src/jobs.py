from __future__ import annotations

import logging
import shutil
from pathlib import Path

from rq import get_current_job
from sqlalchemy.exc import SQLAlchemyError

from src.core.config import get_settings
from src.services import icp_juntas, icp_placas, job_store, viagem_store
from src.services.image_processor import processar_pasta, LARGURA_PX
from src.services.inference_pipeline import InferencePipeline
from src.services.model_loader import listar_modelos
from src.utils.tipo_pista import normalizar_tipo_pista

TIPOS_ANALISE = ("igg", "icp")

logger = logging.getLogger(__name__)


def _set_progress(current: int, total: int, message: str) -> None:
    job = get_current_job()
    if job is not None:
        meta = job.get_meta()
        meta["current_lote"] = current
        meta["total_lotes"] = total
        meta["progress_msg"] = message
        job.meta = meta
        job.save()
        job_store.atualizar_progresso(
            job.id,
            {
                "current_lote": current,
                "total_lotes": total,
                "progress_msg": message,
            },
        )


def _executar_job(func, *args, **kwargs):
    """Executa o job registrando o ciclo de vida no banco (RDT1-13)."""
    job = get_current_job()
    rq_job_id = job.id if job is not None else None
    job_store.marcar_iniciado(rq_job_id)
    try:
        resultado = func(*args, **kwargs)
    except Exception as exc:
        job_store.falhar(rq_job_id, f"{type(exc).__name__}: {exc}")
        raise
    job_store.finalizar(rq_job_id, resultado if isinstance(resultado, dict) else {"resultado": resultado})
    job_store.limpar_jobs_antigos()
    return resultado


def processar_imagens_job(
    pasta_origem: str,
    viagem_nome: str,
    km_inicial: float | None = None,
    km_final: float | None = None,
    tipo_pista: str = "simples",
    sentido: str = "crescente",
    faixa: int | None = None,
    tipo_analise: str = "igg",
    juntas: dict | None = None,
) -> dict:
    return _executar_job(
        _processar_imagens,
        pasta_origem=pasta_origem,
        viagem_nome=viagem_nome,
        km_inicial=km_inicial,
        km_final=km_final,
        tipo_pista=tipo_pista,
        sentido=sentido,
        faixa=faixa,
        tipo_analise=tipo_analise,
        juntas=juntas,
    )


def _processar_imagens(
    pasta_origem: str,
    viagem_nome: str,
    km_inicial: float | None = None,
    km_final: float | None = None,
    tipo_pista: str = "simples",
    sentido: str = "crescente",
    faixa: int | None = None,
    tipo_analise: str = "igg",
    juntas: dict | None = None,
) -> dict:
    import json as _json
    settings = get_settings()
    origem = Path(pasta_origem)
    viagem_nome = viagem_nome.strip()
    destino = settings.dados_dir / viagem_nome
    tipo_pista = normalizar_tipo_pista(tipo_pista)

    if tipo_analise not in TIPOS_ANALISE:
        raise ValueError(
            f"tipo_analise inválido: {tipo_analise!r} (use {' ou '.join(TIPOS_ANALISE)})"
        )
    if tipo_analise == "icp" and km_inicial is None:
        raise ValueError(
            "O modo ICP exige 'km_inicial' para posicionar as placas no trecho."
        )

    config = {
        "nome": viagem_nome,
        "km_inicial": km_inicial,
        "km_final": km_final,
        "tipo_pista": tipo_pista,
        "sentido": sentido,
        "faixa": faixa,
        "tipo_analise": tipo_analise,
    }
    if juntas:
        config["juntas"] = juntas
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
        todas_faixas=(tipo_analise == "icp"),
    )

    resultado: dict = {
        "total_lotes": len(lotes),
        "lotes": lotes,
        "destino": str(destino),
        "tipo_analise": tipo_analise,
    }
    if tipo_analise == "icp":
        resultado.update(_processar_placas_icp(destino, settings, juntas))

    # Grava config/blocos/faixas no banco em uma única transação. Durante a
    # transição, uma falha aqui não invalida a concatenação: os JSONs legados
    # (viagem_config.json e lote_*_meta.json) continuam sendo a fonte de fallback.
    try:
        viagem_store.persistir_viagem(
            viagem_nome=viagem_nome,
            pasta=destino.name,
            config=config,
            lotes=viagem_store.coletar_metas(destino),
        )
    except (RuntimeError, SQLAlchemyError) as exc:
        logger.warning(
            "Falha ao persistir a viagem %s no banco; mantendo o JSON legado: %s",
            viagem_nome,
            exc,
        )

    return resultado


def _processar_placas_icp(
    destino: Path,
    settings,
    juntas: dict | None = None,
) -> dict:
    """Calibra as juntas e materializa placas/amostras/ROIs do trecho (ICP)."""
    faixas = icp_placas.listar_faixas(destino)
    if not faixas:
        raise ValueError(
            "Nenhuma faixa com KM foi gerada; confira 'km_inicial' e as imagens."
        )

    detector = icp_juntas.carregar_config(
        settings.modelos_dir,
        nuclio_dir=settings.icp_nuclio_dir,
        modelo_pt=settings.icp_modelo_pt,
        limiar=settings.icp_limiar_juntas,
        ordem_invertida=settings.icp_ordem_invertida,
        mm_por_px_y=settings.icp_mm_por_px_y,
    )
    calibracao = icp_juntas.calibrar_viagem(
        destino, faixas, config=juntas, detector=detector
    )
    inventario = icp_placas.montar_inventario(
        faixas,
        periodo_m=float(calibracao["periodo_m"]),
        fase_m=float(calibracao.get("fase_m") or 0.0),
        calibracao=calibracao,
    )
    total_rois = icp_placas.salvar_rois(destino, faixas, inventario["placas"])
    icp_placas.salvar_inventario(destino, inventario)
    icp_juntas.salvar_calibracao(destino, calibracao)

    return {
        "total_faixas": len(faixas),
        "total_placas": len(inventario["placas"]),
        "total_amostras": len(inventario["amostras"]),
        "total_rois": total_rois,
        "placas_descartadas": len(inventario.get("descartadas") or []),
        "calibracao": calibracao,
    }


def processar_inferencia_job(
    viagem_nome: str,
    tipo_modelo: str = "igg",
    tipo_pista: str = "simples",
    sentido: str = "crescente",
    faixa: int | None = None,
) -> dict:
    return _executar_job(
        _processar_inferencia,
        viagem_nome=viagem_nome,
        tipo_modelo=tipo_modelo,
        tipo_pista=tipo_pista,
        sentido=sentido,
        faixa=faixa,
    )


def _processar_inferencia(
    viagem_nome: str,
    tipo_modelo: str = "igg",
    tipo_pista: str = "simples",
    sentido: str = "crescente",
    faixa: int | None = None,
) -> dict:
    settings = get_settings()
    viagem_nome = viagem_nome.strip()
    destino = settings.dados_dir / viagem_nome
    tipo_pista = normalizar_tipo_pista(tipo_pista)
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
        viagem_nome=viagem_nome,
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
    return _executar_job(_extrair_zip, viagem=viagem, zip_path=zip_path)


def _extrair_zip(viagem: str, zip_path: str) -> dict:
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
