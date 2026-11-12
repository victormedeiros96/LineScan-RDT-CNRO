from __future__ import annotations

import json
import re
from tqdm.contrib.concurrent import process_map
from dataclasses import dataclass, asdict
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from src.utils.tipo_pista import normalizar_tipo_pista

# ── Constantes hardcoded do linescan ──
LARGURA_PX = 4096       # 4 metros
ALTURA_PX = 1024        # 2 metros (altura de cada imagem raw)
LARGURA_METROS = 4
ALTURA_METROS = 2
METROS_POR_CONCAT = 20  # cada lote bruto representa 20m
IMAGENS_POR_LOTE = METROS_POR_CONCAT // ALTURA_METROS  # = 10
ALTURA_CONCAT_PX = ALTURA_PX * IMAGENS_POR_LOTE        # = 10240

# ── Constantes das faixas de 5m ──
FAIXA_METROS = 5
FAIXA_ALTURA_PX = FAIXA_METROS * (ALTURA_PX // ALTURA_METROS)  # = 2560 (inferência)
FAIXA_ALTURA_DISPLAY = FAIXA_METROS * (LARGURA_PX // LARGURA_METROS)  # = 5120 (display, 1024 px/m)
FAIXAS_POR_LOTE = METROS_POR_CONCAT // FAIXA_METROS             # = 4

EXTENSOES_VALIDAS = {".png", ".jpg", ".jpeg", ".bmp"}

LADO_PADRAO = "left"

# ── Modos de amostragem do IGG (DNIT 006/2003-PRO, item 6.1 + regra operacional) ──
# Modo de 50: uma estação efetiva de 20 m (pista simples e pista dupla faixa 2).
# Modo de 25: uma estação efetiva de 40 m — amostra os 5 m iniciais de um bloco,
# pula o bloco seguinte e assume o mesmo resultado (pista dupla faixa 1).
ESTACOES_POR_KM_MODO_50 = 50
ESTACOES_POR_KM_MODO_25 = 25
PASSO_ESTACAO_METROS_MODO_50 = 20
PASSO_ESTACAO_METROS_MODO_25 = 40


@dataclass
class FaixaResult:
    bloco_indice: int
    faixa_index: int
    arquivo: str
    caminho: Path
    km_inicio: float | None
    km_fim: float | None
    altura_px: int
    imagens_raw: list[str]
    offset_y_px: int
    percentual_ultima_img: float


@dataclass
class LoteResult:
    lado: str
    indice: int
    faixas: list[FaixaResult]
    imagens_no_lote: int


def apply_clahe(img: np.ndarray, clip_limit: float = 2.0, tile_size: int = 8) -> np.ndarray:
    clahe = cv2.createCLAHE(clipLimit=clip_limit, tileGridSize=(tile_size, tile_size))
    if len(img.shape) == 2:
        return clahe.apply(img)
    lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
    lab[:, :, 0] = clahe.apply(lab[:, :, 0])
    return cv2.cvtColor(lab, cv2.COLOR_LAB2BGR)


def _extrair_numero(path: Path) -> int:
    nums = re.findall(r"\d+", path.stem)
    return int(nums[0]) if nums else 0


def listar_imagens_origem(pasta: Path) -> list[Path]:
    if not pasta.is_dir():
        return []
    arquivos = [p for p in pasta.iterdir() if p.is_file() and p.suffix.lower() in EXTENSOES_VALIDAS]
    arquivos.sort(key=_extrair_numero)
    return arquivos


def calcular_km_faixa(
    km_inicial: float | None,
    sentido: str,
    bloco_indice: int,
    faixa_index: int,
) -> tuple[float | None, float | None]:
    if km_inicial is None:
        return None, None

    metros_por_bloco = METROS_POR_CONCAT
    km_por_bloco = metros_por_bloco / 1000
    km_por_faixa = FAIXA_METROS / 1000

    # A visualização (e o stacking de faixas) é sempre crescente em KM: a faixa 0
    # é o topo (menor KM) e a faixa 3 a base (maior KM). Partimos então do menor
    # KM do bloco e somamos o deslocamento da faixa.
    #   crescente:   bloco começa em km_inicial + bloco*0,020;
    #   decrescente: como o bloco é invertido na concatenação, o menor KM do
    #                bloco é km_inicial - (bloco+1)*0,020.
    if sentido == "decrescente":
        km_base_bloco = km_inicial - (bloco_indice + 1) * km_por_bloco
    else:
        km_base_bloco = km_inicial + bloco_indice * km_por_bloco

    km_inicio_faixa = km_base_bloco + faixa_index * km_por_faixa
    km_fim_faixa = km_inicio_faixa + km_por_faixa

    return km_inicio_faixa, km_fim_faixa


def _processar_lote(
    args: tuple,
) -> LoteResult | None:
    """Processa um lote independente (10 imagens → faixas de 5m). Executável em paralelo.

    ``faixas_alvo`` pode ser um único índice (IGG) ou uma sequência (ICP, que
    precisa de todas as faixas do bloco para cobrir o trecho sem lacunas).
    """
    (
        idx,
        batch_paths_str,
        pasta_destino_str,
        lado,
        km_inicial,
        sentido,
        clahe_clip,
        clahe_tile,
        faixas_alvo,
    ) = args
    if not isinstance(faixas_alvo, (list, tuple)):
        faixas_alvo = (faixas_alvo,)

    pasta_destino = Path(pasta_destino_str)
    batch = [Path(p) for p in batch_paths_str]

    raw_images: list[tuple[Path, np.ndarray]] = []
    for path in batch:
        img_bgr = cv2.imread(str(path))
        if img_bgr is None:
            continue
        raw_images.append((path, img_bgr))

    if not raw_images:
        return None

    resized: list[tuple[str, np.ndarray]] = []
    for path, img in raw_images:
        if img.shape[1] != LARGURA_PX:
            ratio = LARGURA_PX / img.shape[1]
            nova_altura = int(img.shape[0] * ratio)
            img = cv2.resize(img, (LARGURA_PX, nova_altura), interpolation=cv2.INTER_LANCZOS4)
        resized.append((path.name, img))

    total_height = sum(img.shape[0] for _, img in resized)
    concat_bgr = np.zeros((total_height, LARGURA_PX, 3), dtype=np.uint8)

    # A visualização é sempre crescente em KM. No sentido crescente os primeiros
    # frames já são os de menor KM (topo), então mantém a ordem. No decrescente os
    # primeiros frames são os de maior KM, então inverte-se para pôr o menor KM no topo.
    frames = list(reversed(resized)) if sentido == "decrescente" else resized

    y = 0
    for nome, img in frames:
        h = img.shape[0]
        concat_bgr[y:y+h, :, :] = img
        y += h

    pasta_destino.mkdir(parents=True, exist_ok=True)

    nomes_empilhados = [nome for nome, _ in frames]
    px_por_img = ALTURA_PX

    faixas_result: list[FaixaResult] = []
    for faixa_idx in faixas_alvo:
        y_inicio = faixa_idx * FAIXA_ALTURA_PX
        y_fim = min(y_inicio + FAIXA_ALTURA_PX, total_height)

        if y_inicio >= total_height:
            continue

        faixa_bgr = concat_bgr[y_inicio:y_fim, :, :]
        faixa_bgr = apply_clahe(faixa_bgr, clip_limit=clahe_clip, tile_size=clahe_tile)
        faixa_display = cv2.resize(
            faixa_bgr, (LARGURA_PX, FAIXA_ALTURA_DISPLAY), interpolation=cv2.INTER_LINEAR
        )

        faixa_rgb = cv2.cvtColor(faixa_display, cv2.COLOR_BGR2RGB)
        pil_faixa = Image.fromarray(faixa_rgb)

        nome_faixa = f"lote_{idx:04d}_faixa_{faixa_idx}.png"
        caminho_faixa = pasta_destino / nome_faixa
        pil_faixa.save(str(caminho_faixa))

        img_raw_inicio = y_inicio // px_por_img
        img_raw_fim = (y_fim - 1) // px_por_img
        offset_y = y_inicio % px_por_img

        imagens_raw_faixa = [
            nomes_empilhados[ri]
            for ri in range(img_raw_inicio, min(img_raw_fim + 1, len(nomes_empilhados)))
        ]

        percentual_ultima = 1.0
        if imagens_raw_faixa:
            pixels_restantes = y_fim - y_inicio
            pixels_ultima = pixels_restantes % FAIXA_ALTURA_PX
            if 0 < pixels_ultima < FAIXA_ALTURA_PX:
                percentual_ultima = pixels_ultima / ALTURA_PX

        km_inicio, km_fim = calcular_km_faixa(km_inicial, sentido, idx, faixa_idx)

        faixas_result.append(
            FaixaResult(
                bloco_indice=idx,
                faixa_index=faixa_idx,
                arquivo=nome_faixa,
                caminho=caminho_faixa,
                km_inicio=km_inicio,
                km_fim=km_fim,
                altura_px=FAIXA_ALTURA_DISPLAY,
                imagens_raw=imagens_raw_faixa,
                offset_y_px=offset_y,
                percentual_ultima_img=percentual_ultima,
            )
        )

    if not faixas_result:
        return None

    km_inicios = [f.km_inicio for f in faixas_result if f.km_inicio is not None]
    km_fins = [f.km_fim for f in faixas_result if f.km_fim is not None]
    metas_faixas = []
    for faixa in faixas_result:
        dados_faixa = asdict(faixa)
        dados_faixa["caminho"] = str(dados_faixa["caminho"])
        metas_faixas.append(dados_faixa)

    meta = {
        "bloco": {
            "indice": idx,
            "lado": lado,
            "km_inicio": min(km_inicios) if km_inicios else None,
            "km_fim": max(km_fins) if km_fins else None,
        },
        "faixas": metas_faixas,
    }

    meta_path = pasta_destino / f"lote_{idx:04d}_meta.json"
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    return LoteResult(
        lado=lado,
        indice=idx,
        faixas=faixas_result,
        imagens_no_lote=len(batch),
    )


def modo_amostragem(tipo_pista: str, faixa: int | None) -> str:
    """Modo de amostragem efetivo do IGG: ``"25"`` ou ``"50"``.

    Só a pista dupla faixa 1 (leve/rápida) usa o modo de 25 (estação efetiva de
    40 m). A pista simples e a pista dupla faixa 2 (pesada/lenta) usam o modo de
    50 (estação de 20 m). Exige o vocabulário canônico de ``tipo_pista``.
    """
    try:
        faixa_int = int(faixa) if faixa is not None else None
    except (TypeError, ValueError):
        faixa_int = None
    if tipo_pista == "dupla" and faixa_int == 1:
        return "25"
    return "50"


def parametros_amostragem(tipo_pista: str, faixa: int | None) -> tuple[int, int]:
    """Retorna ``(n_estacoes, passo_metros)`` do modo de amostragem do IGG.

    Compartilhado por ``image_processor`` (o que amostrar) e ``igg_calculator``
    (como agrupar em estações e qual ``n`` usar), garantindo a mesma regra.
    """
    if modo_amostragem(tipo_pista, faixa) == "25":
        return ESTACOES_POR_KM_MODO_25, PASSO_ESTACAO_METROS_MODO_25
    return ESTACOES_POR_KM_MODO_50, PASSO_ESTACAO_METROS_MODO_50


def deve_pular_bloco(tipo_pista: str, faixa: int | None) -> bool:
    """Indica se os blocos ímpares devem ser pulados na amostragem.

    Só a pista dupla faixa 1 (esquerda/rápida) usa apenas os índices pares, o
    que equivale ao modo de 25. A pista simples e a pista dupla faixa 2
    (direita/lenta) processam todos os blocos (modo de 50).
    Exige o vocabulário canônico de ``tipo_pista`` (``simples``/``dupla``).
    """
    return modo_amostragem(tipo_pista, faixa) == "25"


def processar_pasta(
    pasta_origem: Path,
    pasta_destino: Path,
    *,
    km_inicial: float | None = None,
    sentido: str = "crescente",
    tipo_pista: str = "simples",
    faixa: int | None = None,
    imagens_por_lote: int = IMAGENS_POR_LOTE,
    clahe_clip: float = 2.0,
    clahe_tile: int = 8,
    max_batches: int | None = None,
    workers: int | None = None,
    todas_faixas: bool = False,
) -> list[dict]:
    """
    1. Ordena imagens e agrupa em lotes de imagens_por_lote.
    2. Aplica padrão de amostragem do IGG: pula blocos conforme tipo_pista/faixa.
       No modo ``todas_faixas`` (ICP) não pula blocos.
    3. Para cada bloco, salva a faixa de 5m amostrada (IGG) ou as 4 faixas, em
       contínuo, sem lacunas (ICP).
    """
    tipo_pista = normalizar_tipo_pista(tipo_pista)
    sub_left = pasta_origem / "left"
    source = sub_left if sub_left.is_dir() else pasta_origem

    imagens = listar_imagens_origem(source)
    if not imagens:
        return []

    lotes批次: list[list[Path]] = [
        imagens[i:i + imagens_por_lote]
        for i in range(0, len(imagens), imagens_por_lote)
    ]

    if max_batches is not None:
        lotes批次 = lotes批次[:max_batches]

    pular_bloco = (not todas_faixas) and deve_pular_bloco(tipo_pista, faixa)
    if todas_faixas:
        faixas_alvo: tuple[int, ...] = tuple(range(FAIXAS_POR_LOTE))
    else:
        faixas_alvo = (0,) if sentido == "crescente" else (3,)

    blocos_a_processar: list[tuple[int, list[Path]]] = []
    for idx, batch in enumerate(lotes批次):
        if pular_bloco and idx % 2 != 0:
            continue
        blocos_a_processar.append((idx, batch))

    total_lotes = len(blocos_a_processar)

    tarefas = [
        (
            idx,
            [str(p) for p in batch],
            str(pasta_destino),
            LADO_PADRAO,
            km_inicial,
            sentido,
            clahe_clip,
            clahe_tile,
            faixas_alvo,
        )
        for idx, batch in blocos_a_processar
    ]

    resultados = process_map(
        _processar_lote,
        tarefas,
        max_workers=workers if workers else min(total_lotes, 8),
        desc=f"Concatenando {total_lotes} lotes",
        total=total_lotes,
    )

    lotes = [r for r in resultados if r is not None]
    lotes.sort(key=lambda l: l.indice)

    return [
        {
            "lado": l.lado,
            "indice": l.indice,
            "total_faixas": len(l.faixas),
            "faixas": [
                {
                    "arquivo": f.arquivo,
                    "faixa_index": f.faixa_index,
                    "km_inicio": f.km_inicio,
                    "km_fim": f.km_fim,
                    "altura_px": f.altura_px,
                }
                for f in l.faixas
            ],
            "imagens_no_lote": l.imagens_no_lote,
        }
        for l in lotes
    ]
