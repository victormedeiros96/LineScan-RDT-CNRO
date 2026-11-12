"""Detecção e calibração das juntas transversais do pavimento rígido (ICP).

A calibração pode vir de três fontes, nesta ordem:

1. ``juntas_km`` informadas (lista de KM absolutos das juntas);
2. ``periodo_m``/``fase_m`` informados manualmente;
3. **detecção automática** com o pacote ``juntas-detector`` v0.2.0 e o modelo
   externo ``trinca-detector`` v2.1.0 (GPL-3.0), via ``ProvedorTrincaDetector``.

O detector é usado apenas para calibrar a rede periódica (período + fase). A
grade de placas em si é materializada por ``icp_placas`` a partir dessa
calibração — não se usa a posição individual de cada junta detectada.

Norma: DNIT 062/2004-PRO (avaliação objetiva de pavimento rígido).
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from src.services import icp_placas

logger = logging.getLogger(__name__)

NORMA_ICP = "DNIT 062/2004-PRO"
LIMIAR_PADRAO = 0.8
ORDEM_INVERTIDA_PADRAO = True
# Frame bruto do LineScan: 4096 x 1024 px = 4 m x 2 m.
MM_POR_PX_Y_PADRAO = 2000.0 / 1024.0
# A calibração do juntas-detector é feita em ~1 km de imagens (500 frames de 2 m).
MAX_FRAMES_CALIBRACAO = 500
# Junta detectada em janelas vizinhas do mesmo frame pode duplicar; agrupa.
TOLERANCIA_JUNTA_M = 0.15


@dataclass(frozen=True)
class ConfigJuntas:
    """Configuração do detector externo (``trinca-detector``)."""

    nuclio_dir: Path | None = None
    modelo_pt: Path | None = None
    limiar: float = LIMIAR_PADRAO
    ordem_invertida: bool = ORDEM_INVERTIDA_PADRAO
    mm_por_px_y: float = MM_POR_PX_Y_PADRAO

    @property
    def disponivel(self) -> bool:
        return bool(self.nuclio_dir and self.modelo_pt
                    and Path(self.nuclio_dir).is_dir()
                    and Path(self.modelo_pt).is_file())


def carregar_config(
    modelos_dir: Path,
    *,
    nuclio_dir: Path | None = None,
    modelo_pt: Path | None = None,
    limiar: float | None = None,
    ordem_invertida: bool | None = None,
    mm_por_px_y: float | None = None,
) -> ConfigJuntas:
    """Monta a configuração a partir de ``modelos/icp/config.json`` + overrides.

    O bloco ``"juntas"`` do config é opcional; os argumentos (normalmente vindos
    de ``Settings``/variáveis de ambiente) são o fallback.
    """
    cfg_path = Path(modelos_dir) / "icp" / "config.json"
    raw: dict[str, Any] = {}
    if cfg_path.is_file():
        try:
            raw = json.loads(cfg_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            logger.warning("Config do ICP inválida em %s: %s", cfg_path, exc)
    bloco = raw.get("juntas") if isinstance(raw, dict) else None
    bloco = bloco if isinstance(bloco, dict) else {}

    def _path(valor: Any, fallback: Path | None) -> Path | None:
        if valor:
            return Path(str(valor))
        return Path(fallback) if fallback else None

    return ConfigJuntas(
        nuclio_dir=_path(bloco.get("nuclio_dir"), nuclio_dir),
        modelo_pt=_path(bloco.get("modelo_pt") or bloco.get("modelo"), modelo_pt),
        limiar=float(bloco.get("limiar", limiar if limiar is not None else LIMIAR_PADRAO)),
        ordem_invertida=bool(
            bloco.get("ordem_invertida", ordem_invertida if ordem_invertida is not None else ORDEM_INVERTIDA_PADRAO)
        ),
        mm_por_px_y=float(
            bloco.get("mm_por_px_y", mm_por_px_y if mm_por_px_y is not None else MM_POR_PX_Y_PADRAO)
        ),
    )


def calibrar_manual(
    *,
    juntas_km: Iterable[float] | None = None,
    periodo_m: float | None = None,
    fase_m: float | None = None,
    km0: float | None = None,
    mm_por_px_y: float = MM_POR_PX_Y_PADRAO,
) -> dict[str, Any]:
    """Calibração explícita, sem modelo (útil para testes e como fallback)."""
    if juntas_km:
        cal = icp_placas.calibrar_por_juntas(
            juntas_km, km0=km0, mm_por_px_y=mm_por_px_y
        )
        cal["fonte"] = "juntas_km"
        cal["norma"] = NORMA_ICP
        return cal
    if periodo_m is None or float(periodo_m) <= 0:
        raise ValueError("Informe 'periodo_m' positivo ou uma lista 'juntas_km'.")
    return {
        "periodo_m": round(float(periodo_m), 6),
        "fase_m": round(float(fase_m or 0.0), 6),
        "R": None,
        "fonte": "manual",
        "mm_por_px_x": icp_placas.MM_POR_PX,
        "mm_por_px_y": mm_por_px_y,
        "norma": NORMA_ICP,
    }


class _ProvedorFaixas:
    """Fornece janelas de 2 m (1024 px) das faixas, em ordem crescente de KM."""

    def __init__(self, base: Path, faixas: list[icp_placas.FaixaInfo]):
        self.base = Path(base)
        self.faixas = sorted(faixas, key=lambda f: (f.km_inicio, f.faixa_index))
        self.janelas: list[tuple[icp_placas.FaixaInfo, int]] = []
        for faixa in self.faixas:
            for y0 in range(0, int(faixa.altura_px), 1024):
                self.janelas.append((faixa, y0))

    def __len__(self) -> int:
        return len(self.janelas)

    def frame(self, indice: int):
        import cv2

        faixa, y0 = self.janelas[indice]
        caminho = Path(faixa.arquivo)
        if not caminho.is_absolute():
            caminho = self.base / caminho
        imagem = cv2.imread(str(caminho), cv2.IMREAD_GRAYSCALE)
        if imagem is None:
            raise FileNotFoundError(f"Faixa não encontrada: {caminho}")
        return imagem[y0:y0 + 1024, :]

    def km_da_janela(self, indice: int, y_na_janela: float) -> float:
        faixa, y0 = self.janelas[indice]
        metros_por_px = 1.0 / icp_placas.PX_POR_METRO
        return float(faixa.km_inicio) + (y0 + y_na_janela) * metros_por_px / 1000.0


def detectar_juntas_km(
    base: Path,
    faixas: list[icp_placas.FaixaInfo],
    config: ConfigJuntas,
    *,
    max_frames: int = MAX_FRAMES_CALIBRACAO,
) -> list[float]:
    """Detecta as juntas transversais e devolve os KM absolutos (ordenados)."""
    if not config.disponivel:
        raise RuntimeError(
            "Modelo de juntas indisponível. Configure 'juntas' em "
            "modelos/icp/config.json (nuclio_dir e modelo_pt) ou defina "
            "ICP_NUCLIO_DIR/ICP_MODELO_PT."
        )

    import numpy as np
    from juntas_detector import ProvedorTrincaDetector

    provedor = _ProvedorFaixas(base, faixas)
    modelo = ProvedorTrincaDetector(
        config.nuclio_dir, config.modelo_pt, limiar=config.limiar
    )

    juntas: list[float] = []
    for indice in range(min(len(provedor), max_frames)):
        imagem = provedor.frame(indice)
        if imagem is None or imagem.size == 0:
            continue
        formas = modelo.detectar(imagem)
        pontos = ProvedorTrincaDetector.para_pontos(formas, "junta")
        if not pontos:
            continue
        # Em 2 m cabe no máximo uma junta transversal: usa o ponto médio da
        # polilinha de maior extensão vertical.
        polilinha = max(pontos, key=lambda pts: float(np.ptp(pts[:, 1])) if len(pts) else 0.0)
        y_medio = float(np.mean(polilinha[:, 1]))
        juntas.append(provedor.km_da_janela(indice, y_medio))

    juntas.sort()
    if len(juntas) >= 2:
        agrupadas = [juntas[0]]
        for km in juntas[1:]:
            if abs(km - agrupadas[-1]) >= TOLERANCIA_JUNTA_M:
                agrupadas.append(km)
        juntas = agrupadas
    return juntas


def calibrar_viagem(
    base: Path,
    faixas: list[icp_placas.FaixaInfo],
    *,
    config: dict[str, Any] | None = None,
    detector: ConfigJuntas | None = None,
) -> dict[str, Any]:
    """Calibra período/fase do trecho (manual ou por detecção automática)."""
    if not faixas:
        raise ValueError("Nenhuma faixa disponível para calibrar as juntas.")

    config = dict(config or {})
    km0 = min(float(f.km_inicio) for f in faixas)
    detector = detector or ConfigJuntas()

    if config.get("juntas_km"):
        return calibrar_manual(
            juntas_km=config["juntas_km"],
            km0=km0,
            mm_por_px_y=detector.mm_por_px_y,
        )
    if config.get("periodo_m") is not None:
        return calibrar_manual(
            periodo_m=float(config["periodo_m"]),
            fase_m=config.get("fase_m"),
            mm_por_px_y=detector.mm_por_px_y,
        )

    juntas_km = detectar_juntas_km(base, faixas, detector)
    if len(juntas_km) < 3:
        raise RuntimeError(
            f"Detecção de juntas insuficiente ({len(juntas_km)} encontradas); "
            "confira o modelo e a escala das imagens."
        )
    cal = calibrar_manual(
        juntas_km=juntas_km,
        km0=km0,
        mm_por_px_y=detector.mm_por_px_y,
    )
    cal["fonte"] = "detector"
    cal["n_juntas"] = len(juntas_km)
    return cal


def salvar_calibracao(base: Path, calibracao: dict[str, Any]) -> Path:
    destino = Path(base) / "icp_juntas.json"
    destino.write_text(
        json.dumps(calibracao, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return destino


def carregar_calibracao(base: Path) -> dict[str, Any] | None:
    caminho = Path(base) / "icp_juntas.json"
    if not caminho.is_file():
        return None
    return json.loads(caminho.read_text(encoding="utf-8"))
