"""CLI da ETL dos dados legados em ``dados/`` para o banco (RDT1-14).

Uso (dentro do container, com ``DATABASE_URL`` definido):

    python -m src.migrar_dados_legados --dry-run
    python -m src.migrar_dados_legados --report migracao_relatorio.json
    python -m src.migrar_dados_legados --rollback migracao_relatorio.json

``--dry-run`` não toca no banco: lê os arquivos, mostra contagens e checksums e
imprime o relatório. A execução real grava viagem/config/blocos/faixas e
análise/detecções/parâmetros e grava o relatório (manifesto de rollback).
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from src.services import migracao_legado


def _caminho_relatorio(padrao: Path | None) -> Path:
    if padrao is not None:
        return padrao
    carimbo = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return Path(f"migracao_relatorio_{carimbo}.json")


def _imprimir_resumo(relatorio: dict, caminho: Path | None) -> None:
    resumo = {
        "dry_run": relatorio["dry_run"],
        "dados_dir": relatorio["dados_dir"],
        "viagens": relatorio["totais"]["viagens"],
        "totais": relatorio["totais"],
        "checksum_consolidado": relatorio["checksum_consolidado"],
        "relatorio": str(caminho) if caminho else None,
    }
    print(json.dumps(resumo, ensure_ascii=False, indent=2))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Migra os dados legados de dados/ para o PostgreSQL (RDT1-14)."
    )
    parser.add_argument(
        "--dados-dir",
        type=Path,
        default=None,
        help="Diretório de dados (padrão: DADOS_DIR / configuração da aplicação).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Apenas planeja e relata contagens/checksums; não grava no banco.",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=None,
        help="Caminho do relatório JSON (obrigatório na prática para o rollback).",
    )
    parser.add_argument(
        "--rollback",
        type=Path,
        default=None,
        metavar="RELATORIO",
        help="Desfaz a migração descrita no relatório (soft delete das viagens criadas).",
    )
    args = parser.parse_args(argv)

    if args.rollback is not None:
        relatorio = json.loads(args.rollback.read_text(encoding="utf-8"))
        from src.db.session import session_scope

        with session_scope() as session:
            contagens = migracao_legado.reverter_relatorio_em_sessao(session, relatorio)
        print(json.dumps({"rollback": str(args.rollback), "contagens": contagens}, ensure_ascii=False, indent=2))
        return 0

    dados_dir = args.dados_dir
    if dados_dir is None:
        from src.core.config import get_settings

        dados_dir = get_settings().dados_dir
    relatorio = migracao_legado.executar_migracao(dados_dir, dry_run=args.dry_run)

    caminho = None if args.dry_run else _caminho_relatorio(args.report)
    if caminho is not None:
        caminho.parent.mkdir(parents=True, exist_ok=True)
        caminho.write_text(
            json.dumps(relatorio, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    _imprimir_resumo(relatorio, caminho)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
