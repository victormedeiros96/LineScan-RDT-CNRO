from __future__ import annotations

import json
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile

from src.api.routes_folders import _resolver_viagem
from src.core.config import Settings
from src.core.dependencies import get_settings_dep
from src.services import icp_placas, resultado_store
from src.services.igg_calculator import (
    calcular_igg_por_km,
    montar_estacoes_por_km,
)
from src.services.planilha_import import importar_planilha
from src.utils.tipo_pista import normalizar_tipo_pista

router = APIRouter(prefix="/api/relatorios", tags=["relatorios"])


@router.post("/retigrafico")
async def gerar_retigrafico():
    return {"status": "pending", "arquivo": ""}


@router.post("/antt")
async def exportar_antt():
    return {"status": "pending", "arquivo": ""}


@router.post("/igg/{viagem_nome}")
async def calcular_igg(
    viagem_nome: str,
    body: dict,
    settings: Settings = Depends(get_settings_dep),
):
    base = _resolver_viagem(settings, viagem_nome)
    if base is None:
        raise HTTPException(404, "Análise não encontrada para esta viagem")
    analise_path = base / "analise_completa.json"
    params_path = base / "parametros_adicionais.json"

    if not analise_path.is_file():
        raise HTTPException(404, "Análise não encontrada para esta viagem")

    try:
        analise: dict = json.loads(analise_path.read_text(encoding="utf-8"))
    except Exception as e:
        raise HTTPException(500, f"Erro ao ler análise: {e}")

    parametros: dict = {}
    if params_path.is_file():
        try:
            parametros = json.loads(params_path.read_text(encoding="utf-8"))
        except Exception:
            parametros = {}

    km_filter = body.get("km")

    agrupado = montar_estacoes_por_km(analise, viagem_nome, base)
    if not agrupado:
        return []

    resultados = []
    for km_key, data in sorted(agrupado.items(), key=lambda x: int(x[0])):
        if km_filter is not None and km_key != str(km_filter):
            continue
        params_km = parametros.get(km_key, {})
        resultado = calcular_igg_por_km(int(km_key), data["estacoes"], params_km)
        resultados.append(resultado)

    # Persiste os resultados por trecho/KM (RDT1-13); best-effort: sem banco o
    # relatório continua sendo devolvido normalmente.
    resultado_store.persistir_resultados(
        viagem_nome=base.name,
        tipo_analise="igg",
        resultados=resultados,
        norma=resultado_store.NORMA_IGG,
        reconciliar=km_filter is None,
    )

    return resultados


@router.get("/resultados/{viagem_nome}")
async def listar_resultados(
    viagem_nome: str,
    tipo: str = "igg",
    settings: Settings = Depends(get_settings_dep),
):
    """Resultados de IGG/ICP persistidos por KM (RDT1-13)."""
    if tipo not in ("igg", "icp"):
        raise HTTPException(400, "Parâmetro 'tipo' deve ser 'igg' ou 'icp'")
    base = _resolver_viagem(settings, viagem_nome)
    if base is None:
        raise HTTPException(404, f"Viagem não encontrada: {viagem_nome}")
    resultados = resultado_store.carregar_resultados(base.name, tipo)
    if resultados is None:
        raise HTTPException(503, "Banco de dados indisponível")
    return resultados


