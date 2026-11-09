"""ETL dos dados legados em `dados/` para o banco (RDT1-14).

A migração lê as viagens que ainda vivem apenas no volume (`viagem_config.json`,
`lote_*_meta.json`, `analise_completa.json` e `parametros_adicionais.json`) e
grava tudo nas tabelas relacionais usando os mesmos stores das slices:

* viagem/config/blocos/faixas -> :func:`viagem_store.persistir_viagem_em_sessao`;
* análise/detecções/parâmetros -> :func:`analise_store.persistir_analise_em_sessao`.

Propriedades:

* **Idempotente** — cada viagem é reescrita por chaves naturais (`projeto`/`nome`,
  `bloco.indice`, `faixa.arquivo`, `(viagem, km)`), então rodar duas vezes não
  duplica linhas. O advisory lock transacional da viagem também serializa
  execuções concorrentes.
* **Dry-run** — o planejamento é puro (só lê arquivos) e devolve contagens e
  checksums sem tocar no banco.
* **Relatório** — cada execução real gera um relatório JSON com contagens,
  checksums de origem/dataset e o `viagem_id` de cada viagem, que serve de
  manifesto para o rollback.
* **Rollback** — :func:`reverter_relatorio_em_sessao` faz soft delete apenas das
  viagens que a migração *criou* (`criada = true`). Viagens que já existiam no
  banco não são removidas: para elas o rollback autoritativo é restaurar o backup
  anterior (`scripts/db_restore.sh`), já que a migração pode ter atualizado linhas
  pré-existentes.
* Sem banco configurado o modo real falha explicitamente; o dry-run continua
  funcionando só com o sistema de arquivos.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from src.utils.tipo_pista import normalizar_tipo_pista

if TYPE_CHECKING:  # pragma: no cover - apenas para tipagem
    from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

VERSAO_RELATORIO = 1

ARQUIVO_CONFIG = "viagem_config.json"
ARQUIVO_ANALISE = "analise_completa.json"
ARQUIVO_PARAMETROS = "parametros_adicionais.json"
PADRAO_METAS = "lote_*_meta.json"

CAMPOS_CONTAGEM = (
    "blocos",
    "faixas",
    "analise_faixas",
    "deteccoes",
    "parametros",
)


def _agora_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _json_canonico(payload: Any) -> str:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def checksum_json(payload: Any) -> str:
    """Checksum estável de um payload JSON (ordem de chaves e espaços)."""
    return _sha256_bytes(_json_canonico(payload).encode("utf-8"))


def checksum_arquivo(caminho: Path) -> str:
    """SHA-256 do conteúdo bruto do arquivo."""
    return _sha256_bytes(caminho.read_bytes())


def _ler_json(caminho: Path) -> dict[str, Any]:
    try:
        dados = json.loads(caminho.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.warning("Ignorando JSON ilegível %s: %s", caminho, exc)
        return {}
    return dados if isinstance(dados, dict) else {}


def _inteiro(valor: Any, padrao: int = 0) -> int:
    try:
        return int(valor)
    except (TypeError, ValueError):
        return padrao


def _coletar_metas(pasta: Path) -> list[dict[str, Any]]:
    """Lê os ``lote_*_meta.json`` da viagem na ordem dos blocos."""
    metas: list[dict[str, Any]] = []
    for meta_path in sorted(pasta.glob(PADRAO_METAS)):
        meta = _ler_json(meta_path)
        if meta:
            metas.append(meta)
    return metas


def _normalizar_analise(bruta: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Aceita o formato antigo (lista) e o novo (``{deteccoes, faixa_meta}``)."""
    analise: dict[str, list[dict[str, Any]]] = {}
    for arquivo, dados in bruta.items():
        if isinstance(dados, list):
            deteccoes = dados
        elif isinstance(dados, dict):
            deteccoes = dados.get("deteccoes") or []
        else:
            continue
        analise[str(arquivo)] = [det for det in deteccoes if isinstance(det, dict)]
    return analise


def _config_efetiva(nome: str, config: dict[str, Any]) -> dict[str, Any]:
    """Normaliza o `viagem_config.json` (inclusive o vocabulário legado)."""
    bruto = config.get("tipo_pista", config.get("tipo_faixa"))
    try:
        tipo_pista = normalizar_tipo_pista(bruto)
    except ValueError:
        # Dado legado inválido não impede a ETL: cai no padrão e o relatório
        # mantém o valor bruto em ``avisos``.
        tipo_pista = "simples"

    return {
        "nome": nome,
        "km_inicial": config.get("km_inicial"),
        "km_final": config.get("km_final"),
        "tipo_pista": tipo_pista,
        "sentido": config.get("sentido") or "crescente",
        "faixa": config.get("faixa"),
    }


def _arquivos_origem(pasta: Path) -> list[Path]:
    arquivos: list[Path] = []
    config = pasta / ARQUIVO_CONFIG
    if config.is_file():
        arquivos.append(config)
    arquivos.extend(sorted(pasta.glob(PADRAO_METAS)))
    for nome in (ARQUIVO_ANALISE, ARQUIVO_PARAMETROS):
        caminho = pasta / nome
        if caminho.is_file():
            arquivos.append(caminho)
    return arquivos


