"""Testes do slice de persistência de viagem/config/blocos/faixas (RDT1-11).

Cobrem escrita transacional idempotente, reconciliação, leitura pela API interna
(as mesmas funções usadas pelas rotas) e escrita concorrente da mesma viagem.
"""

from __future__ import annotations

import threading
import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from src.db.models import Bloco, Faixa, Viagem
from src.services import viagem_store


def _config(nome: str) -> dict[str, Any]:
    return {
        "nome": nome,
        "km_inicial": 100.0,
        "km_final": 100.4,
        "tipo_pista": "dupla",
        "sentido": "crescente",
        "faixa": 1,
    }


def _lotes() -> list[dict[str, Any]]:
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
                    "imagens_raw": ["img_0001.png", "img_0002.png"],
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
                    "imagens_raw": ["img_0003.png"],
                    "offset_y_px": 0,
                    "percentual_ultima_img": 0.5,
                }
            ],
        },
    ]


def test_grava_e_le_config_blocos_faixas(session: Session) -> None:
    nome = f"Viagem {uuid.uuid4().hex[:8]}"

    viagem_store.persistir_viagem_em_sessao(
        session,
        projeto_nome="Projeto teste",
        viagem_nome=nome,
        pasta=nome,
        config=_config(nome),
        lotes=_lotes(),
    )

    config = viagem_store.ler_config_viagem(nome, session=session)
    assert config is not None
    assert config["nome"] == nome
    assert config["km_inicial"] == 100.0
    assert config["tipo_pista"] == "dupla"
    assert config["sentido"] == "crescente"
    assert config["faixa"] == 1

    faixas = viagem_store.listar_faixas_viagem(nome, session=session)
    assert faixas is not None
    assert faixas["total_faixas"] == 2
    assert [f["arquivo"] for f in faixas["faixas"]] == [
        "lote_0000_faixa_0.png",
        "lote_0001_faixa_0.png",
    ]
    assert faixas["faixas"][0]["_bloco"]["indice"] == 0
    assert faixas["faixas"][0]["km_inicio"] == 100.0

    meta = viagem_store.obter_meta_bloco(nome, 1, session=session)
    assert meta is not None
    assert meta["bloco"]["indice"] == 1
    assert [f["arquivo"] for f in meta["faixas"]] == ["lote_0001_faixa_0.png"]


def test_reprocessar_e_idempotente_e_reconcilia(session: Session) -> None:
    nome = f"Viagem {uuid.uuid4().hex[:8]}"

    def contar() -> tuple[int, int]:
        viagem = _viagem_da_pasta(session, nome)
        blocos = session.scalar(
            select(func.count())
            .select_from(Bloco)
            .where(Bloco.viagem_id == viagem.id, Bloco.deleted_at.is_(None))
        )
        faixas = session.scalar(
            select(func.count())
            .select_from(Faixa)
            .where(Faixa.viagem_id == viagem.id, Faixa.deleted_at.is_(None))
        )
        return blocos or 0, faixas or 0

    for _ in range(2):
        viagem_store.persistir_viagem_em_sessao(
            session,
            projeto_nome="Projeto teste",
            viagem_nome=nome,
            pasta=nome,
            config=_config(nome),
            lotes=_lotes(),
        )
    assert contar() == (2, 2)

    # Reprocessa só com o primeiro bloco: o bloco/faixa antigos saem de cena.
    viagem_store.persistir_viagem_em_sessao(
        session,
        projeto_nome="Projeto teste",
        viagem_nome=nome,
        pasta=nome,
        config=_config(nome),
        lotes=_lotes()[:1],
    )
    assert contar() == (1, 1)
    faixas = viagem_store.listar_faixas_viagem(nome, session=session)
    assert faixas is not None
    assert faixas["total_faixas"] == 1


def _viagem_da_pasta(session: Session, nome: str) -> Viagem:
    viagem = session.scalars(
        select(Viagem).where(Viagem.pasta == nome, Viagem.deleted_at.is_(None))
    ).first()
    assert viagem is not None
    return viagem


def test_escrita_concorrente_da_mesma_viagem(engine: Engine) -> None:
    nome = f"Viagem {uuid.uuid4().hex[:8]}"
    erros: list[BaseException] = []
    barreira = threading.Barrier(2)

    def escrever() -> None:
        try:
            with Session(engine) as sessao:
                barreira.wait(timeout=10)
                viagem_store.persistir_viagem_em_sessao(
                    sessao,
                    projeto_nome="Projeto concorrente",
                    viagem_nome=nome,
                    pasta=nome,
                    config=_config(nome),
                    lotes=_lotes(),
                )
                sessao.commit()
        except BaseException as exc:  # noqa: BLE001 - reportado no assert
            erros.append(exc)

    threads = [threading.Thread(target=escrever) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)

    assert not erros, erros

    with Session(engine) as sessao:
        viagens = list(
            sessao.scalars(
                select(Viagem).where(Viagem.pasta == nome, Viagem.deleted_at.is_(None))
            )
        )
        assert len(viagens) == 1
        viagem_id = viagens[0].id
        blocos = sessao.scalar(
            select(func.count())
            .select_from(Bloco)
            .where(Bloco.viagem_id == viagem_id, Bloco.deleted_at.is_(None))
        )
        faixas = sessao.scalar(
            select(func.count())
            .select_from(Faixa)
            .where(Faixa.viagem_id == viagem_id, Faixa.deleted_at.is_(None))
        )
        assert blocos == 2
        assert faixas == 2

        # A leitura concorrente (após o commit) vê o mesmo conjunto.
        lidas = viagem_store.listar_faixas_viagem(nome, session=sessao)
        assert lidas is not None
        assert lidas["total_faixas"] == 2
