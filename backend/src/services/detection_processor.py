from __future__ import annotations

from src.models.detection import AnalysisResult, ImageDetection, PathologyDetection


def processar_deteccoes(resultado: AnalysisResult) -> dict[int, list[dict]]:
    agrupado: dict[int, list[dict]] = {}
    for img in resultado.imagens:
        km_int = int(img.km)
        if km_int not in agrupado:
            agrupado[km_int] = []
        for det in img.deteccoes:
            agrupado[km_int].append({
                "arquivo_imagem": img.arquivo_imagem,
                "km": img.km,
                "quadrante": img.quadrante,
                "faixa": img.faixa,
                "classe": det.classe,
                "area_m2": det.area_m2,
                "linha": det.linha,
                "coluna": det.coluna,
                "confidence": det.confidence,
            })
    return agrupado
