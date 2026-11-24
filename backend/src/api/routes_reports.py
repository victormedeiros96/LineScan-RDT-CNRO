from __future__ import annotations

import json
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile
from fastapi.responses import FileResponse

from src.api.routes_folders import _resolver_viagem
from src.core.config import Settings
from src.core.dependencies import get_settings_dep
from src.services import icp_juntas, icp_marcacoes, icp_placas, resultado_store
from src.services.icp_import import NORMA_ICP, importar_icp
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


@router.post("/icp/{viagem_nome}")
async def importar_icp_endpoint(
    viagem_nome: str,
    file: UploadFile = File(...),
    settings: Settings = Depends(get_settings_dep),
):
    """Importa a ficha de inspeção (XLSX/CSV), calcula e persiste o ICP por amostra."""
    base = _resolver_viagem(settings, viagem_nome)
    if base is None:
        raise HTTPException(404, f"Viagem não encontrada: {viagem_nome}")

    nome_arquivo = Path(file.filename or "ficha_icp.xlsx").name
    if Path(nome_arquivo).suffix.lower() not in {".xlsx", ".xlsm", ".csv", ".xls"}:
        raise HTTPException(
            400, "Envie a ficha de inspeção em .xlsx, .xlsm ou .csv."
        )

    with tempfile.TemporaryDirectory() as tmpdir:
        caminho = Path(tmpdir) / nome_arquivo
        caminho.write_bytes(await file.read())
        try:
            resultados = importar_icp(caminho)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    persistidos = resultado_store.persistir_resultados(
        viagem_nome=base.name,
        tipo_analise="icp",
        resultados=resultados,
        norma=NORMA_ICP,
        reconciliar=True,
    )

    # Reimportar a ficha não pode apagar as marcações manuais: recalcula as
    # amostras que têm edição manual (RDT1-23).
    totais = {
        str(item["amostra"]): float(item.get("total_placas") or 0)
        for item in resultados
        if item.get("amostra")
    }
    reaplicadas = icp_marcacoes.reaplicar_apos_importacao(
        base, base.name, totais
    )

    return {
        "status": "ok",
        "viagem": base.name,
        "arquivo": nome_arquivo,
        "total_amostras": len(resultados),
        "persistidos": persistidos,
        "amostras_com_marcacoes_recalculadas": reaplicadas,
        "resultados": resultados,
    }


@router.get("/icp/{viagem_nome}")
async def listar_icp(
    viagem_nome: str,
    km: int | None = None,
    settings: Settings = Depends(get_settings_dep),
):
    """Resultados de ICP persistidos por amostra, opcionalmente filtrados por KM."""
    base = _resolver_viagem(settings, viagem_nome)
    if base is None:
        raise HTTPException(404, f"Viagem não encontrada: {viagem_nome}")
    resultados = resultado_store.carregar_resultados(base.name, "icp")
    if resultados is None:
        raise HTTPException(503, "Banco de dados indisponível")
    if km is not None:
        resultados = [item for item in resultados if item.get("km") == km]
    return resultados


