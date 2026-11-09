from __future__ import annotations

from src.services.igg_calculator import calcular_igg_por_km

# Fatores da Tabela 1 da DNIT 006/2003-PRO (item 7.3).
FATORES_NORMATIVOS = {
    "T1": 0.2,
    "FC2": 0.5,
    "FC3": 0.8,
    "T4": 0.9,
    "O5": 1.0,
    "EX": 0.5,
    "D": 0.3,
    "R": 0.6,
}


def _estacoes(deteccoes_por_estacao: dict[int, list[str]], n: int = 50) -> list[list[dict]]:
    estacoes: list[list[dict]] = [[] for _ in range(n)]
    for idx, classes in deteccoes_por_estacao.items():
        estacoes[idx] = [{"classe": classe} for classe in classes]
    return estacoes


def _grupo(resultado: dict, codigo: str) -> dict:
    return next(g for g in resultado["grupos"] if g["codigo"] == codigo)


def test_saida_tem_os_oito_grupos_normativos_com_fatores_da_tabela_1() -> None:
    resultado = calcular_igg_por_km(120, _estacoes({}), {})

    assert [g["codigo"] for g in resultado["grupos"]] == list(FATORES_NORMATIVOS)
    for grupo in resultado["grupos"]:
        assert grupo["fp"] == FATORES_NORMATIVOS[grupo["codigo"]]


def test_caso_conferido_a_mao_uma_estacao_de_cada_tipo() -> None:
    # 50 estações (n = 50). Estações 0-7 têm uma ocorrência de cada tipo; 8-49 vazias.
    estacoes = _estacoes({
        0: ["Trincas"],                       # T1
        1: ["Couro de Jacaré"],               # FC2
        2: ["Couro de Jacaré c/ Erosão"],     # FC3
        3: ["Afundamento"],                   # T4
        4: ["Ondulação"],                     # O5
        5: ["Exsudação"],                     # EX
        6: ["Desgaste"],                      # D
        7: ["Remendo"],                       # R
    })

    resultado = calcular_igg_por_km(120, estacoes, {})

    # FA = 1 por grupo, FR = 1/50*100 = 2,0; IGI = FR * fp.
    esperado_igi = {
        "T1": 0.4,
        "FC2": 1.0,
        "FC3": 1.6,
        "T4": 1.8,
        "O5": 2.0,
        "EX": 1.0,
        "D": 0.6,
        "R": 1.2,
    }
    for codigo, igi in esperado_igi.items():
        grupo = _grupo(resultado, codigo)
        assert grupo["fa"] == 1
        assert grupo["fr"] == 2.0
        assert grupo["igi"] == igi

    assert resultado["n_estacoes"] == 50
    assert resultado["igg_sem_flecha"] == 9.6
    assert resultado["igg"] == 9.6
    assert resultado["conceito"] == "Ótimo"


def test_caso_conferido_a_mao_com_flechas() -> None:
    estacoes = _estacoes({
        0: ["Trincas"],
        1: ["Couro de Jacaré"],
        2: ["Couro de Jacaré c/ Erosão"],
        3: ["Afundamento"],
        4: ["Ondulação"],
        5: ["Exsudação"],
        6: ["Desgaste"],
        7: ["Remendo"],
    })
    parametros = {
        "TRI (mm)": [10.0] * 50,  # média 10, variância 0
        "TRE (mm)": [20.0] * 50,  # média 20, variância 0
    }

    resultado = calcular_igg_por_km(120, estacoes, parametros)

    # Item 9: F = (10 + 20)/2 = 15 <= 30 -> 15 * 4/3 = 20,0.
    assert resultado["item_9_flecha"] == 20.0
    # Item 10: média das variâncias = 0.
    assert resultado["item_10_variancia"] == 0.0
    # IGG = 9,6 + 20,0 + 0 = 29,6 -> Bom (20 < IGG <= 40).
    assert resultado["igg"] == 29.6
    assert resultado["conceito"] == "Bom"


def test_fc3_bruto_do_modelo_entra_como_tipo_1() -> None:
    # JSON legado com o nome bruto "fc3" (trincas isoladas do modelo, não a FC-3 normativa).
    resultado = calcular_igg_por_km(1, _estacoes({0: ["fc3"]}), {})

    assert _grupo(resultado, "T1")["fa"] == 1
    assert _grupo(resultado, "FC3")["fa"] == 0
    assert resultado["estacoes_com_trinca"] == 1
    assert resultado["estacoes_com_fc3"] == 0


def test_tbe_e_trinca_em_bloco_com_erosao_entram_em_fc3() -> None:
    resultado = calcular_igg_por_km(1, _estacoes({0: ["TBE"], 1: ["Trinca em Bloco c/ Erosão"]}), {})

    assert _grupo(resultado, "FC3")["fa"] == 2
    assert _grupo(resultado, "FC2")["fa"] == 0
    assert resultado["estacoes_com_fc3"] == 2


def test_prioridade_na_mesma_estacao_prevalece_o_tipo_mais_grave() -> None:
    # NOTA da Tabela 1: tipos 1, 2 e 3 na mesma estação -> só o tipo 3;
    # tipos 1 e 2 -> só o tipo 2.
    resultado = calcular_igg_por_km(
        1,
        _estacoes({
            0: ["Trincas", "Couro de Jacaré c/ Erosão"],  # T1 descartado -> FC3
            1: ["Trincas", "Couro de Jacaré"],            # T1 descartado -> FC2
            2: ["Trincas"],                               # T1
        }),
        {},
    )

    assert _grupo(resultado, "T1")["fa"] == 1
    assert _grupo(resultado, "FC2")["fa"] == 1
    assert _grupo(resultado, "FC3")["fa"] == 1


def test_afundamentos_alp_atp_alc_atc_entram_em_t4() -> None:
    resultado = calcular_igg_por_km(
        1,
        _estacoes({0: ["ALP"], 1: ["ATP"], 2: ["ALC"], 3: ["ATC"]}),
        {},
    )

    assert _grupo(resultado, "T4")["fa"] == 4
    assert _grupo(resultado, "T4")["igi"] == 7.2  # FR = 4/50*100 = 8,0; 8,0 * 0,9
