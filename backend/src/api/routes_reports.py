from __future__ import annotations

import json
from pathlib import Path

import tempfile
import os

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Response

from src.core.config import Settings
from src.core.dependencies import get_settings_dep
from src.api.routes_folders import _resolver_viagem
from src.services.igg_calculator import (
    calcular_igg_por_km,
    montar_estacoes_por_km,
)
from src.services.planilha_import import importar_planilha

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
    tipo_pista = "simples"
    faixa = None
    if config_path.is_file():
        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
            tipo_pista = config.get("tipo_pista", "simples")
            faixa = config.get("faixa")
        except Exception:
            pass

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


@router.post("/exportar/{viagem_nome}")
async def exportar_viagem(
    viagem_nome: str,
    settings: Settings = Depends(get_settings_dep),
):
    import io
    import csv

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
        return Response(content="", media_type="text/csv", headers={"Content-Disposition": "attachment; filename=export.csv"})

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
