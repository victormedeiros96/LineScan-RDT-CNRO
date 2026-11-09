from __future__ import annotations

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.db.models import Deteccao
from src.db.repositories import (
    ArquivoUploadRepository,
    BlocoRepository,
    DeteccaoRepository,
    FaixaRepository,
    JobRepository,
    ParametroKmRepository,
    ProjetoRepository,
    ResultadoAnaliseRepository,
    ViagemRepository,
)


def _fluxo_base(session: Session):
    projeto = ProjetoRepository(session).get_or_create("Trecho A")
    viagem = ViagemRepository(session).create(projeto_id=projeto.id, nome="Viagem 1")
    bloco = BlocoRepository(session).upsert(
        viagem_id=viagem.id, indice=0, km_inicio=0, km_fim=0.02
    )
    faixa = FaixaRepository(session).upsert(
        viagem_id=viagem.id,
        bloco_id=bloco.id,
        faixa_index=0,
        arquivo="lote_0000_faixa_0.png",
    )
    return projeto, viagem, bloco, faixa


# ── Camada importável sem banco ──


def test_import_camada_sem_banco() -> None:
    import importlib

    importlib.import_module("src.db.models")
    importlib.import_module("src.db.repositories")


# ── Projeto / viagem ──


def test_projeto_get_or_create_idempotente(session: Session) -> None:
    repo = ProjetoRepository(session)
    a = repo.get_or_create("Trecho A")
    b = repo.get_or_create("  trecho a  ")
    assert a.id == b.id
    assert len(repo.list()) == 1


def test_viagem_upsert_idempotente(session: Session) -> None:
    projeto = ProjetoRepository(session).get_or_create("P")
    repo = ViagemRepository(session)
    v1 = repo.upsert(projeto_id=projeto.id, nome="V", pasta="V", tipo_pista="dupla", faixa=1)
    v2 = repo.upsert(projeto_id=projeto.id, nome="v", km_inicial=1.0, km_final=2.0)
    assert v1.id == v2.id
    assert float(v2.km_inicial) == 1.0
    assert v2.tipo_pista == "dupla"
    assert len(repo.list_by_projeto(projeto.id)) == 1


def test_viagem_nome_unico_por_projeto(session: Session) -> None:
    projeto = ProjetoRepository(session).get_or_create("P")
    repo = ViagemRepository(session)
    repo.create(projeto_id=projeto.id, nome="V")
    with pytest.raises(IntegrityError):
        with session.begin_nested():
            repo.create(projeto_id=projeto.id, nome="v")


# ── Bloco / faixa ──


def test_bloco_e_faixa_upsert(session: Session) -> None:
    _, viagem, _, _ = _fluxo_base(session)
    brepo = BlocoRepository(session)
    b = brepo.upsert(viagem_id=viagem.id, indice=1, km_inicio=0.02, km_fim=0.04)
    assert [x.indice for x in brepo.list_by_viagem(viagem.id)] == [0, 1]

    frepo = FaixaRepository(session)
    f = frepo.upsert(
        viagem_id=viagem.id,
        bloco_id=b.id,
        faixa_index=0,
        arquivo="lote_0001_faixa_0.png",
        altura_px=5120,
    )
    assert frepo.get_by_arquivo(viagem.id, "lote_0001_faixa_0.png").id == f.id
    assert frepo.get_by_bloco_index(b.id, 0).id == f.id


# ── Detecções (escrita transacional por faixa) ──


def test_replace_for_faixa_substitui_sem_estado_parcial(session: Session) -> None:
    _, viagem, _, faixa = _fluxo_base(session)
    repo = DeteccaoRepository(session)

    repo.replace_for_faixa(
        faixa.id,
        [
            {"classe": "F3", "area_pixels": 10, "confidence": 0.9, "origem": "inferencia"},
            {"classe": "F3", "area_pixels": 20, "confidence": 0.8},
        ],
    )
    assert len(repo.list_by_faixa(faixa.id)) == 2

    repo.replace_for_faixa(faixa.id, [{"classe": "J", "area_pixels": 30, "confidence": 0.95}])
    ativos = repo.list_by_faixa(faixa.id)
    assert len(ativos) == 1
    assert ativos[0].classe == "J"

    total = session.scalar(
        select(func.count()).select_from(Deteccao).where(Deteccao.faixa_id == faixa.id)
    )
    assert total == 3
    assert repo.count_by_viagem_classe(viagem.id, "J") == 1
    assert repo.count_by_viagem_classe(viagem.id, "F3") == 0


