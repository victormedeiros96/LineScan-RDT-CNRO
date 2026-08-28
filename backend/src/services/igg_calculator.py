from __future__ import annotations

import re
from collections import Counter, defaultdict
from pathlib import Path

MAP_DNIT: dict[str, tuple[str, str, float, int]] = {
    "Trincas":                    ("T1",  "Trincas Isoladas Tipo 1",       0.2, 1),
    "Trinca em Bloco":            ("FC2", "FC-2 (Trinca Bloco/Jacaré)",    0.5, 2),
    "Couro de Jacaré":            ("FC2", "FC-2 (Trinca Bloco/Jacaré)",    0.5, 2),
    "Couro de Jacaré c/ Erosão":  ("FC3", "FC-3 (Jacaré c/ Erosão)",      0.8, 3),
    "fc3":                        ("FC3", "FC-3 (Jacaré c/ Erosão)",      0.8, 3),
    "Ondulação":                  ("O5",  "Ond./Panela/Escorreg.",         1.0, 0),
    "Panela":                     ("O5",  "Ond./Panela/Escorreg.",         1.0, 0),
    "Escorregamento":             ("O5",  "Ond./Panela/Escorreg.",         1.0, 0),
    "Exsudação":                  ("EX",  "Exsudação",                     0.5, 0),
    "Desgaste":                   ("D",   "Desgaste",                      0.3, 0),
    "Remendo":                    ("R",   "Remendo",                       0.6, 0),
}

ALL_GROUPS: list[tuple[str, str, float]] = [
    ("T1",  "Trincas Isoladas Tipo 1",       0.2),
    ("FC2", "FC-2 (Trinca Bloco/Jacaré)",    0.5),
    ("FC3", "FC-3 (Jacaré c/ Erosão)",       0.8),
    ("O5",  "Ond./Panela/Escorreg.",         1.0),
    ("EX",  "Exsudação",                     0.5),
    ("D",   "Desgaste",                      0.3),
    ("R",   "Remendo",                       0.6),
]


def classificar_igg(igg: float) -> str:
    if igg <= 20:
        return "Ótimo"
    if igg <= 40:
        return "Bom"
    if igg <= 80:
        return "Regular"
    if igg <= 160:
        return "Ruim"
    return "Péssimo"