@router.post("/icp/{viagem_nome}/placas")
async def gerar_placas_icp(
    viagem_nome: str,
    body: dict,
    settings: Settings = Depends(get_settings_dep),
):
    """Gera o inventário de placas do rígido e as amostras de ICP.

    Origens aceitas, em ordem de preferência:

    * ``detectar: true`` — roda o modelo (juntas-detector + trinca-detector) e
      monta cada placa entre duas juntas realmente segmentadas na imagem;
    * ``juntas`` — lista de geometrias de junta já detectadas (``km``,
      ``angulo_graus``, ``x0``, ``x1``);
    * ``periodo_m`` + ``fase_m`` ou ``juntas_km`` — rede periódica (fallback).

    Nos dois primeiros casos o tamanho da placa vem da imagem e pode variar na
    viagem; não é preciso declarar um tamanho fixo.
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
    juntas_geometria = list(body.get("juntas") or [])
    mm_x = float(body.get("mm_por_px_x", icp_placas.MM_POR_PX))
    mm_y = float(body.get("mm_por_px_y", icp_placas.MM_POR_PX))
    periodo_m: float | None = None
    fase_m = 0.0
    try:
        if body.get("detectar"):
            detector = icp_juntas.carregar_config(
                settings.modelos_dir,
                nuclio_dir=settings.icp_nuclio_dir,
                modelo_pt=settings.icp_modelo_pt,
                limiar=settings.icp_limiar_juntas,
                ordem_invertida=settings.icp_ordem_invertida,
                mm_por_px_y=settings.icp_mm_por_px_y,
            )
            juntas_geometria = icp_juntas.detectar_juntas_geometria(
                base, faixas, detector
            )
            if not juntas_geometria:
                raise ValueError(
                    "A detecção não encontrou juntas na imagem; confira o modelo e a escala."
                )
            calibracao = {
                "fonte": "detector",
                "n_juntas": len(juntas_geometria),
                "mm_por_px_x": mm_x,
                "mm_por_px_y": mm_y,
                "norma": icp_placas.NORMA_ICP,
            }
        elif juntas_geometria:
            calibracao = {
                "fonte": "juntas_detectadas",
                "n_juntas": len(juntas_geometria),
                "mm_por_px_x": mm_x,
                "mm_por_px_y": mm_y,
                "norma": icp_placas.NORMA_ICP,
            }
        elif body.get("juntas_km"):
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
                    "Informe 'juntas', 'detectar', 'periodo_m' (calibração) "
                    "ou 'juntas_km' (juntas para calibrar)."
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
            juntas=juntas_geometria or None,
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
    except (ValueError, RuntimeError) as exc:
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


def _localizar_placa(
    inventario: dict, placa_id: str
) -> dict | None:
    for placa in inventario.get("placas", []):
        if placa.get("id") == placa_id:
            return placa
    return None


def _vizinhos_da_placa(
    inventario: dict, placa: dict
) -> tuple[dict | None, str | None, str | None]:
    """Amostra da placa e os ids da placa anterior/próxima dentro dela."""
    amostra_id = placa.get("amostra_id")
    amostra = next(
        (item for item in inventario.get("amostras", []) if item.get("id") == amostra_id),
        None,
    )
    if amostra is None:
        return None, None, None
    ids = list(amostra.get("placas") or [])
    try:
        indice = ids.index(placa["id"])
    except ValueError:
        return amostra, None, None
    anterior = ids[indice - 1] if indice > 0 else None
    proxima = ids[indice + 1] if indice + 1 < len(ids) else None
    return amostra, anterior, proxima


@router.get("/icp/{viagem_nome}/placas/{placa_id}")
async def obter_placa_icp(
    viagem_nome: str,
    placa_id: str,
    settings: Settings = Depends(get_settings_dep),
):
    """Detalhe de uma placa do inventário persistido (RDT1-24).

    Devolve a placa, a amostra a que pertence, os ids da anterior/próxima na
    amostra e se há recorte de imagem disponível.
    """
    base = _resolver_viagem(settings, viagem_nome)
    if base is None:
        raise HTTPException(404, f"Viagem não encontrada: {viagem_nome}")
    inventario = icp_placas.carregar_inventario(base)
    if inventario is None:
        raise HTTPException(
            404, "Inventário de placas não gerado para esta viagem."
        )
    placa = _localizar_placa(inventario, placa_id)
    if placa is None:
        raise HTTPException(404, f"Placa não encontrada: {placa_id}")
    amostra, anterior, proxima = _vizinhos_da_placa(inventario, placa)
    tem_recorte = bool(placa.get("roi"))
    return {
        "viagem": base.name,
        "placa": placa,
        "amostra": amostra,
        "anterior": anterior,
        "proxima": proxima,
        "recorte_disponivel": tem_recorte,
        "recorte_url": (
            f"/api/relatorios/icp/{base.name}/placas/{placa['id']}/recorte"
            if tem_recorte
            else None
        ),
    }


_MEDIA_TYPE_RECORTE = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
}


@router.get("/icp/{viagem_nome}/placas/{placa_id}/recorte")
async def obter_recorte_placa_icp(
    viagem_nome: str,
    placa_id: str,
    settings: Settings = Depends(get_settings_dep),
):
    """Serve o PNG de recorte da placa em ``icp_rois/`` (RDT1-24).

    O nome do arquivo é sempre validado contra o diretório de ROIs da viagem
    para impedir path traversal.
    """
    base = _resolver_viagem(settings, viagem_nome)
    if base is None:
        raise HTTPException(404, f"Viagem não encontrada: {viagem_nome}")
    inventario = icp_placas.carregar_inventario(base)
    if inventario is None:
        raise HTTPException(
            404, "Inventário de placas não gerado para esta viagem."
        )
    placa = _localizar_placa(inventario, placa_id)
    if placa is None:
        raise HTTPException(404, f"Placa não encontrada: {placa_id}")

    roi = placa.get("roi") or {}
    nome = roi.get("arquivo")
    if not nome:
        raise HTTPException(
            404, f"A placa {placa_id} não tem recorte de imagem disponível."
        )

    sufixo = Path(str(nome)).suffix.lower()
    if Path(str(nome)).name != str(nome) or sufixo not in _MEDIA_TYPE_RECORTE:
        raise HTTPException(400, "Caminho de recorte inválido.")

    diretorio = (base / "icp_rois").resolve()
    caminho = (diretorio / str(nome)).resolve()
    if caminho.parent != diretorio:
        raise HTTPException(400, "Caminho de recorte inválido.")
    if not caminho.is_file():
        raise HTTPException(
            404, f"Recorte da placa {placa_id} não encontrado em disco."
        )
    return FileResponse(caminho, media_type=_MEDIA_TYPE_RECORTE[sufixo])


def _total_placas_amostra(
    viagem_nome: str, inventario: dict, amostra_id: str
) -> float:
    """Total de placas da amostra: prioriza o resultado importado da ficha."""
    resultados = resultado_store.carregar_resultados(viagem_nome, "icp") or []
    for resultado in resultados:
        if resultado.get("amostra") == amostra_id:
            total = float(resultado.get("total_placas") or 0)
            if total > 0:
                return total
    amostra = next(
        (item for item in inventario.get("amostras", []) if item.get("id") == amostra_id),
        None,
    )
    return float((amostra or {}).get("n_placas") or 0)


def _resolver_placa_e_total(
    settings: Settings, viagem_nome: str, placa_id: str
) -> tuple[Path, dict, str, float]:
    base = _resolver_viagem(settings, viagem_nome)
    if base is None:
        raise HTTPException(404, f"Viagem não encontrada: {viagem_nome}")
    inventario = icp_placas.carregar_inventario(base)
    if inventario is None:
        raise HTTPException(
            404, "Inventário de placas não gerado para esta viagem."
        )
    placa = _localizar_placa(inventario, placa_id)
    if placa is None:
        raise HTTPException(404, f"Placa não encontrada: {placa_id}")
    amostra_id = str(placa.get("amostra_id") or "")
    if not amostra_id:
        raise HTTPException(400, f"A placa {placa_id} não pertence a uma amostra.")
    total = _total_placas_amostra(base.name, inventario, amostra_id)
    if total <= 0:
        raise HTTPException(
            400, f"A amostra {amostra_id} não tem placas para calcular o ICP."
        )
    return base, placa, amostra_id, total


def _total_da_marcacao(
    settings: Settings, viagem_nome: str, marcacao: dict
) -> tuple[Path, float]:
    base = _resolver_viagem(settings, viagem_nome)
    if base is None:
        raise HTTPException(404, f"Viagem não encontrada: {viagem_nome}")
    inventario = icp_placas.carregar_inventario(base)
    if inventario is None:
        raise HTTPException(
            404, "Inventário de placas não gerado para esta viagem."
        )
    amostra_id = str(marcacao.get("amostra") or "")
    total = _total_placas_amostra(base.name, inventario, amostra_id)
    if total <= 0:
        raise HTTPException(
            400, f"A amostra {amostra_id} não tem placas para calcular o ICP."
        )
    return base, total


@router.get("/icp/{viagem_nome}/marcacoes")
async def listar_marcacoes_icp(
    viagem_nome: str,
    amostra: str | None = None,
    incluir_removidas: bool = False,
    settings: Settings = Depends(get_settings_dep),
):
    """Marcações manuais de patologias do ICP (RDT1-23)."""
    base = _resolver_viagem(settings, viagem_nome)
    if base is None:
        raise HTTPException(404, f"Viagem não encontrada: {viagem_nome}")
    return {
        "viagem": base.name,
        "marcacoes": icp_marcacoes.listar(
            base, amostra, incluir_removidas=incluir_removidas
        ),
    }


@router.post("/icp/{viagem_nome}/marcacoes")
async def criar_marcacao_icp(
    viagem_nome: str,
    body: dict,
    settings: Settings = Depends(get_settings_dep),
):
    """Marca um defeito numa placa e recalcula a amostra (ficha + manual)."""
    placa_id = str(body.get("placa_id") or "").strip()
    if not placa_id:
        raise HTTPException(400, "Informe 'placa_id'.")
    base, placa, amostra_id, total = _resolver_placa_e_total(
        settings, viagem_nome, placa_id
    )
    try:
        marcacao, resultado = icp_marcacoes.adicionar(
            base,
            base.name,
            placa_id=placa_id,
            amostra=amostra_id,
            total_placas=total,
            tipo=body.get("tipo"),
            severidade=body.get("severidade", ""),
            km=placa.get("km"),
            faixa=placa.get("faixa"),
            geometria=body.get("geometria"),
            autor=body.get("autor"),
        )
    except PermissionError as exc:
        raise HTTPException(409, str(exc)) from exc
    except (ValueError, TypeError) as exc:
        raise HTTPException(400, str(exc)) from exc
    return {
        "status": "ok",
        "marcacao": marcacao,
        "resultado": resultado,
        "marcacoes": icp_marcacoes.listar(base, amostra_id),
    }


@router.patch("/icp/{viagem_nome}/marcacoes/{marcacao_id}")
async def atualizar_marcacao_icp(
    viagem_nome: str,
    marcacao_id: str,
    body: dict,
    settings: Settings = Depends(get_settings_dep),
):
    """Edita tipo/severidade/geometria de uma marcação e recalcula a amostra."""
    base = _resolver_viagem(settings, viagem_nome)
    if base is None:
        raise HTTPException(404, f"Viagem não encontrada: {viagem_nome}")
    alvo = icp_marcacoes.obter(base, marcacao_id)
    if alvo is None:
        raise HTTPException(404, f"Marcação não encontrada: {marcacao_id}")
    base, total = _total_da_marcacao(settings, viagem_nome, alvo)
    try:
        marcacao, resultado = icp_marcacoes.atualizar(
            base,
            base.name,
            marcacao_id,
            tipo=body.get("tipo"),
            severidade=body.get("severidade"),
            geometria=body.get("geometria"),
            autor=body.get("autor"),
            total_placas=total,
        )
    except PermissionError as exc:
        raise HTTPException(409, str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except (ValueError, TypeError) as exc:
        raise HTTPException(400, str(exc)) from exc
    return {
        "status": "ok",
        "marcacao": marcacao,
        "resultado": resultado,
        "marcacoes": icp_marcacoes.listar(base, str(marcacao["amostra"])),
    }


@router.delete("/icp/{viagem_nome}/marcacoes/{marcacao_id}")
async def remover_marcacao_icp(
    viagem_nome: str,
    marcacao_id: str,
    settings: Settings = Depends(get_settings_dep),
):
    """Remove (soft delete) uma marcação e recalcula a amostra."""
    base = _resolver_viagem(settings, viagem_nome)
    if base is None:
        raise HTTPException(404, f"Viagem não encontrada: {viagem_nome}")
    alvo = icp_marcacoes.obter(base, marcacao_id)
    if alvo is None:
        raise HTTPException(404, f"Marcação não encontrada: {marcacao_id}")
    base, total = _total_da_marcacao(settings, viagem_nome, alvo)
    try:
        marcacao, resultado = icp_marcacoes.remover(
            base, base.name, marcacao_id, total_placas=total
        )
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {
        "status": "ok",
        "marcacao": marcacao,
        "resultado": resultado,
        "marcacoes": icp_marcacoes.listar(base, str(marcacao["amostra"])),
    }


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


@router.get("/icp/{viagem_nome}/exportar")
async def exportar_icp(
    viagem_nome: str,
    settings: Settings = Depends(get_settings_dep),
):
    """Exporta o laudo do ICP por amostra (uma linha por defeito) em CSV."""
    import csv
    import io

    from src.services.icp_calculator import DEFEITOS

    base = _resolver_viagem(settings, viagem_nome)
    if base is None:
        raise HTTPException(404, f"Viagem não encontrada: {viagem_nome}")

    resultados = resultado_store.carregar_resultados(base.name, "icp")
    if resultados is None:
        raise HTTPException(503, "Banco de dados indisponível")
    if not resultados:
        raise HTTPException(404, "Nenhum resultado de ICP para esta viagem.")

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "AMOSTRA",
            "RODOVIA",
            "KM_INICIAL",
            "KM_FINAL",
            "FAIXA",
            "TOTAL_PLACAS",
            "TIPO",
            "DEFEITO",
            "SEVERIDADE",
            "DENSIDADE_PCT",
            "PLACAS_AFETADAS",
            "VD",
            "VDT",
            "Q",
            "VDC",
            "ICP",
            "CONCEITO",
        ]
    )
    ordem = {"A": 3, "M": 2, "B": 1, "U": 0}
    for item in resultados:
        detalhe = item
        defeitos = item.get("defeitos") or []
        base_row = [
            item.get("amostra"),
            detalhe.get("rodovia"),
            detalhe.get("km_inicial"),
            detalhe.get("km_final"),
            detalhe.get("faixa"),
            detalhe.get("total_placas"),
            None,
            None,
            None,
            None,
            None,
            None,
            detalhe.get("vdt"),
            detalhe.get("q"),
            detalhe.get("vdc"),
            item.get("valor"),
            item.get("conceito"),
        ]
        if not defeitos:
            writer.writerow(base_row)
            continue
        for defeito in sorted(
            defeitos,
            key=lambda d: (int(d.get("tipo", 0)), -ordem.get(str(d.get("severidade")), 0)),
        ):
            tipo = int(defeito.get("tipo", 0))
            linha = list(base_row)
            linha[6] = tipo
            linha[7] = DEFEITOS.get(tipo)
            linha[8] = defeito.get("severidade")
            linha[9] = defeito.get("densidade")
            linha[10] = defeito.get("placas_afetadas")
            linha[11] = defeito.get("vd")
            writer.writerow(linha)

    return Response(
        content=output.getvalue(),
        media_type="text/csv",
        headers={
            "Content-Disposition": f"attachment; filename=icp_{base.name}.csv",
            "Content-Type": "text/csv; charset=utf-8",
        },
    )


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
