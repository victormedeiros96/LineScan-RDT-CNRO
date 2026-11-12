"""Testes do slice de histórico de jobs e resultados IGG/ICP (RDT1-13).

Cobrem o ciclo de vida do job no banco (queued -> started -> finished/failed),
a independência do Redis (leitura só pelo banco), a retenção por soft delete e a
gravação/leitura/reconciliação dos resultados por KM.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.orm import Session

from src.db.repositories import JobRepository, ProjetoRepository, ViagemRepository
from src.services import job_store, resultado_store


def _viagem(session: Session, nome: str):
    projeto = ProjetoRepository(session).get_or_create(f"Projeto {nome}")
    return ViagemRepository(session).create(
        projeto_id=projeto.id, nome=nome, pasta=nome
    )


# ── Jobs ──


def test_registra_ciclo_de_vida_do_job(session: Session) -> None:
    job = job_store.registrar_job_em_sessao(
        session,
        rq_job_id="rq-123",
        viagem_nome="Viagem A",
        tipo="inferencia",
        meta={"viagem": "Viagem A", "tipo_modelo": "igg"},
    )
    assert job.status == "queued"

    job_store.marcar_iniciado_em_sessao(session, "rq-123")
    job_store.atualizar_progresso_em_sessao(
        session, "rq-123", {"current_lote": 2, "total_lotes": 5, "progress_msg": "x"}
    )
    job_store.finalizar_em_sessao(session, "rq-123", {"total_imagens": 10})

    entrada = job_store.obter_job("rq-123", session=session)
    assert entrada is not None
    assert entrada["status"] == "finished"
    assert entrada["resultado"] == {"total_imagens": 10}
    assert entrada["progress"]["current_lote"] == 2
    assert entrada["progress"]["total_lotes"] == 5
    assert entrada["finalizado_em"] is not None


def test_falha_registra_erro(session: Session) -> None:
    job_store.registrar_job_em_sessao(
        session, rq_job_id="rq-fail", viagem_nome="V", tipo="upload_zip"
    )
    job_store.falhar_em_sessao(session, "rq-fail", "ValueError: boom")

    entrada = job_store.obter_job("rq-fail", session=session)
    assert entrada is not None
    assert entrada["status"] == "failed"
    assert entrada["error"] == "ValueError: boom"
    assert "resultado" not in entrada


def test_registrar_job_idempotente_por_rq_id(session: Session) -> None:
    primeiro = job_store.registrar_job_em_sessao(
        session, rq_job_id="rq-dupe", viagem_nome="V", tipo="concatenacao"
    )
    segundo = job_store.registrar_job_em_sessao(
        session, rq_job_id="rq-dupe", viagem_nome="V", tipo="concatenacao"
    )
    assert primeiro.id == segundo.id


def test_listar_jobs_do_banco(session: Session) -> None:
    job_store.registrar_job_em_sessao(
        session, rq_job_id="rq-a", viagem_nome="V", tipo="concatenacao"
    )
    job_store.registrar_job_em_sessao(
        session, rq_job_id="rq-b", viagem_nome="V", tipo="inferencia"
    )
    jobs = job_store.listar_jobs(session=session)
    assert jobs is not None
    assert {job["job_id"] for job in jobs} == {"rq-a", "rq-b"}


def test_retencao_soft_delete_jobs_antigos(session: Session) -> None:
    job = job_store.registrar_job_em_sessao(
        session, rq_job_id="rq-old", viagem_nome="V", tipo="concatenacao"
    )
    job_store.finalizar_em_sessao(session, "rq-old", {"total_lotes": 1})
    job.created_at = datetime.now(timezone.utc) - timedelta(days=40)
    session.flush()

    removidos = job_store.limpar_jobs_antigos_em_sessao(session, dias=30)
    assert removidos == 1
    assert job_store.obter_job("rq-old", session=session) is None


def test_remover_job_por_rq_id(session: Session) -> None:
    job_store.registrar_job_em_sessao(
        session, rq_job_id="rq-del", viagem_nome="V", tipo="concatenacao"
    )
    assert JobRepository(session).soft_delete_by_rq_id("rq-del") is True
    assert job_store.obter_job("rq-del", session=session) is None


# ── Resultados IGG/ICP ──


def test_persiste_e_le_resultados_igg(session: Session) -> None:
    _viagem(session, "Viagem IGG")

    gravados = resultado_store.persistir_resultados_em_sessao(
        session,
        viagem_nome="Viagem IGG",
        tipo_analise="igg",
        resultados=[
            {
                "km": 10,
                "igg": 12.5,
                "conceito": "Ótimo",
                "n_estacoes": 50,
                "grupos": [{"codigo": "T1", "igi": 12.5}],
                "item_9_flecha": 0,
            },
            {"km": 11, "igg": 85.0, "conceito": "Ruim", "n_estacoes": 50},
        ],
    )
    assert gravados == 2

    itens = resultado_store.carregar_resultados("Viagem IGG", "igg", session=session)
    assert itens is not None
    assert [item["km"] for item in itens] == [10, 11]
    assert itens[0]["valor"] == 12.5
    assert itens[0]["norma"] == resultado_store.NORMA_IGG
    assert itens[0]["conceito"] == "Ótimo"
    assert itens[0]["grupos"] == [{"codigo": "T1", "igi": 12.5}]


def test_recalcular_reconcilia_kms_removidos(session: Session) -> None:
    _viagem(session, "Viagem Reconcilia")

    resultado_store.persistir_resultados_em_sessao(
        session,
        viagem_nome="Viagem Reconcilia",
        tipo_analise="igg",
        resultados=[
            {"km": 10, "igg": 10.0},
            {"km": 11, "igg": 20.0},
        ],
    )
    resultado_store.persistir_resultados_em_sessao(
        session,
        viagem_nome="Viagem Reconcilia",
        tipo_analise="igg",
        resultados=[{"km": 10, "igg": 15.0}],
    )

    itens = resultado_store.carregar_resultados(
        "Viagem Reconcilia", "igg", session=session
    )
    assert itens is not None
    assert [(item["km"], item["valor"]) for item in itens] == [(10, 15.0)]


def test_km_isolado_nao_remove_os_demais(session: Session) -> None:
    _viagem(session, "Viagem Isolado")

    resultado_store.persistir_resultados_em_sessao(
        session,
        viagem_nome="Viagem Isolado",
        tipo_analise="igg",
        resultados=[{"km": 10, "igg": 10.0}, {"km": 11, "igg": 20.0}],
    )
    resultado_store.persistir_resultados_em_sessao(
        session,
        viagem_nome="Viagem Isolado",
        tipo_analise="igg",
        resultados=[{"km": 10, "igg": 12.0}],
        reconciliar=False,
    )

    itens = resultado_store.carregar_resultados(
        "Viagem Isolado", "igg", session=session
    )
    assert itens is not None
    assert [item["km"] for item in itens] == [10, 11]


def test_resultado_icp_usando_campo_valor(session: Session) -> None:
    _viagem(session, "Viagem ICP")

    resultado_store.persistir_resultados_em_sessao(
        session,
        viagem_nome="Viagem ICP",
        tipo_analise="icp",
        resultados=[
            {"km": 5, "valor": 3.14, "conceito": "Bom"},
        ],
    )

    itens = resultado_store.carregar_resultados("Viagem ICP", "icp", session=session)
    assert itens is not None
    assert itens[0]["tipo_analise"] == "icp"
    assert itens[0]["valor"] == 3.14
    assert itens[0]["icp"] == 3.14
    assert itens[0]["amostra"] is None
    assert itens[0]["norma"] == resultado_store.NORMA_ICP


def test_persiste_icp_por_amostra_no_mesmo_km(session: Session) -> None:
    _viagem(session, "Viagem ICP Amostras")

    gravados = resultado_store.persistir_resultados_em_sessao(
        session,
        viagem_nome="Viagem ICP Amostras",
        tipo_analise="icp",
        resultados=[
            {"km": 33, "amostra": "ICP_1", "icp": 84.11, "conceito": "Muito Bom"},
            {"km": 33, "amostra": "ICP_2", "icp": 71.99, "conceito": "Muito Bom"},
            {"km": 33, "amostra": "ICP_1", "icp": 80.0, "conceito": "Muito Bom"},
        ],
    )
    assert gravados == 3

    itens = resultado_store.carregar_resultados(
        "Viagem ICP Amostras", "icp", session=session
    )
    assert itens is not None
    assert [(item["km"], item["amostra"], item["icp"]) for item in itens] == [
        (33, "ICP_1", 80.0),
        (33, "ICP_2", 71.99),
    ]
    assert itens[0]["norma"] == resultado_store.NORMA_ICP


def test_icp_sem_km_usa_sentinela_zero(session: Session) -> None:
    _viagem(session, "Viagem ICP PPV")

    resultado_store.persistir_resultados_em_sessao(
        session,
        viagem_nome="Viagem ICP PPV",
        tipo_analise="icp",
        resultados=[
            {
                "amostra": "ICP_1",
                "km": None,
                "km_informado": False,
                "icp": 84.11,
                "conceito": "Muito Bom",
            }
        ],
    )

    itens = resultado_store.carregar_resultados(
        "Viagem ICP PPV", "icp", session=session
    )
    assert itens is not None
    assert itens[0]["km"] == 0
    assert itens[0]["km_informado"] is False


def test_tipo_de_analise_invalido(session: Session) -> None:
    _viagem(session, "Viagem Invalida")
    with pytest.raises(ValueError):
        resultado_store.persistir_resultados_em_sessao(
            session,
            viagem_nome="Viagem Invalida",
            tipo_analise="iri",
            resultados=[{"km": 1, "valor": 1.0}],
        )


def test_viagem_fora_do_banco_retorna_none(session: Session) -> None:
    assert resultado_store.carregar_resultados("inexistente", "igg", session=session) is None