def _contagens(
    config: dict[str, Any],
    metas: list[dict[str, Any]],
    analise: dict[str, list[dict[str, Any]]],
    parametros: dict[str, Any],
) -> dict[str, int]:
    blocos: set[int] = set()
    faixas = 0
    for meta in metas:
        bloco_meta = meta.get("bloco") or {}
        blocos.add(_inteiro(bloco_meta.get("indice", meta.get("indice", 0))))
        faixas += sum(
            1 for faixa in (meta.get("faixas") or []) if faixa.get("arquivo")
        )
    return {
        "blocos": len(blocos),
        "faixas": faixas,
        "analise_faixas": len(analise),
        "deteccoes": sum(len(deteccoes) for deteccoes in analise.values()),
        "parametros": len(parametros),
    }


def planejar_viagem(pasta: Path) -> dict[str, Any] | None:
    """Monta o plano de uma viagem (contagens + checksums), sem tocar no banco."""
    pasta = pasta.resolve()
    if not pasta.is_dir():
        return None
    arquivos = _arquivos_origem(pasta)
    if not arquivos:
        return None

    config_bruta = _ler_json(pasta / ARQUIVO_CONFIG)
    metas = _coletar_metas(pasta)
    analise = _normalizar_analise(_ler_json(pasta / ARQUIVO_ANALISE))
    parametros = _ler_json(pasta / ARQUIVO_PARAMETROS)
    config = _config_efetiva(pasta.name, config_bruta)

    checksums = {arquivo.name: checksum_arquivo(arquivo) for arquivo in arquivos}
    payload = {
        "nome": pasta.name,
        "pasta": pasta.name,
        "config": config,
        "lotes": metas,
        "analise": analise,
        "parametros": parametros,
    }

    return {
        "nome": pasta.name,
        "pasta": pasta.name,
        "caminho": str(pasta),
        "config": config,
        "lotes": metas,
        "analise": analise,
        "parametros": parametros,
        "contagens": _contagens(config, metas, analise, parametros),
        "arquivos": checksums,
        "origem_checksum": checksum_json(checksums),
        "dados_checksum": checksum_json(payload),
    }


def coletar_planos(dados_dir: Path) -> list[dict[str, Any]]:
    """Descobre as viagens legadas no volume, em ordem estável de nome."""
    if not dados_dir.is_dir():
        raise FileNotFoundError(f"Diretório de dados não encontrado: {dados_dir}")
    planos: list[dict[str, Any]] = []
    for pasta in sorted(dados_dir.iterdir(), key=lambda item: item.name):
        if not pasta.is_dir() or pasta.name.startswith("."):
            continue
        plano = planejar_viagem(pasta)
        if plano is not None:
            planos.append(plano)
    return planos


def migrar_viagem_em_sessao(session: "Session", plano: dict[str, Any]) -> dict[str, Any]:
    """Grava uma viagem na transação corrente.

    A concatenação e a análise rodam na mesma transação para que o advisory lock
    da viagem (adquirido por ``persistir_viagem_em_sessao``) também serialize a
    escrita de detecções/parâmetros em execuções concorrentes.
    """
    # Import tardio: o planejamento/dry-run não depende de SQLAlchemy.
    from src.db.repositories import ProjetoRepository, ViagemRepository
    from src.services import analise_store, viagem_store

    # A identidade usada pela ETL é ``(projeto, nome)``, a mesma do upsert de
    # `persistir_viagem_em_sessao` — e não só a pasta.
    projeto = ProjetoRepository(session).get_by_nome(viagem_store.PROJETO_PADRAO)
    existente = (
        ViagemRepository(session).get_by_nome(projeto.id, plano["nome"])
        if projeto is not None
        else None
    )

    viagem_id = viagem_store.persistir_viagem_em_sessao(
        session,
        projeto_nome=viagem_store.PROJETO_PADRAO,
        viagem_nome=plano["nome"],
        pasta=plano["pasta"],
        config=plano["config"],
        lotes=plano["lotes"],
    )
    faixas_analisadas, parametros_gravados = analise_store.persistir_analise_em_sessao(
        session,
        viagem_nome=plano["nome"],
        deteccoes_por_imagem=plano["analise"],
        parametros_por_km=plano["parametros"] or None,
    )

    contagens = dict(plano["contagens"])
    contagens["faixas_analisadas"] = faixas_analisadas
    contagens["parametros_gravados"] = parametros_gravados

    return {
        "viagem_id": str(viagem_id),
        "criada": existente is None,
        "contagens": contagens,
    }


def _somar_totais(viagens: list[dict[str, Any]]) -> dict[str, int]:
    totais = {campo: 0 for campo in CAMPOS_CONTAGEM}
    for viagem in viagens:
        for campo in CAMPOS_CONTAGEM:
            totais[campo] += int(viagem["contagens"].get(campo, 0))
    totais["viagens"] = len(viagens)
    return totais


