"""Detecção e calibração das juntas transversais do pavimento rígido (ICP).

A calibração pode vir de três fontes, nesta ordem:

1. ``juntas_km`` informadas (lista de KM absolutos das juntas);
2. ``periodo_m``/``fase_m`` informados manualmente;
3. **detecção automática** com o pacote ``juntas-detector`` v0.2.0 e o modelo
   externo ``trinca-detector`` v2.1.0 (GPL-3.0), via ``ProvedorTrincaDetector``.

O detector é usado para calibrar a rede periódica (período + fase) **e** para
entregar a geometria das juntas realmente segmentadas. ``icp_placas`` monta cada
placa como o intervalo entre duas juntas consecutivas detectadas — o tamanho sai
da imagem e pode variar dentro da mesma viagem, sem um tamanho fixo declarado.

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
# Junta transversal é perpendicular à via. Trincas diagonais não devem virar
# transversal: rejeita ajuste com inclinação acima deste limite (graus).
ANGULO_MAX_TRANSVERSAL = 20.0


def _selecionar_transversal(
    grupos: Iterable[dict[str, Any]],
    *,
    altura: int,
    largura: int,
) -> dict[str, Any] | None:
    """Escolhe a transversal mais confiável de uma janela.

    ``geometria.agrupar`` estende ``t0``/``t1`` de borda a borda
    (``estender=True``), então **todo** grupo horizontal sai com a mesma
    cobertura lateral; usar ``t1 - t0`` como desempate (como antes) devolvia
    sempre o primeiro grupo, muitas vezes um ajuste extrapolado fora da
    imagem. Aqui a validade é medida no centro lateral (``y`` em ``x_ref``,
    que é o mesmo ponto usado para o KM) e o desempate usa o nº de fragmentos
    e o comprimento real medido nos pontos — o critério de
    ``geometria.melhor_por_frame``, adaptado ao referencial do KM.
    """
    x_ref = largura / 2.0
    validos: list[tuple[float, dict[str, Any]]] = []
    for grupo in grupos:
        if grupo.get("orientacao") != "h":
            continue
        if abs(float(grupo.get("angulo_graus") or 0.0)) > ANGULO_MAX_TRANSVERSAL:
            continue
        y_centro = float(grupo["a"]) * x_ref + float(grupo["b"])
        if not (-5.0 <= y_centro <= altura + 5.0):
            continue
        validos.append((y_centro, grupo))
    if not validos:
        return None
    _, escolhido = max(
        validos,
        key=lambda item: (
            int(item[1].get("n_fragmentos") or 1),
            float(item[1].get("comprimento") or 0.0),
        ),
    )
    return escolhido


def _agrupar_juntas(juntas: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Remove duplicatas da mesma junta (detectada em janelas vizinhas).

    A tolerância é em **metros**; ``km`` é em quilômetros, daí a conversão.
    Comparar ``km`` com metros (bug do RDT1-31) tratava qualquer par de juntas
    a menos de 150 m como duplicata e deixava ~1 junta por 100 m.
    """
    if not juntas:
        return []
    ordenadas = sorted(juntas, key=lambda j: float(j["km"]))
    agrupadas = [ordenadas[0]]
    for junta in ordenadas[1:]:
        distancia_m = abs(float(junta["km"]) - float(agrupadas[-1]["km"])) * 1000.0
        if distancia_m >= TOLERANCIA_JUNTA_M:
            agrupadas.append(junta)
    return agrupadas


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


def detectar_juntas_geometria(
    base: Path,
    faixas: list[icp_placas.FaixaInfo],
    config: ConfigJuntas,
    *,
    max_frames: int = MAX_FRAMES_CALIBRACAO,
) -> list[dict[str, Any]]:
    """Detecta as juntas transversais e devolve a geometria segmentada na imagem.

    Cada item traz:

    * ``km`` — posição da junta no eixo da via (no centro da faixa);
    * ``angulo_graus`` — inclinação da transversal na imagem;
    * ``x0``/``x1`` — extensão lateral (px) da junta na faixa.

    É essa geometria que ``icp_placas.gerar_placas_por_juntas`` usa para montar
    cada placa como o intervalo entre duas juntas consecutivas **reais** — sem
    assumir um tamanho de placa fixo.
    """
    if not config.disponivel:
        raise RuntimeError(
            "Modelo de juntas indisponível. Configure 'juntas' em "
            "modelos/icp/config.json (nuclio_dir e modelo_pt) ou defina "
            "ICP_NUCLIO_DIR/ICP_MODELO_PT."
        )

    from juntas_detector import ProvedorTrincaDetector, geometria

    provedor = _ProvedorFaixas(base, faixas)
    modelo = ProvedorTrincaDetector(
        config.nuclio_dir, config.modelo_pt, limiar=config.limiar
    )
    largura = icp_placas.LARGURA_FAIXA_PX
    x_ref = largura / 2.0

    juntas: list[dict[str, Any]] = []
    for indice in range(min(len(provedor), max_frames)):
        imagem = provedor.frame(indice)
        if imagem is None or imagem.size == 0:
            continue
        formas = modelo.detectar(imagem)
        pontos = ProvedorTrincaDetector.para_pontos(formas, "junta")
        if not pontos:
            continue
        grupos = geometria.agrupar(
            pontos, largura=largura, altura=int(imagem.shape[0])
        )
        # Em 2 m cabe no máximo uma junta transversal: fica a mais confiável.
        junta = _selecionar_transversal(
            grupos, altura=int(imagem.shape[0]), largura=largura
        )
        if junta is None:
            continue
        y_centro = float(junta["a"]) * x_ref + float(junta["b"])
        juntas.append(
            {
                "km": provedor.km_da_janela(indice, y_centro),
                "angulo_graus": float(junta.get("angulo_graus") or 0.0),
                "x0": float(junta["t0"]),
                "x1": float(junta["t1"]),
            }
        )

    juntas = _agrupar_juntas(juntas)
    return juntas