def calcular_igg_por_km(
    km: int,
    estacoes: list[list[dict]],
    parametros: dict[str, list],
) -> dict:
    n = len(estacoes)

    trinca_por_estacao: list[str | None] = [None] * n
    grupos_por_estacao: list[set[str]] = [set() for _ in range(n)]

    for idx, deteccoes in enumerate(estacoes):
        max_prio = 0
        for det in deteccoes:
            classe = det.get("classe", "")
            entry = MAP_DNIT.get(classe)
            if entry is None:
                continue
            codigo, nome, fp, prio = entry
            if prio > 0:
                if prio > max_prio:
                    max_prio = prio
                    trinca_por_estacao[idx] = codigo
            else:
                grupos_por_estacao[idx].add(codigo)
        if trinca_por_estacao[idx] is not None:
            grupos_por_estacao[idx].add(trinca_por_estacao[idx])

    fa_por_grupo: Counter[str] = Counter()
    for est_set in grupos_por_estacao:
        for codigo in est_set:
            fa_por_grupo[codigo] += 1

    grupos_resultado: list[dict] = []
    for codigo, nome, fp in ALL_GROUPS:
        fa = fa_por_grupo.get(codigo, 0)
        fr = (fa / n) * 100 if n > 0 else 0.0
        igi = round(fr * fp, 2)
        grupos_resultado.append({
            "codigo": codigo,
            "nome": nome,
            "fa": fa,
            "fr": round(fr, 2),
            "fp": fp,
            "igi": igi,
        })

    igi_flecha = 0.0
    igi_variancia = 0.0

    # Coleta valores de TRI e TRE (ou IRI como fallback)
    raw_tri: list[float] = []
    for key in ["TRI (mm)", "IRI (3E)", "IRI (3D)"]:
        raw = parametros.get(key)
        if raw and isinstance(raw, list):
            try:
                raw_tri = [float(v) if v else 0.0 for v in raw]
            except (ValueError, TypeError):
                raw_tri = []
            if raw_tri:
                break

    raw_tre: list[float] = []
    for key in ["TRE (mm)", "IRI (1E)", "IRI (1D)"]:
        raw = parametros.get(key)
        if raw and isinstance(raw, list):
            try:
                raw_tre = [float(v) if v else 0.0 for v in raw]
            except (ValueError, TypeError):
                raw_tre = []
            if raw_tre:
                break

    # Média e variância calculadas separadamente por trilha (TRI e TRE),
    # conforme item 7.2 e Anexo C da DNIT 006/2003-PRO.
    def _media_variancia(valores: list[float]) -> tuple[float | None, float]:
        if not valores:
            return None, 0.0
        n = len(valores)
        media = sum(valores) / n
        if n > 1:
            var = sum((v - media) ** 2 for v in valores) / (n - 1)  # n-1 conforme NOTA 3
        else:
            var = 0.0
        return media, var

    medias_flechas: list[float] = []
    variancias_flechas: list[float] = []
    for serie in (raw_tri, raw_tre):
        media, var = _media_variancia(serie)
        if media is not None:
            medias_flechas.append(media)
            variancias_flechas.append(var)

    if medias_flechas:
        # Item 9: média aritmética dos valores médios das flechas (TRI e TRE)
        F = sum(medias_flechas) / len(medias_flechas)
        igi_flecha = round(F * 4 / 3, 2) if F <= 30 else 40.0

        # Item 10: média aritmética das variâncias das flechas (ambas as trilhas)
        if variancias_flechas:
            var_media = sum(variancias_flechas) / len(variancias_flechas)
            igi_variancia = round(var_media, 2) if var_media <= 50 else 50.0

    igg_sem_flecha = sum(g["igi"] for g in grupos_resultado)
    igg_total = round(igg_sem_flecha + igi_flecha + igi_variancia, 2)

    return {
        "km": km,
        "igg": igg_total,
        "igg_sem_flecha": round(igg_sem_flecha, 2),
        "conceito": classificar_igg(igg_total),
        "n_estacoes": n,
        "grupos": grupos_resultado,
        "item_9_flecha": round(igi_flecha, 2),
        "item_10_variancia": round(igi_variancia, 2),
        "estacoes_com_trinca": sum(1 for e in trinca_por_estacao if e == "T1"),
        "estacoes_com_fc2": sum(1 for e in trinca_por_estacao if e == "FC2"),
        "estacoes_com_fc3": sum(1 for e in trinca_por_estacao if e == "FC3"),
    }


def montar_estacoes_por_km(
    analise: dict,
    viagem_nome: str,
    base: Path,
) -> dict[str, dict]:
    def _faixa_num(nome: str) -> int:
        digits = re.findall(r"\d+", nome)
        return int(digits[-1]) if digits else 0

    def _bloco_num(nome: str) -> int:
        match = re.search(r"lote_(\d+)", nome)
        return int(match.group(1)) if match else 0

    config_path = base / "viagem_config.json"
    config: dict = {}
    if config_path.exists():
        import json
        config = json.loads(config_path.read_text(encoding="utf-8"))

    faixas = sorted(analise.keys(), key=lambda n: (_bloco_num(n), _faixa_num(n)))

    km_inicial = config.get("km_inicial")
    km_por_bloco = 0.020
    km_por_faixa = 0.005
    sentido = config.get("sentido", "crescente")

    kms: dict[str, dict] = {}
    for nome in faixas:
        bloco = _bloco_num(nome)
        faixa = _faixa_num(nome)
        dados = analise[nome]
        deteccoes = dados.get("deteccoes", []) if isinstance(dados, dict) else dados

        if km_inicial is not None:
            if sentido == "decrescente":
                km_atual = float(km_inicial) - (bloco * km_por_bloco + faixa * km_por_faixa)
            else:
                km_atual = float(km_inicial) + (bloco * km_por_bloco + faixa * km_por_faixa)
            km_key = str(int(km_atual))

            if km_key not in kms:
                kms[km_key] = {"imagens": [], "estacoes": [[] for _ in range(50)]}

            metros_no_km = abs(km_atual - int(km_atual)) * 1000
            estacao_idx = min(49, int(metros_no_km / 20))

            kms[km_key]["imagens"].append(nome)
            kms[km_key]["estacoes"][estacao_idx].extend(deteccoes)

    return kms
