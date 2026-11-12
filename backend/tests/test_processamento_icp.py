"""Processamento do modo rígido (RDT1-21): faixas contínuas, placas e validação."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.api.routes_folders import _normalizar_juntas
from src.jobs import _processar_imagens, _processar_placas_icp
from src.services import image_processor as ip


def _settings(base: Path) -> SimpleNamespace:
    return SimpleNamespace(
        dados_dir=base / "dados",
        modelos_dir=base / "modelos",
        icp_nuclio_dir=base / "nao-existe",
        icp_modelo_pt=base / "nao-existe.pt",
        icp_limiar_juntas=0.8,
        icp_ordem_invertida=True,
        icp_mm_por_px_y=1.953125,
    )


def _escrever_metas(base: Path, n_blocos: int = 6, km_inicio: float = 0.0) -> None:
    for bloco in range(n_blocos):
        faixas = []
        for indice in range(4):
            inicio = km_inicio + bloco * 0.020 + indice * 0.005
            faixas.append(
                {
                    "bloco_indice": bloco,
                    "faixa_index": indice,
                    "arquivo": f"lote_{bloco:04d}_faixa_{indice}.png",
                    "caminho": str(base / f"lote_{bloco:04d}_faixa_{indice}.png"),
                    "km_inicio": inicio,
                    "km_fim": inicio + 0.005,
                    "altura_px": 5120,
                    "imagens_raw": [],
                    "offset_y_px": 0,
                    "percentual_ultima_img": 1.0,
                }
            )
        meta = {
            "bloco": {
                "indice": bloco,
                "lado": "left",
                "km_inicio": faixas[0]["km_inicio"],
                "km_fim": faixas[-1]["km_fim"],
            },
            "faixas": faixas,
        }
        (base / f"lote_{bloco:04d}_meta.json").write_text(
            json.dumps(meta), encoding="utf-8"
        )


# ── Placas/calibração no job ─────────────────────────────────────────────────
def test_processar_placas_icp_com_calibracao_manual(tmp_path: Path) -> None:
    base = tmp_path / "viagem"
    base.mkdir()
    _escrever_metas(base)

    resultado = _processar_placas_icp(
        base,
        _settings(tmp_path),
        {"periodo_m": 5.12, "fase_m": 0.0},
    )

    assert resultado["total_faixas"] == 24
    assert resultado["total_placas"] == 23
    assert resultado["total_amostras"] == 2
    assert resultado["calibracao"]["fonte"] == "manual"
    # Resumo estruturado também vai para o resultado do job (persistido no banco).
    assert resultado["inventario_resumo"]["total_amostras"] == 2
    assert resultado["inventario_resumo"]["trecho"]["n_faixas"] == 24
    assert (base / "icp_placas.json").is_file()
    assert (base / "icp_juntas.json").is_file()

    inventario = json.loads((base / "icp_placas.json").read_text(encoding="utf-8"))
    assert inventario["norma"] == "DNIT 062/2004-PRO"
    assert inventario["trecho"]["n_faixas"] == 24


def test_processar_placas_icp_sem_faixas_falha(tmp_path: Path) -> None:
    base = tmp_path / "viagem"
    base.mkdir()

    with pytest.raises(ValueError, match="Nenhuma faixa"):
        _processar_placas_icp(base, _settings(tmp_path), {"periodo_m": 5.12})


def test_job_icp_conclui_com_aviso_sem_calibracao(
    tmp_path: Path, monkeypatch
) -> None:
    """Sem modelo/detecção o job conclui com aviso (não falha nem omite o fato)."""
    import src.jobs as jobs

    persistidas: list[str] = []
    monkeypatch.setattr(jobs, "get_settings", lambda: _settings(tmp_path))
    monkeypatch.setattr(jobs, "processar_pasta", lambda *a, **kw: [])
    monkeypatch.setattr(
        jobs.viagem_store,
        "persistir_viagem",
        lambda **kw: persistidas.append(kw["viagem_nome"]),
    )
    monkeypatch.setattr(
        jobs,
        "_processar_placas_icp",
        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("Modelo de juntas indisponível")),
    )

    origem = tmp_path / "origem"
    origem.mkdir()
    resultado = _processar_imagens(
        pasta_origem=str(origem),
        viagem_nome="viagem_icp",
        km_inicial=0.0,
        tipo_analise="icp",
    )

    assert resultado["placas_geradas"] is False
    assert resultado["avisos"] == [
        "Placas do ICP não geradas: Modelo de juntas indisponível"
    ]
    assert persistidas == ["viagem_icp"]  # config/blocos/faixas gravados antes


def test_job_icp_exige_km_inicial() -> None:
    with pytest.raises(ValueError, match="km_inicial"):
        _processar_imagens(
            pasta_origem="/tmp",
            viagem_nome="teste",
            tipo_analise="icp",
        )


def test_job_rejeita_tipo_de_analise_invalido() -> None:
    with pytest.raises(ValueError, match="tipo_analise"):
        _processar_imagens(
            pasta_origem="/tmp",
            viagem_nome="teste",
            km_inicial=0.0,
            tipo_analise="outro",
        )


# ── image_processor: faixas contínuas no modo ICP ────────────────────────────
def test_todas_faixas_nao_pula_blocos_e_gera_as_quatro(tmp_path: Path, monkeypatch) -> None:
    origem = tmp_path / "origem"
    origem.mkdir()
    for i in range(40):  # 4 blocos de 10 imagens
        (origem / f"img_{i:04d}.bmp").write_bytes(b"")

    tarefas: list[tuple] = []

    def fake_lote(args):
        tarefas.append(args)
        return ip.LoteResult(
            lado="left",
            indice=args[0],
            faixas=[],
            imagens_no_lote=len(args[1]),
        )

    monkeypatch.setattr(ip, "_processar_lote", fake_lote)
    monkeypatch.setattr(ip, "process_map", lambda func, itens, **kw: [func(t) for t in itens])

    ip.processar_pasta(
        origem,
        tmp_path / "destino",
        km_inicial=0.0,
        tipo_pista="dupla",
        faixa=1,
        todas_faixas=True,
    )

    assert len(tarefas) == 4  # nenhum bloco pulado
    assert all(tarefa[8] == (0, 1, 2, 3) for tarefa in tarefas)


def test_modo_igg_mantem_amostragem_e_uma_faixa(tmp_path: Path, monkeypatch) -> None:
    origem = tmp_path / "origem"
    origem.mkdir()
    for i in range(40):
        (origem / f"img_{i:04d}.bmp").write_bytes(b"")

    tarefas: list[tuple] = []

    def fake_lote(args):
        tarefas.append(args)
        return ip.LoteResult(
            lado="left",
            indice=args[0],
            faixas=[],
            imagens_no_lote=len(args[1]),
        )

    monkeypatch.setattr(ip, "_processar_lote", fake_lote)
    monkeypatch.setattr(ip, "process_map", lambda func, itens, **kw: [func(t) for t in itens])

    ip.processar_pasta(
        origem,
        tmp_path / "destino",
        km_inicial=0.0,
        tipo_pista="dupla",
        faixa=1,
        todas_faixas=False,
    )

    assert [tarefa[0] for tarefa in tarefas] == [0, 2]  # blocos ímpares pulados
    assert all(tarefa[8] == (0,) for tarefa in tarefas)


# ── API: calibração opcional ─────────────────────────────────────────────────
def test_normalizar_juntas_aceita_campos_validos() -> None:
    saida = _normalizar_juntas(
        {"periodo_m": "5.12", "fase_m": 0.3, "juntas_km": [1.0, 6.12]}
    )

    assert saida == {"periodo_m": 5.12, "fase_m": 0.3, "juntas_km": [1.0, 6.12]}


def test_normalizar_juntas_rejeita_periodo_invalido() -> None:
    with pytest.raises(ValueError):
        _normalizar_juntas({"periodo_m": 0})
