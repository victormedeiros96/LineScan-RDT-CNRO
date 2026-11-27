"""Imagens de display do ICP: thumbnail comprimido e tiles de alta resolução.

As faixas do ICP são PNGs de 4096 px (lateral) × 5120 px (5 m de KM), então
enviar a imagem integral ao navegador é caro. O viewer exibe a imagem rotacionada
90° (KM no eixo x, lateral no eixo y); para reduzir o custo:

* a visão geral usa um **thumbnail JPEG** (imagem completa, sem rotação — o
  ``<canvas>`` do frontend faz a rotação);
* o zoom carrega **tiles JPEG já rotacionados**, somente da viewport.

Só visualização: nada aqui altera detecção, cálculo ou persistência.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import cv2
import numpy as np

BLOCO_20M_RE = re.compile(r"^lote_(\d+)_20m\.png$")
LATERAL_PX = 4096


def _ler(caminho: Path) -> np.ndarray | None:
    if not caminho.is_file():
        return None
    return cv2.imread(str(caminho))


def _montar_bloco(base: Path, indice: int) -> np.ndarray | None:
    """Empilha as faixas de 5 m do bloco (faixa 0 = topo/menor KM)."""
    faixas = sorted(
        base.glob(f"lote_{indice:04d}_faixa_*.png"),
        key=lambda p: int(p.stem.rsplit("_", 1)[-1]),
    )
    imagens = [img for img in (_ler(p) for p in faixas) if img is not None]
    if not imagens:
        return None
    return np.vstack(imagens)


def _recortar_intervalo(
    base: Path, faixas: list[dict], km_inicio: float, km_fim: float
) -> np.ndarray | None:
    """Recorta e empilha as faixas cobrindo ``[km_inicio, km_fim]`` (KM nas linhas)."""
    trechos = []
    for faixa in sorted(faixas, key=lambda f: float(f["km_inicio"])):
        f_ini = float(faixa["km_inicio"])
        f_fim = float(faixa["km_fim"])
        ini = max(km_inicio, f_ini)
        fim = min(km_fim, f_fim)
        if fim <= ini:
            continue
        imagem = _ler(base / str(faixa["arquivo"]))
        if imagem is None:
            continue
        altura = imagem.shape[0]
        span = f_fim - f_ini
        if span <= 0 or altura <= 0:
            continue
        y0 = max(0, min(altura, int(round((ini - f_ini) / span * altura))))
        y1 = max(0, min(altura, int(round((fim - f_ini) / span * altura))))
        if y1 <= y0:
            continue
        trechos.append(imagem[y0:y1])
    if not trechos:
        return None
    return np.vstack(trechos)


def imagem_fonte(
    base: Path,
    faixas: list[dict],
    arquivo: str | None = None,
    km_inicio: float | None = None,
    km_fim: float | None = None,
) -> np.ndarray | None:
    """Imagem-fonte (linhas = KM, colunas = lateral) de um item do viewer."""
    if km_inicio is not None and km_fim is not None:
        return _recortar_intervalo(base, faixas, km_inicio, km_fim)
    if not arquivo:
        return None
    casamento = BLOCO_20M_RE.match(arquivo)
    if casamento:
        return _montar_bloco(base, int(casamento.group(1)))
    return _ler(base / arquivo)


def _codificar_jpeg(imagem: np.ndarray, qualidade: int) -> bytes:
    ok, buffer = cv2.imencode(
        ".jpg", imagem, [cv2.IMWRITE_JPEG_QUALITY, int(qualidade)]
    )
    if not ok:
        raise ValueError("Falha ao codificar o thumbnail JPEG")
    return buffer.tobytes()


def _escala_thumbnail(altura_total: int, largura: int, largura_max: int) -> float:
    """Fator de redução para caber o maior lado em ``largura_max`` (nunca amplia)."""
    maior = max(int(altura_total), int(largura))
    if maior <= 0 or largura_max <= 0 or maior <= largura_max:
        return 1.0
    return largura_max / maior


def thumbnail_jpeg(
    imagem: np.ndarray, largura_max: int = 1024, qualidade: int = 60
) -> bytes:
    """JPEG comprimido da imagem inteira (sem rotação), limitado por ``largura_max``.

    O limite vale para o maior lado, então tanto a lateral quanto o KM cabem.
    """
    altura, largura = imagem.shape[:2]
    escala = _escala_thumbnail(altura, largura, largura_max)
    if escala < 1.0:
        novo = (max(1, int(round(largura * escala))), max(1, int(round(altura * escala))))
        imagem = cv2.resize(imagem, novo, interpolation=cv2.INTER_AREA)
    return _codificar_jpeg(imagem, qualidade)


def thumbnail_intervalo_jpeg(
    base: Path,
    faixas: list[dict],
    km_inicio: float,
    km_fim: float,
    largura_max: int = 1024,
    qualidade: int = 60,
) -> bytes | None:
    """Thumbnail de ``[km_inicio, km_fim]`` reduzindo cada trecho ao ler.

    Diferente de :func:`imagem_fonte`, não empilha as faixas em resolução
    integral: cada recorte é reduzido imediatamente ao fator final e só então
    empilhado, mantendo o pico de memória em ~uma faixa (em vez de centenas de
    MB para intervalos com N placas). A escala vem de 1024 px/m (display), então
    a proporção em relação ao KM é preservada.
    """
    recortes: list[tuple[np.ndarray, float]] = []
    for faixa in sorted(faixas, key=lambda f: float(f.get("km_inicio") or 0.0)):
        try:
            f_ini = float(faixa["km_inicio"])
            f_fim = float(faixa["km_fim"])
        except (KeyError, TypeError, ValueError):
            continue
        ini = max(km_inicio, f_ini)
        fim = min(km_fim, f_fim)
        if fim <= ini:
            continue
        imagem = _ler(base / str(faixa["arquivo"]))
        if imagem is None:
            continue
        altura = imagem.shape[0]
        span = f_fim - f_ini
        if span <= 0 or altura <= 0:
            continue
        y0 = max(0, min(altura, int(round((ini - f_ini) / span * altura))))
        y1 = max(0, min(altura, int(round((fim - f_ini) / span * altura))))
        if y1 <= y0:
            continue
        recortes.append((imagem[y0:y1, :], fim - ini))
    if not recortes:
        return None

    # Altura de display do intervalo (1024 px/m) → fator único para todos os trechos.
    total_km = sum(span for _, span in recortes)
    total_h = max(1, int(round(total_km * 1024 * 1000)))
    largura_real = recortes[0][0].shape[1]
    escala = _escala_thumbnail(total_h, largura_real, largura_max)
    alvo_w = max(1, int(round(largura_real * escala)))

    trechos = [
        cv2.resize(
            recorte,
            (alvo_w, max(1, int(round(span * 1024 * 1000 * escala)))),
            interpolation=cv2.INTER_AREA,
        )
        for recorte, span in recortes
    ]
    return _codificar_jpeg(np.vstack(trechos), qualidade)


def thumbnail_item_jpeg(
    base: Path,
    faixas: list[dict],
    arquivo: str | None = None,
    km_inicio: float | None = None,
    km_fim: float | None = None,
    largura_max: int = 1024,
    qualidade: int = 60,
) -> bytes | None:
    """Thumbnail do item do viewer (intervalo, bloco de 20 m ou faixa de 5 m)."""
    if km_inicio is not None and km_fim is not None:
        return thumbnail_intervalo_jpeg(
            base, faixas, km_inicio, km_fim, largura_max, qualidade
        )
    imagem = imagem_fonte(base, faixas, arquivo, km_inicio, km_fim)
    if imagem is None:
        return None
    return thumbnail_jpeg(imagem, largura_max, qualidade)


def tile_display(
    imagem: np.ndarray, x: int, y: int, tamanho: int
) -> np.ndarray | None:
    """Recorta um tile em coordenadas de display (x = KM, y = lateral) e rotaciona.

    A imagem-fonte tem linhas = KM e colunas = lateral. O display é a fonte
    rotacionada 90°: ``display_y = lateral`` e ``display_x = KM`` (KM cresce
    para a esquerda). O tile devolvido já está na orientação do display.
    """
    altura_km, largura_lateral = imagem.shape[:2]
    x0 = max(0, int(x))
    x1 = min(altura_km, int(x) + int(tamanho))
    y0 = max(0, int(y))
    y1 = min(largura_lateral, int(y) + int(tamanho))
    if x1 <= x0 or y1 <= y0:
        return None
    # x (KM no display) = altura_km - v, com v = linha da fonte.
    vlow = altura_km - x1
    vhigh = altura_km - x0
    recorte = imagem[vlow:vhigh, y0:y1]
    # T[a][b] = recorte[n-1-b][a]  (flip vertical + transposta)
    return recorte[::-1].transpose(1, 0, 2)


def tile_jpeg(
    imagem: np.ndarray, x: int, y: int, tamanho: int, qualidade: int = 85
) -> bytes | None:
    """Versão JPEG de :func:`tile_display`."""
    tile = tile_display(imagem, x, y, tamanho)
    if tile is None:
        return None
    ok, buffer = cv2.imencode(
        ".jpg", tile, [cv2.IMWRITE_JPEG_QUALITY, int(qualidade)]
    )
    if not ok:
        return None
    return buffer.tobytes()


def faixas_relevantes(
    faixas: list[dict],
    arquivo: str | None = None,
    km_inicio: float | None = None,
    km_fim: float | None = None,
) -> list[dict]:
    """Faixas que compõem o item, para compor a chave de cache."""
    if km_inicio is not None and km_fim is not None:
        return [
            f
            for f in faixas
            if f.get("km_inicio") is not None
            and f.get("km_fim") is not None
            and float(f["km_fim"]) > km_inicio
            and float(f["km_inicio"]) < km_fim
        ]
    if not arquivo:
        return []
    casamento = BLOCO_20M_RE.match(arquivo)
    if casamento:
        prefixo = f"lote_{int(casamento.group(1)):04d}_faixa_"
        return [f for f in faixas if str(f.get("arquivo", "")).startswith(prefixo)]
    return [f for f in faixas if f.get("arquivo") == arquivo]


def chave_cache(
    base: Path,
    faixas: list[dict],
    params: dict,
    arquivo: str | None = None,
    km_inicio: float | None = None,
    km_fim: float | None = None,
) -> str:
    """Chave estável a partir dos parâmetros e do estado dos arquivos-fonte."""
    partes = [repr(sorted(params.items())), repr(arquivo), repr(km_inicio), repr(km_fim)]
    for faixa in sorted(faixas_relevantes(faixas, arquivo, km_inicio, km_fim), key=lambda f: str(f.get("arquivo"))):
        caminho = base / str(faixa.get("arquivo"))
        try:
            stat = caminho.stat()
            partes.append(f"{caminho.name}:{stat.st_mtime_ns}:{stat.st_size}")
        except OSError:
            partes.append(f"{caminho.name}:ausente")
    return hashlib.md5("|".join(partes).encode("utf-8")).hexdigest()
