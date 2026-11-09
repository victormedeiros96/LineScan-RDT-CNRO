"""Testes da ETL de dados legados e do rollback (RDT1-14).

Fixtures em disco reproduzem o layout real de `dados/` (config, `lote_*_meta.json`,
`analise_completa.json` e `parametros_adicionais.json`). Cobrem dry-run sem
escrita, migração completa, idempotência (rodar 2x), concorrência de dois jobs e
rollback por relatório.
"""

from __future__ import annotations

import json
import threading
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from src.db.models import Bloco, Deteccao, Faixa, ParametroKm, Viagem
from src.services import migracao_legado, viagem_store


def _config(nome: str) -> dict[str, Any]:
    return {
        "nome": nome,
        "km_inicial": 100.0,
        "km_final": 100.4,
        "tipo_pista": "duplicada",
        "sentido": "crescente",
        "faixa": 1,
    }


def _metas() -> list[dict[str, Any]]:
    return [
        {
            "bloco": {"indice": 0, "lado": "left", "km_inicio": 100.0, "km_fim": 100.02},
            "faixas": [
                {
                    "bloco_indice": 0,
                    "faixa_index": 0,
                    "arquivo": "lote_0000_faixa_0.png",
                    "km_inicio": 100.0,
                    "km_fim": 100.005,
                    "altura_px": 5120,
                    "imagens_raw": ["img_0001.png"],
                    "offset_y_px": 0,
                    "percentual_ultima_img": 1.0,
                }
            ],
        },
        {
            "bloco": {"indice": 1, "lado": "left", "km_inicio": 100.02, "km_fim": 100.04},
            "faixas": [
                {
                    "bloco_indice": 1,
                    "faixa_index": 0,
                    "arquivo": "lote_0001_faixa_0.png",
                    "km_inicio": 100.02,
                    "km_fim": 100.025,
                    "altura_px": 5120,
                    "imagens_raw": ["img_0002.png"],
                    "offset_y_px": 0,
                    "percentual_ultima_img": 0.5,
                }
            ],
        },
    ]


def _deteccao(classe: str = "Trincas") -> dict[str, Any]:
    return {
        "classe": classe,
        "area": 120,
        "area_pixels": 120,
        "area_m2": 0.0012,
        "confidence": 0.9,
        "score": 0.9,
        "confidence_max": 0.95,
        "linha": 2,
        "coluna": 1,
        "global_box": [10, 20, 30, 40],
        "global_polygon": [[10, 20], [30, 20], [30, 40]],
        "direction": "",
    }


def _analise() -> dict[str, Any]:
    return {
        # Formato novo (dict com deteccoes).
        "lote_0000_faixa_0.png": {"deteccoes": [_deteccao(), _deteccao("Remendo")]},
        # Formato antigo (lista).
        "lote_0001_faixa_0.png": [_deteccao("Panela")],
    }


def _parametros() -> dict[str, Any]:
    return {
        "100": {"TRI (mm)": [1.0, 2.0], "TRE (mm)": [1.5, 2.5], "IRI (3E)": [3.2]},
        "101": {"TRI (mm)": [2.0, 3.0], "TRE (mm)": [2.5, 3.5]},
    }


def _criar_pasta(dados_dir: Path, nome: str) -> Path:
    pasta = dados_dir / nome
    pasta.mkdir(parents=True)
    (pasta / "viagem_config.json").write_text(
        json.dumps(_config(nome), ensure_ascii=False), encoding="utf-8"
    )
    for indice, meta in enumerate(_metas()):
        (pasta / f"lote_{indice:04d}_meta.json").write_text(
            json.dumps(meta, ensure_ascii=False), encoding="utf-8"
        )
    (pasta / "analise_completa.json").write_text(
        json.dumps(_analise(), ensure_ascii=False), encoding="utf-8"
    )
    (pasta / "parametros_adicionais.json").write_text(
        json.dumps(_parametros(), ensure_ascii=False), encoding="utf-8"
    )
    return pasta


def _plano(dados_dir: Path, nome: str) -> dict[str, Any]:
    plano = migracao_legado.planejar_viagem(dados_dir / nome)
    assert plano is not None
    return plano


def _contar(session: Session, modelo: Any, **filtros: Any) -> int:
    stmt = select(func.count()).select_from(modelo).where(
        modelo.deleted_at.is_(None), *[getattr(modelo, k) == v for k, v in filtros.items()]
    )
    return int(session.scalar(stmt) or 0)