class _ProvedorFormas:
    """Adapta as formas já inferidas ao protocolo ``infer`` do pacote.

    Evita rodar o modelo duas vezes por bloco: as transversais e as longitudinais
    saem da **mesma** inferência sobre o strip concatenado.
    """

    def __init__(self, formas: list[dict[str, Any]]):
        self._formas = formas

    def infer(self, _imagem):
        return self._formas


def _agrupar_por_bloco(
    faixas: list[icp_placas.FaixaInfo],
) -> list[tuple[int, list[icp_placas.FaixaInfo]]]:
    por_bloco: dict[int, list[icp_placas.FaixaInfo]] = {}
    for faixa in sorted(faixas, key=lambda f: (f.km_inicio, f.faixa_index)):
        por_bloco.setdefault(faixa.bloco_indice, []).append(faixa)
    return sorted(por_bloco.items(), key=lambda item: item[1][0].km_inicio)


def _strip_do_bloco(
    base: Path, faixas_bloco: list[icp_placas.FaixaInfo]
) -> tuple[Any, int, float]:
    """Concatena as faixas de um bloco em uma imagem e devolve ``(strip, altura, km_inicio)``."""
    import cv2
    import numpy as np

    blocos = []
    altura_total = 0
    for faixa in faixas_bloco:
        caminho = Path(faixa.arquivo)
        if not caminho.is_absolute():
            caminho = Path(base) / caminho
        imagem = cv2.imread(str(caminho), cv2.IMREAD_GRAYSCALE)
        if imagem is None:
            raise FileNotFoundError(f"Faixa não encontrada: {caminho}")
        blocos.append(imagem)
        altura_total += imagem.shape[0]
    strip = np.vstack(blocos)
    return strip, altura_total, float(faixas_bloco[0].km_inicio)


def _selecionar_par_longitudinais(
    retas: list[Any],
    *,
    mm_por_px_x: float,
    altura_px: int,
    largura_min_m: float = 3.0,
    largura_max_m: float = 4.5,
) -> tuple[Any, Any] | None:
    """Escolhe o par de longitudinais que forma a largura da placa (3,0–4,5 m)."""
    validas = [r for r in retas if 0.0 <= r.x(altura_px / 2.0) <= icp_placas.LARGURA_FAIXA_PX - 1]
    if len(validas) < 2:
        return None
    melhor: tuple[float, tuple[Any, Any]] | None = None
    for i, esq in enumerate(validas):
        for dir_ in validas[i + 1:]:
            largura_m = abs(dir_.x(altura_px / 2.0) - esq.x(altura_px / 2.0)) * mm_por_px_x / 1000.0
            if not (largura_min_m <= largura_m <= largura_max_m):
                continue
            erro = abs(largura_m - 3.6)
            if melhor is None or erro < melhor[0]:
                par = sorted([esq, dir_], key=lambda r: r.x(altura_px / 2.0))
                melhor = (erro, (par[0], par[1]))
    return melhor[1] if melhor else None