# ── Parâmetros TRI/TRE ──


def test_parametro_km_upsert_preserva_campos(session: Session) -> None:
    _, viagem, _, _ = _fluxo_base(session)
    repo = ParametroKmRepository(session)

    repo.upsert(viagem_id=viagem.id, km=10, tri_serie=[1.0, 2.0], extras={"obs": "x"})
    repo.upsert(viagem_id=viagem.id, km=10, tre_serie=[3.0], extras={"novo": True})

    parametro = repo.get(viagem.id, 10)
    assert parametro.tri_serie == [1.0, 2.0]
    assert parametro.tre_serie == [3.0]
    assert parametro.extras == {"obs": "x", "novo": True}
    assert repo.count_by_viagem(viagem.id) == 1


# ── Jobs ──


def test_job_ciclo_de_vida(session: Session) -> None:
    _, viagem, _, _ = _fluxo_base(session)
    repo = JobRepository(session)

    job = repo.create(
        viagem_nome="Viagem 1",
        tipo="inferencia",
        rq_job_id="rq-1",
        viagem_id=viagem.id,
        meta={"a": 1},
    )
    assert job.status == "queued"

    repo.mark_started(job.id)
    repo.update_progress(job.id, {"atual": 1, "total": 2})
    repo.finish(job.id, resultado={"ok": True})

    salvo = repo.get_by_rq_id("rq-1")
    assert salvo.status == "finished"
    assert salvo.progresso == {"atual": 1, "total": 2}
    assert salvo.resultado == {"ok": True}
    assert salvo.finalizado_em is not None
    assert repo.list_recent(viagem_id=viagem.id, tipo="inferencia")[0].id == job.id


def test_job_falha(session: Session) -> None:
    repo = JobRepository(session)
    job = repo.create(viagem_nome="V", tipo="upload_zip")
    repo.fail(job.id, "boom")
    salvo = repo.get(job.id)
    assert salvo.status == "failed"
    assert salvo.erro == "boom"


# ── Resultados IGG/ICP ──


def test_resultado_analise_upsert(session: Session) -> None:
    _, viagem, _, _ = _fluxo_base(session)
    repo = ResultadoAnaliseRepository(session)

    repo.upsert(
        viagem_id=viagem.id,
        km=5,
        tipo_analise="igg",
        valor=12.34,
        norma="DNIT 006/2003-PRO",
        conceito="Bom",
        n_estacoes=50,
        detalhe={"grupos": [1]},
    )
    repo.upsert(
        viagem_id=viagem.id, km=5, tipo_analise="igg", valor=13.0, conceito="Regular"
    )

    resultado = repo.get(viagem.id, 5, "igg")
    assert float(resultado.valor) == 13.0
    assert resultado.conceito == "Regular"
    assert len(repo.list_by_viagem(viagem.id, "igg")) == 1


# ── Uploads ──


def test_arquivo_upload_upsert(session: Session) -> None:
    repo = ArquivoUploadRepository(session)
    repo.upsert(
        viagem_nome="V",
        nome="a.jpg",
        caminho_relativo="uploads/V/a.jpg",
        tamanho_bytes=123,
    )
    repo.upsert(
        viagem_nome="V",
        nome="a.jpg",
        caminho_relativo="uploads/V/a.jpg",
        md5="abc",
    )
    arquivos = repo.list_by_viagem_nome("V")
    assert len(arquivos) == 1
    assert arquivos[0].md5 == "abc"
    assert arquivos[0].tamanho_bytes == 123