# ── Planejamento / dry-run ──


def test_dry_run_planeja_sem_gravar(session: Session, tmp_path: Path) -> None:
    nome = f"Viagem {uuid.uuid4().hex[:8]}"
    _criar_pasta(tmp_path, nome)

    relatorio = migracao_legado.executar_migracao(tmp_path, dry_run=True)

    assert relatorio["dry_run"] is True
    assert relatorio["totais"]["viagens"] == 1
    assert relatorio["totais"]["blocos"] == 2
    assert relatorio["totais"]["faixas"] == 2
    assert relatorio["totais"]["deteccoes"] == 3
    assert relatorio["totais"]["analise_faixas"] == 2
    assert relatorio["totais"]["parametros"] == 2
    assert relatorio["checksum_consolidado"]

    viagem = relatorio["viagens"][0]
    assert set(viagem["arquivos"]) == {
        "viagem_config.json",
        "lote_0000_meta.json",
        "lote_0001_meta.json",
        "analise_completa.json",
        "parametros_adicionais.json",
    }
    assert "viagem_id" not in viagem

    # Nada foi gravado no banco.
    assert _contar(session, Viagem) == 0


def test_checksums_estaveis_entre_planejamentos(tmp_path: Path) -> None:
    nome = f"Viagem {uuid.uuid4().hex[:8]}"
    _criar_pasta(tmp_path, nome)

    primeiro = _plano(tmp_path, nome)
    segundo = _plano(tmp_path, nome)

    assert primeiro["origem_checksum"] == segundo["origem_checksum"]
    assert primeiro["dados_checksum"] == segundo["dados_checksum"]


def test_planejamento_ignora_pasta_vazia_e_arquivo(tmp_path: Path) -> None:
    (tmp_path / "vazia").mkdir()
    (tmp_path / "solto.json").write_text("{}", encoding="utf-8")
    assert migracao_legado.coletar_planos(tmp_path) == []


# ── Migração / idempotência ──


def test_migra_viagem_completa(session: Session, tmp_path: Path) -> None:
    nome = f"Viagem {uuid.uuid4().hex[:8]}"
    _criar_pasta(tmp_path, nome)
    plano = _plano(tmp_path, nome)

    resultado = migracao_legado.migrar_viagem_em_sessao(session, plano)
    assert resultado["criada"] is True
    assert resultado["contagens"]["faixas_analisadas"] == 2
    assert resultado["contagens"]["parametros_gravados"] == 2

    config = viagem_store.ler_config_viagem(nome, session=session)
    assert config is not None
    # Vocabulário legado "duplicada" normalizado para "dupla".
    assert config["tipo_pista"] == "dupla"

    faixas = viagem_store.listar_faixas_viagem(nome, session=session)
    assert faixas is not None
    assert faixas["total_faixas"] == 2

    assert _contar(session, Bloco) == 2
    assert _contar(session, Faixa) == 2
    assert _contar(session, Deteccao) == 3
    assert _contar(session, ParametroKm) == 2


def test_migracao_idempotente_duas_vezes(session: Session, tmp_path: Path) -> None:
    nome = f"Viagem {uuid.uuid4().hex[:8]}"
    _criar_pasta(tmp_path, nome)
    plano = _plano(tmp_path, nome)

    primeiro = migracao_legado.migrar_viagem_em_sessao(session, plano)
    segundo = migracao_legado.migrar_viagem_em_sessao(session, plano)

    assert primeiro["viagem_id"] == segundo["viagem_id"]
    assert segundo["criada"] is False
    assert _contar(session, Viagem) == 1
    assert _contar(session, Bloco) == 2
    assert _contar(session, Faixa) == 2
    assert _contar(session, Deteccao) == 3
    assert _contar(session, ParametroKm) == 2

    relatorio = migracao_legado.gerar_relatorio(
        [plano], [dict(primeiro, nome=nome)], dry_run=False, dados_dir=tmp_path
    )
    assert relatorio["checksum_consolidado"] == migracao_legado.gerar_relatorio(
        [plano], [dict(segundo, nome=nome)], dry_run=False, dados_dir=tmp_path
    )["checksum_consolidado"]


