from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile
from fastapi.responses import FileResponse

from src.api.routes_folders import _resolver_viagem
from src.core.config import Settings
from src.core.dependencies import get_settings_dep
from src.services import (
    icp_laudo,
    icp_marcacoes,
    icp_placas,
    icp_trecho,
    resultado_store,
)
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
    """Resultados de ICP persistidos por amostra, opcionalmente filtrados por KM.

    O consolidado do trecho fica à parte em ``GET .../icp/{viagem}/trecho`` para
    não se misturar às amostras (média, gráfico e tabela da tela do ICP).
    """
    base = _resolver_viagem(settings, viagem_nome)
    if base is None:
        raise HTTPException(404, f"Viagem não encontrada: {viagem_nome}")
    resultados = resultado_store.carregar_resultados(base.name, "icp")
    if resultados is None:
        raise HTTPException(503, "Banco de dados indisponível")
    resultados = [item for item in resultados if not icp_trecho.eh_trecho(item)]
    if km is not None:
        resultados = [item for item in resultados if item.get("km") == km]
    return resultados


@router.post("/icp/{viagem_nome}/trecho")
async def calcular_trecho_icp(
    viagem_nome: str,
    body: dict,
    settings: Settings = Depends(get_settings_dep),
):
    """Calcula e persiste o ICP do trecho (DNIT 062/2004-PRO, itens 6 e 7.3).

    Corpo aceito:

    * ``metodo`` — ``total`` (padrão), ``amostragem`` ou
      ``amostragem_adicional``;
    * ``adicionais`` — ids das amostras adicionais (só no último método);
    * ``N`` — conferência do total de amostras do trecho (padrão: quantidade
      persistida);
    * ``e`` — erro admissível; sem ele o ICP e ``S`` são calculados, mas ``n``
      fica indisponível (a norma não fixa ``e``).

    A consolidação lê as amostras **persistidas** (ficha importada + marcações
    manuais já recalculadas) para manter uma única base de cálculo.
    """
    base = _resolver_viagem(settings, viagem_nome)
    if base is None:
        raise HTTPException(404, f"Viagem não encontrada: {viagem_nome}")
    resultados = resultado_store.carregar_resultados(base.name, "icp")
    if resultados is None:
        raise HTTPException(503, "Banco de dados indisponível")
    amostras = [item for item in resultados if not icp_trecho.eh_trecho(item)]
    if not amostras:
        raise HTTPException(
            404, "Nenhuma amostra de ICP para consolidar neste trecho."
        )

    try:
        resultado = icp_trecho.calcular_icp_trecho(
            amostras,
            metodo=body.get("metodo"),
            adicionais=body.get("adicionais"),
            n=body.get("N"),
            e=body.get("e"),
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    kms = [
        float(item["km"])
        for item in amostras
        if isinstance(item.get("km"), (int, float))
    ]
    persistivel = icp_trecho.para_persistencia(
        resultado,
        km_inicio=min(kms) if kms else None,
        km_fim=max(kms) if kms else None,
    )
    persistidos = resultado_store.persistir_resultados(
        viagem_nome=base.name,
        tipo_analise="icp",
        resultados=[persistivel],
        norma=icp_trecho.NORMA_ICP,
        reconciliar=False,
    )
    return {
        "status": "ok",
        "viagem": base.name,
        "persistido": persistidos is not None,
        "trecho": resultado,
    }


@router.get("/icp/{viagem_nome}/trecho")
async def obter_trecho_icp(
    viagem_nome: str,
    settings: Settings = Depends(get_settings_dep),
):
    """ICP consolidado do trecho persistido (ou 404 se ainda não calculado)."""
    base = _resolver_viagem(settings, viagem_nome)
    if base is None:
        raise HTTPException(404, f"Viagem não encontrada: {viagem_nome}")
    resultados = resultado_store.carregar_resultados(base.name, "icp")
    if resultados is None:
        raise HTTPException(503, "Banco de dados indisponível")
    trecho = next(
        (item for item in resultados if icp_trecho.eh_trecho(item)), None
    )
    if trecho is None:
        raise HTTPException(
            404, "ICP do trecho ainda não calculado para esta viagem."
        )
    return trecho


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


def _montar_laudo_icp(base: Path) -> dict:
    """Carrega resultados + inventário e monta o laudo do item 5.5."""
    resultados = resultado_store.carregar_resultados(base.name, "icp")
    if resultados is None:
        raise HTTPException(503, "Banco de dados indisponível")
    if not resultados:
        raise HTTPException(404, "Nenhum resultado de ICP para esta viagem.")
    inventario = icp_placas.carregar_inventario(base)
    try:
        return icp_laudo.montar_laudo(
            resultados,
            larguras=icp_laudo.larguras_por_amostra(inventario),
        )
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/icp/{viagem_nome}/laudo")
async def obter_laudo_icp(
    viagem_nome: str,
    settings: Settings = Depends(get_settings_dep),
):
    """Laudo estruturado do ICP (DNIT 062/2004-PRO, item 5.5).

    Seções: resumo do trecho, distribuição por conceito, resumo por amostra,
    juntas/selante, defeitos atípicos, assentamentos, buracos e amostras com
    ICP ≤ 40.
    """
    base = _resolver_viagem(settings, viagem_nome)
    if base is None:
        raise HTTPException(404, f"Viagem não encontrada: {viagem_nome}")
    return _montar_laudo_icp(base)


def _escrever_secao(
    writer: Any,
    titulo: str,
    cabecalho: list[str],
    linhas: list[list[Any]],
) -> None:
    writer.writerow([])
    writer.writerow([titulo])
    writer.writerow(cabecalho)
    if not linhas:
        writer.writerow(["— sem registros —"])
        return
    writer.writerows(linhas)


@router.get("/icp/{viagem_nome}/laudo.csv")
async def exportar_laudo_icp_csv(
    viagem_nome: str,
    settings: Settings = Depends(get_settings_dep),
):
    """Laudo do ICP em CSV com as seções do item 5.5 (abre em planilha)."""
    import csv
    import io

    base = _resolver_viagem(settings, viagem_nome)
    if base is None:
        raise HTTPException(404, f"Viagem não encontrada: {viagem_nome}")
    laudo = _montar_laudo_icp(base)

    output = io.StringIO()
    writer = csv.writer(output)
    meta = laudo["metadados"]
    writer.writerow([f"LAUDO ICP — {laudo['norma']}"])
    writer.writerow(["Rodovia", meta.get("rodovia")])
    writer.writerow(["KM inicial", meta.get("km_inicio")])
    writer.writerow(["KM final", meta.get("km_fim")])
    writer.writerow(["Faixas", ", ".join(str(f) for f in meta.get("faixas") or [])])
    writer.writerow(["Datas", ", ".join(str(d) for d in meta.get("datas") or [])])
    writer.writerow(
        ["Operadores", ", ".join(str(o) for o in meta.get("operadores") or [])]
    )
    writer.writerow(["Norma", meta.get("norma")])
    writer.writerow(["Gerado em", laudo.get("gerado_em")])

    trecho = laudo["resumo_trecho"]
    _escrever_secao(
        writer,
        "RESUMO DO TRECHO",
        ["CAMPO", "VALOR"],
        [
            ["ICP do trecho", trecho.get("icp", trecho.get("valor"))],
            ["Conceito", trecho.get("conceito")],
            ["Método", trecho.get("metodo")],
            ["N (amostras do trecho)", trecho.get("N")],
            ["Desvio padrão (S)", trecho.get("desvio_padrao")],
            ["Erro admitido (e)", trecho.get("e")],
            ["N mínimo (n)", trecho.get("n_minimo")],
        ],
    )

    _escrever_secao(
        writer,
        "RESUMO POR AMOSTRA",
        [
            "AMOSTRA",
            "RODOVIA",
            "KM_INICIAL",
            "KM_FINAL",
            "FAIXA",
            "TOTAL_PLACAS",
            "VDT",
            "Q",
            "VDC",
            "ICP",
            "CONCEITO",
            "EM_BRANCO",
            "ICP_MENOR_OU_IGUAL_40",
        ],
        [
            [
                item["amostra"],
                item["rodovia"],
                item["km_inicial"],
                item["km_final"],
                item["faixa"],
                item["total_placas"],
                item["vdt"],
                item["q"],
                item["vdc"],
                item["icp"],
                item["conceito"],
                "sim" if item["em_branco"] else "não",
                "sim" if item["critico"] else "não",
            ]
            for item in laudo["amostras"]
        ],
    )

    _escrever_secao(
        writer,
        "DEFEITOS (UMA LINHA POR DEFEITO)",
        [
            "AMOSTRA",
            "KM_INICIAL",
            "FAIXA",
            "TIPO",
            "DEFEITO",
            "SEVERIDADE",
            "DENSIDADE_PCT",
            "PLACAS_AFETADAS",
            "VD",
            "ICP_MENOR_OU_IGUAL_40",
        ],
        [
            [
                item["amostra"],
                item["km_inicial"],
                item["faixa"],
                item["tipo"],
                item["nome"],
                item["severidade"],
                item.get("densidade"),
                item.get("placas_afetadas"),
                item.get("vd"),
                "sim" if item["critico"] else "não",
            ]
            for item in laudo["defeitos"]
        ],
    )

    _escrever_secao(
        writer,
        "JUNTAS E SELANTE (DEFEITO 5)",
        [
            "AMOSTRA",
            "KM_INICIAL",
            "FAIXA",
            "SEVERIDADE",
            "JUNTAS_AFETADAS",
            "DENSIDADE_PCT",
            "EXTENSAO_M",
            "VD",
        ],
        [
            [
                item["amostra"],
                item["km_inicial"],
                item["faixa"],
                item["severidade"],
                item.get("juntas_afetadas"),
                item.get("densidade"),
                item.get("extensao_m"),
                item.get("vd"),
            ]
            for item in laudo["secoes"]["juntas_selante"]["itens"]
        ],
    )

    for chave, titulo in (
        ("defeitos_atipicos", "DEFEITOS ATÍPICOS"),
        ("assentamentos", "ASSENTAMENTOS"),
        ("buracos", "BURACOS"),
    ):
        _escrever_secao(
            writer,
            titulo,
            ["AMOSTRA", "KM_INICIAL", "FAIXA", "TIPO", "DESCRIÇÃO", "SEVERIDADE"],
            [
                [
                    item["amostra"],
                    item["km_inicial"],
                    item["faixa"],
                    item.get("tipo"),
                    item.get("nome"),
                    item.get("severidade"),
                ]
                for item in laudo["secoes"][chave]["itens"]
            ],
        )

    _escrever_secao(
        writer,
        "AMOSTRAS COM ICP <= 40",
        ["AMOSTRA", "KM_INICIAL", "FAIXA", "ICP", "CONCEITO"],
        [
            [item["amostra"], item["km_inicial"], item["faixa"], item["icp"], item["conceito"]]
            for item in laudo["amostras_criticas"]
        ],
    )

    _escrever_secao(
        writer,
        "DISTRIBUIÇÃO POR CONCEITO",
        ["CONCEITO", "QUANTIDADE", "PERCENTUAL", "ICP_MIN", "ICP_MAX"],
        [
            [
                item["conceito"],
                item["quantidade"],
                item["percentual"],
                item["icp_min"],
                item["icp_max"],
            ]
            for item in laudo["distribuicao_conceitos"]
        ],
    )

    return Response(
        content=output.getvalue(),
        media_type="text/csv",
        headers={
            "Content-Disposition": f"attachment; filename=laudo_icp_{base.name}.csv",
            "Content-Type": "text/csv; charset=utf-8",
        },
    )


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
    trecho = next(
        (item for item in resultados if icp_trecho.eh_trecho(item)), None
    )
    amostras_resultados = [
        item for item in resultados if not icp_trecho.eh_trecho(item)
    ]

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
            "N",
            "N_MINIMO",
            "DESVIO_PADRAO",
            "E",
            "METODO",
            "NIVEL",
        ]
    )
    ordem = {"A": 3, "M": 2, "B": 1, "U": 0}
    coluna_tipo = 6
    for item in amostras_resultados:
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
            None,
            None,
            None,
            None,
            None,
            "amostra",
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
            linha[coluna_tipo] = tipo
            linha[coluna_tipo + 1] = DEFEITOS.get(tipo)
            linha[coluna_tipo + 2] = defeito.get("severidade")
            linha[coluna_tipo + 3] = defeito.get("densidade")
            linha[coluna_tipo + 4] = defeito.get("placas_afetadas")
            linha[coluna_tipo + 5] = defeito.get("vd")
            writer.writerow(linha)

    if trecho is not None:
        writer.writerow(
            [
                "TRECHO",
                None,
                trecho.get("km_inicio"),
                trecho.get("km_fim"),
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                trecho.get("icp", trecho.get("valor")),
                trecho.get("conceito"),
                trecho.get("N"),
                trecho.get("n_minimo"),
                trecho.get("desvio_padrao"),
                trecho.get("e"),
                trecho.get("metodo"),
                "trecho",
            ]
        )

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