def gerar_relatorio(
    planos: list[dict[str, Any]],
    resultados: list[dict[str, Any]] | None = None,
    *,
    dry_run: bool,
    dados_dir: Path,
) -> dict[str, Any]:
    """Consolida planos (dry-run) ou planos + gravações em um relatório."""
    por_nome = {resultado.get("nome") or f"#{i}": resultado for i, resultado in enumerate(resultados or [])}
    viagens: list[dict[str, Any]] = []
    for plano in planos:
        item: dict[str, Any] = {
            "nome": plano["nome"],
            "pasta": plano["pasta"],
            "caminho": plano["caminho"],
            "contagens": dict(plano["contagens"]),
            "arquivos": plano["arquivos"],
            "origem_checksum": plano["origem_checksum"],
            "dados_checksum": plano["dados_checksum"],
        }
        resultado = por_nome.get(plano["nome"])
        if resultado is not None:
            item["viagem_id"] = resultado["viagem_id"]
            item["criada"] = resultado["criada"]
            item["contagens"].update(
                {
                    "faixas_analisadas": resultado["contagens"].get("faixas_analisadas", 0),
                    "parametros_gravados": resultado["contagens"].get("parametros_gravados", 0),
                }
            )
        viagens.append(item)

    return {
        "versao": VERSAO_RELATORIO,
        "gerado_em": _agora_iso(),
        "dry_run": dry_run,
        "dados_dir": str(dados_dir),
        "viagens": viagens,
        "totais": _somar_totais(viagens),
        "checksum_consolidado": checksum_json(
            [(viagem["nome"], viagem["dados_checksum"]) for viagem in viagens]
        ),
    }


def executar_migracao(
    dados_dir: Path,
    *,
    dry_run: bool = False,
    planos: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Executa (ou simula) a migração e devolve o relatório."""
    dados_dir = dados_dir.resolve()
    planos = coletar_planos(dados_dir) if planos is None else planos

    if dry_run:
        return gerar_relatorio(planos, dry_run=True, dados_dir=dados_dir)

    from src.db.session import session_scope

    resultados: list[dict[str, Any]] = []
    for plano in planos:
        with session_scope() as session:
            resultado = migrar_viagem_em_sessao(session, plano)
        resultado["nome"] = plano["nome"]
        resultados.append(resultado)
        logger.info(
            "Migrada viagem %s (%s blocos, %s faixas)",
            plano["nome"],
            plano["contagens"]["blocos"],
            plano["contagens"]["faixas"],
        )

    return gerar_relatorio(planos, resultados, dry_run=False, dados_dir=dados_dir)


def _soft_delete(session: "Session", modelo: Any, condicao: Any, agora: datetime) -> int:
    from sqlalchemy import update

    resultado = session.execute(
        update(modelo)
        .where(condicao, modelo.deleted_at.is_(None))
        .values(deleted_at=agora)
    )
    return int(resultado.rowcount or 0)


def reverter_relatorio_em_sessao(
    session: "Session", relatorio: dict[str, Any]
) -> dict[str, int]:
    """Desfaz uma migração real usando o relatório como manifesto.

    Só remove (soft delete) viagens que não existiam antes da migração
    (``criada = true``). Viagens pré-existentes são ignoradas — restaurar o
    backup anterior é o rollback autoritativo para elas.
    """
    if relatorio.get("dry_run"):
        raise ValueError("Relatório de dry-run não pode ser revertido: nada foi gravado")

    from sqlalchemy import select

    from src.db.models import (
        Bloco,
        Deteccao,
        Faixa,
        ParametroKm,
        ResultadoAnalise,
        Viagem,
    )

    agora = datetime.now(timezone.utc)
    contagens = {
        "viagens": 0,
        "blocos": 0,
        "faixas": 0,
        "deteccoes": 0,
        "parametros": 0,
        "resultados": 0,
        "ignoradas": 0,
    }

    for item in relatorio.get("viagens", []):
        if not item.get("criada") or not item.get("viagem_id"):
            contagens["ignoradas"] += 1
            continue

        viagem_id = uuid.UUID(str(item["viagem_id"]))
        faixas_da_viagem = select(Faixa.id).where(Faixa.viagem_id == viagem_id)

        contagens["deteccoes"] += _soft_delete(
            session, Deteccao, Deteccao.faixa_id.in_(faixas_da_viagem), agora
        )
        contagens["parametros"] += _soft_delete(
            session, ParametroKm, ParametroKm.viagem_id == viagem_id, agora
        )
        contagens["resultados"] += _soft_delete(
            session, ResultadoAnalise, ResultadoAnalise.viagem_id == viagem_id, agora
        )
        contagens["faixas"] += _soft_delete(
            session, Faixa, Faixa.viagem_id == viagem_id, agora
        )
        contagens["blocos"] += _soft_delete(
            session, Bloco, Bloco.viagem_id == viagem_id, agora
        )
        contagens["viagens"] += _soft_delete(
            session, Viagem, Viagem.id == viagem_id, agora
        )

    return contagens