def detectar_estrutura(
    base: Path,
    faixas: list[icp_placas.FaixaInfo],
    config: ConfigJuntas,
    *,
    max_blocos: int | None = None,
    comprimento_max_m: float = icp_placas.COMPRIMENTO_MAX_M,
) -> dict[str, Any]:
    """Detecta transversais **e** longitudinais sobre a imagem concatenada.

    Concatena as faixas de cada bloco (≈20 m), roda o modelo uma vez por bloco e
    separa as duas famílias de juntas. As transversais saem no eixo do KM
    (``km`` absoluto) e o par de longitudinais fica em coordenadas do strip da
    viagem, pronto para o cruzamento em ``icp_blocos.montar_blocos``.

    Devolve ``transversais``, ``retas_por_bloco``, ``blocos_ranges`` e ``km0``.
    Levanta erro explícito quando o modelo não está disponível ou não há faixas.
    """
    from src.services import icp_blocos

    if not config.disponivel:
        raise RuntimeError(
            "Modelo de juntas indisponível. Configure 'juntas' em "
            "modelos/icp/config.json (nuclio_dir e modelo_pt) ou defina "
            "ICP_NUCLIO_DIR/ICP_MODELO_PT."
        )
    if not faixas:
        raise ValueError("Nenhuma faixa disponível para detectar juntas.")

    from juntas_detector import ProvedorTrincaDetector, geometria, longitudinais

    modelo = ProvedorTrincaDetector(
        config.nuclio_dir, config.modelo_pt, limiar=config.limiar
    )
    mm_x = icp_placas.MM_POR_PX
    km0 = min(float(f.km_inicio) for f in faixas)
    largura = icp_placas.LARGURA_FAIXA_PX
    x_ref = largura / 2.0

    transversais: list[dict[str, Any]] = []
    retas_por_bloco: dict[int, tuple[Any, Any]] = {}
    blocos_ranges: list[tuple[int, float, float]] = []

    grupos = _agrupar_por_bloco(faixas)
    for indice, (bloco_indice, faixas_bloco) in enumerate(grupos):
        if max_blocos is not None and indice >= max_blocos:
            break
        strip, altura, km_inicio = _strip_do_bloco(base, faixas_bloco)
        y0_global = (km_inicio - km0) * icp_placas.PX_POR_METRO
        blocos_ranges.append((bloco_indice, y0_global, y0_global + altura))

        formas = modelo.detectar(strip)
        pontos = ProvedorTrincaDetector.para_pontos(formas, "junta")
        if pontos:
            grupos_geo = geometria.agrupar(
                pontos, largura=largura, altura=altura
            )
            for grupo in grupos_geo:
                if grupo.get("orientacao") != "h":
                    continue
                if abs(float(grupo.get("angulo_graus") or 0.0)) > ANGULO_MAX_TRANSVERSAL:
                    # Trinca diagonal não é junta transversal (DNIT 062/2004-PRO).
                    continue
                y_centro = float(grupo["a"]) * x_ref + float(grupo["b"])
                if not (-5.0 <= y_centro <= altura + 5.0):
                    # Ajuste extrapolado para fora do strip: não é junta.
                    continue
                transversais.append(
                    {
                        "y": y0_global + y_centro,
                        "angulo_graus": float(grupo.get("angulo_graus") or 0.0),
                        "x0": float(grupo["t0"]),
                        "x1": float(grupo["t1"]),
                        "origem": "detectada",
                        "bloco_indice": bloco_indice,
                    }
                )

        provedor = _ProvedorFormas(formas)
        fragmentos = longitudinais.fragmentos_verticais(provedor, strip)
        candidatas = [
            reta
            for reta in longitudinais.agrupa_por_retas(fragmentos)
            if longitudinais.valida(reta, altura)
        ]
        par = _selecionar_par_longitudinais(
            candidatas, mm_por_px_x=mm_x, altura_px=altura
        )
        if par is not None:
            retas_por_bloco[bloco_indice] = (
                icp_blocos.reta_para_global(par[0], y0_global),
                icp_blocos.reta_para_global(par[1], y0_global),
            )

    for junta in transversais:
        junta["km"] = round(
            km0 + float(junta["y"]) * icp_placas.MM_POR_PX / 1_000_000.0, 6
        )

    _ = comprimento_max_m  # o limite é aplicado no fechamento dos blocos
    return {
        "transversais": transversais,
        "retas_por_bloco": retas_por_bloco,
        "blocos_ranges": blocos_ranges,
        "km0": km0,
    }


def detectar_juntas_km(
    base: Path,
    faixas: list[icp_placas.FaixaInfo],
    config: ConfigJuntas,
    *,
    max_frames: int = MAX_FRAMES_CALIBRACAO,
) -> list[float]:
    """Detecta as juntas transversais e devolve só os KM absolutos (ordenados)."""
    return [
        float(junta["km"])
        for junta in detectar_juntas_geometria(
            base, faixas, config, max_frames=max_frames
        )
    ]


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

    juntas_geometria = detectar_juntas_geometria(base, faixas, detector)
    juntas_km = [float(junta["km"]) for junta in juntas_geometria]
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
    # A geometria segmentada segue com a calibração para a etapa de placas: cada
    # placa é montada entre duas juntas reais, sem tamanho fixo declarado.
    cal["juntas_geometria"] = juntas_geometria
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