def test_migracao_concorrente_nao_duplica(engine: Engine, tmp_path: Path) -> None:
    nome = f"Viagem {uuid.uuid4().hex[:8]}"
    _criar_pasta(tmp_path, nome)
    plano = _plano(tmp_path, nome)
    erros: list[BaseException] = []
    barreira = threading.Barrier(2)

    def migrar() -> None:
        try:
            with Session(engine) as sessao:
                barreira.wait(timeout=10)
                migracao_legado.migrar_viagem_em_sessao(sessao, plano)
                sessao.commit()
        except BaseException as exc:  # noqa: BLE001 - reportado no assert
            erros.append(exc)

    threads = [threading.Thread(target=migrar) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)

    assert not erros, erros

    with Session(engine) as sessao:
        assert _contar(sessao, Viagem) == 1
        assert _contar(sessao, Bloco) == 2
        assert _contar(sessao, Faixa) == 2
        # O advisory lock da viagem serializa a escrita; não há detecções duplicadas.
        assert _contar(sessao, Deteccao) == 3
        assert _contar(sessao, ParametroKm) == 2

        # Limpa o que este teste commitou (os demais usam transação revertida).
        viagem = sessao.scalars(select(Viagem).where(Viagem.pasta == nome)).one()
        relatorio = {
            "dry_run": False,
            "viagens": [{"criada": True, "viagem_id": str(viagem.id)}],
        }
        migracao_legado.reverter_relatorio_em_sessao(sessao, relatorio)
        sessao.commit()


# ── Rollback ──


def test_rollback_remove_somente_viagens_criadas(session: Session, tmp_path: Path) -> None:
    nome = f"Viagem {uuid.uuid4().hex[:8]}"
    _criar_pasta(tmp_path, nome)
    plano = _plano(tmp_path, nome)
    resultado = migracao_legado.migrar_viagem_em_sessao(session, plano)
    relatorio = migracao_legado.gerar_relatorio(
        [plano], [dict(resultado, nome=nome)], dry_run=False, dados_dir=tmp_path
    )

    contagens = migracao_legado.reverter_relatorio_em_sessao(session, relatorio)

    assert contagens["viagens"] == 1
    assert contagens["blocos"] == 2
    assert contagens["faixas"] == 2
    assert contagens["deteccoes"] == 3
    assert contagens["parametros"] == 2
    assert _contar(session, Viagem) == 0
    assert _contar(session, Bloco) == 0
    assert _contar(session, Faixa) == 0
    assert _contar(session, Deteccao) == 0
    assert _contar(session, ParametroKm) == 0

    # Reverter de novo é inofensivo (idempotente).
    assert migracao_legado.reverter_relatorio_em_sessao(session, relatorio)["viagens"] == 0


def test_rollback_ignora_viagem_preexistente(session: Session, tmp_path: Path) -> None:
    nome = f"Viagem {uuid.uuid4().hex[:8]}"
    _criar_pasta(tmp_path, nome)
    plano = _plano(tmp_path, nome)

    # Simula uma viagem que já existia antes da ETL.
    viagem_store.persistir_viagem_em_sessao(
        session,
        projeto_nome=viagem_store.PROJETO_PADRAO,
        viagem_nome=nome,
        pasta=nome,
        config=plano["config"],
        lotes=plano["lotes"],
    )
    resultado = migracao_legado.migrar_viagem_em_sessao(session, plano)
    assert resultado["criada"] is False

    relatorio = migracao_legado.gerar_relatorio(
        [plano], [dict(resultado, nome=nome)], dry_run=False, dados_dir=tmp_path
    )
    contagens = migracao_legado.reverter_relatorio_em_sessao(session, relatorio)

    assert contagens["ignoradas"] == 1
    assert contagens["viagens"] == 0
    assert _contar(session, Viagem) == 1


def test_rollback_recusa_relatorio_de_dry_run(session: Session, tmp_path: Path) -> None:
    nome = f"Viagem {uuid.uuid4().hex[:8]}"
    _criar_pasta(tmp_path, nome)
    relatorio = migracao_legado.executar_migracao(tmp_path, dry_run=True)

    try:
        migracao_legado.reverter_relatorio_em_sessao(session, relatorio)
    except ValueError:
        pass
    else:  # pragma: no cover - falha explícita
        raise AssertionError("Esperava ValueError para relatório de dry-run")