@router.post("/importar-planilha/{viagem_nome}")
async def importar_planilha_endpoint(
    viagem_nome: str,
    file: UploadFile = File(...),
    settings: Settings = Depends(get_settings_dep),
):
    base = _resolver_viagem(settings, viagem_nome)
    if base is None:
        raise HTTPException(404, f"Viagem não encontrada: {viagem_nome}")

    # Lê config da viagem para saber tipo de pista e faixa
    config_path = base / "viagem_config.json"
    config: dict = {}
    if config_path.is_file():
        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
        except Exception:
            config = {}

    try:
        tipo_pista = normalizar_tipo_pista(
            config.get("tipo_pista", config.get("tipo_faixa"))
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    faixa = config.get("faixa")

    params_path = base / "parametros_adicionais.json"

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir) / (file.filename or "planilha.xls")
        content = await file.read()
        tmp_path.write_bytes(content)

        try:
            dados_importados = importar_planilha(tmp_path, tipo_pista, faixa)
        except ValueError as e:
            raise HTTPException(400, str(e))

    parametros_existentes: dict = {}
    if params_path.is_file():
        try:
            parametros_existentes = json.loads(params_path.read_text(encoding="utf-8"))
        except Exception:
            parametros_existentes = {}

    kms_importados = []
    for km_key, valores in dados_importados.items():
        if km_key not in parametros_existentes:
            parametros_existentes[km_key] = {}
        parametros_existentes[km_key]["TRI (mm)"] = valores["TRI (mm)"]
        parametros_existentes[km_key]["TRE (mm)"] = valores["TRE (mm)"]
        kms_importados.append(int(km_key))

    params_path.write_text(
        json.dumps(parametros_existentes, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return {
        "status": "ok",
        "kms_importados": sorted(kms_importados),
        "total_kms": len(kms_importados),
    }


@router.post("/icp/{viagem_nome}/placas")
async def gerar_placas_icp(
    viagem_nome: str,
    body: dict,
    settings: Settings = Depends(get_settings_dep),
):
    """Gera o inventário de placas do rígido e as amostras de ICP.

    Aceita ``periodo_m`` + ``fase_m`` de uma calibração já conhecida ou
    ``juntas_km`` para calibrar pela rede periódica do ``juntas-detector``.
    """
    base = _resolver_viagem(settings, viagem_nome)
    if base is None:
        raise HTTPException(404, f"Viagem não encontrada: {viagem_nome}")

    faixas = icp_placas.listar_faixas(base)
    if not faixas:
        raise HTTPException(
            404,
            "Nenhuma faixa encontrada (lote_*_meta.json com km_inicio/km_fim).",
        )

    calibracao: dict | None = None
    mm_x = float(body.get("mm_por_px_x", icp_placas.MM_POR_PX))
    mm_y = float(body.get("mm_por_px_y", icp_placas.MM_POR_PX))
    try:
        if body.get("juntas_km"):
            calibracao = icp_placas.calibrar_por_juntas(
                body["juntas_km"],
                km0=min(faixa.km_inicio for faixa in faixas),
                mm_por_px_y=mm_y,
            )
            periodo_m = float(calibracao["periodo_m"])
            fase_m = float(calibracao["fase_m"])
        else:
            if body.get("periodo_m") is None:
                raise ValueError(
                    "Informe 'periodo_m' (calibração) ou 'juntas_km' (juntas para calibrar)."
                )
            periodo_m = float(body["periodo_m"])
            fase_m = float(body.get("fase_m", 0.0))
            calibracao = {
                "periodo_m": round(periodo_m, 6),
                "fase_m": round(fase_m, 6),
                "mm_por_px_x": mm_x,
                "mm_por_px_y": mm_y,
                "norma": icp_placas.NORMA_ICP,
            }

        inventario = icp_placas.montar_inventario(
            faixas,
            periodo_m=periodo_m,
            fase_m=fase_m,
            largura_placa_m=float(body.get("largura_placa_m", icp_placas.LARGURA_PLACA_M)),
            comprimento_max_m=float(
                body.get("comprimento_max_m", icp_placas.COMPRIMENTO_MAX_M)
            ),
            tamanho_amostra=int(body.get("tamanho_amostra", icp_placas.TAMANHO_AMOSTRA)),
            angulo_graus=float(body.get("angulo_graus", 0.0)),
            mm_por_px_x=mm_x,
            mm_por_px_y=mm_y,
            calibracao=calibracao,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    if body.get("gerar_rois", True):
        icp_placas.salvar_rois(
            base,
            faixas,
            inventario["placas"],
            mm_por_px_x=mm_x,
            mm_por_px_y=mm_y,
        )

    icp_placas.salvar_inventario(base, inventario)
    return {
        "status": "ok",
        "viagem": base.name,
        **icp_placas.resumo_inventario(inventario),
        "inventario": inventario,
    }


@router.get("/icp/{viagem_nome}/placas")
async def obter_placas_icp(
    viagem_nome: str,
    settings: Settings = Depends(get_settings_dep),
):
    """Inventário de placas persistido (``icp_placas.json``)."""
    base = _resolver_viagem(settings, viagem_nome)
    if base is None:
        raise HTTPException(404, f"Viagem não encontrada: {viagem_nome}")
    inventario = icp_placas.carregar_inventario(base)
    if inventario is None:
        raise HTTPException(
            404, "Inventário de placas não gerado para esta viagem."
        )
    return inventario


@router.get("/icp/{viagem_nome}/amostras")
async def listar_amostras_icp(
    viagem_nome: str,
    settings: Settings = Depends(get_settings_dep),
):
    """Amostras de até 20 placas do inventário persistido."""
    base = _resolver_viagem(settings, viagem_nome)
    if base is None:
        raise HTTPException(404, f"Viagem não encontrada: {viagem_nome}")
    inventario = icp_placas.carregar_inventario(base)
    if inventario is None:
        raise HTTPException(
            404, "Inventário de placas não gerado para esta viagem."
        )
    return {
        "norma": inventario.get("norma"),
        "trecho": inventario.get("trecho"),
        "amostras": inventario.get("amostras", []),
    }


@router.post("/exportar/{viagem_nome}")
async def exportar_viagem(
    viagem_nome: str,
    settings: Settings = Depends(get_settings_dep),
):
    import csv
    import io

    base = _resolver_viagem(settings, viagem_nome)
    if base is None:
        raise HTTPException(404, "Análise não encontrada")
    analise_path = base / "analise_completa.json"
    if not analise_path.is_file():
        raise HTTPException(404, "Análise não encontrada")

    try:
        analise = json.loads(analise_path.read_text("utf-8"))
    except Exception as e:
        raise HTTPException(500, f"Erro ao ler análise: {e}")

    params_path = base / "parametros_adicionais.json"
    parametros: dict = {}
    if params_path.is_file():
        try:
            parametros = json.loads(params_path.read_text("utf-8"))
        except Exception:
            parametros = {}

    agrupado = montar_estacoes_por_km(analise, viagem_nome, base)
    if not agrupado:
        return Response(
            content="",
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=export.csv"},
        )

    resultados = []
    for km_key, data in sorted(agrupado.items(), key=lambda x: int(x[0])):
        params_km = parametros.get(km_key, {})
        r = calcular_igg_por_km(int(km_key), data["estacoes"], params_km)
        resultados.append(r)

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["KM_INICIAL", "KM_FINAL", "IGG", "CONCEITO"])
    for r in resultados:
        km = r["km"]
        writer.writerow([km, km + 1, r["igg"], r["conceito"]])

    csv_content = output.getvalue()
    return Response(
        content=csv_content,
        media_type="text/csv",
        headers={
            "Content-Disposition": f"attachment; filename=igg_{viagem_nome}.csv",
            "Content-Type": "text/csv; charset=utf-8",
        },
    )
