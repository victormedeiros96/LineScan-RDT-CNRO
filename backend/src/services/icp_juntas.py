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

O modelo foi treinado nos quadros crus do sensor (512 px/m no eixo longitudinal).
Por isso a detecção roda sobre a **faixa crua** gravada no modo ICP
(``lote_*_faixa_*_infer.png``, sem CLAHE) e converte a geometria para o display de
1024 px/m antes de entregá-la (RDT1-31).

Norma: DNIT 062/2004-PRO (avaliação objetiva de pavimento rígido).
"""
from __future__ import annotations

import json
import logging
import math
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
    """Fornece janelas de 1024 px na **escala de detecção** de cada faixa.

    A detecção usa a faixa crua do sensor (``arquivo_infer``, 512 px/m) quando
    existe — a mesma escala em que o modelo foi treinado — e cai para o display
    com CLAHE só quando ela não estiver disponível (RDT1-31).
    """

    def __init__(self, base: Path, faixas: list[icp_placas.FaixaInfo]):
        self.base = Path(base)
        self.faixas = sorted(faixas, key=lambda f: (f.km_inicio, f.faixa_index))
        self.janelas: list[tuple[icp_placas.FaixaInfo, int]] = []
        for faixa in self.faixas:
            altura = faixa.altura_infer_px or faixa.altura_px
            for y0 in range(0, int(altura), 1024):
                self.janelas.append((faixa, y0))

    def __len__(self) -> int:
        return len(self.janelas)

    def frame(self, indice: int):
        import cv2

        faixa, y0 = self.janelas[indice]
        caminho = Path(faixa.arquivo_deteccao)
        if not caminho.is_absolute():
            caminho = self.base / caminho
        imagem = cv2.imread(str(caminho), cv2.IMREAD_GRAYSCALE)
        if imagem is None:
            raise FileNotFoundError(f"Faixa não encontrada: {caminho}")
        return imagem[y0:y0 + 1024, :]

    def km_da_janela(self, indice: int, y_na_janela: float) -> float:
        faixa, y0 = self.janelas[indice]
        metros_por_px = 1.0 / faixa.escala_deteccao
        return float(faixa.km_inicio) + (y0 + y_na_janela) * metros_por_px / 1000.0


def _angulo_para_display(angulo_graus: float, fator: float) -> float:
    """Converte a inclinação de uma transversal da escala de detecção para o display.

    ``fator`` é quantos pixels de display equivalem a um pixel de detecção no
    eixo longitudinal (2 para a faixa crua, 1 para o próprio display). A reta
    ``y = a*x + b`` vira ``y_disp = fator*a*x + fator*b``; o ângulo acompanha.
    """
    if fator == 1.0:
        return float(angulo_graus)
    return float(math.degrees(math.atan(math.tan(math.radians(angulo_graus)) * fator)))



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
        transversais = [g for g in grupos if g.get("orientacao") == "h"]
        if not transversais:
            continue
        # Em 2 m cabe no máximo uma junta transversal: fica a de maior cobertura.
        junta = max(transversais, key=lambda g: float(g["t1"] - g["t0"]))
        y_centro = float(junta["a"]) * x_ref + float(junta["b"])
        faixa = provedor.janelas[indice][0]
        juntas.append(
            {
                "km": provedor.km_da_janela(indice, y_centro),
                "angulo_graus": _angulo_para_display(
                    float(junta.get("angulo_graus") or 0.0), faixa.fator_para_display
                ),
                "x0": float(junta["t0"]),
                "x1": float(junta["t1"]),
            }
        )

    juntas.sort(key=lambda j: j["km"])
    if len(juntas) >= 2:
        agrupadas = [juntas[0]]
        for junta in juntas[1:]:
            if abs(junta["km"] - agrupadas[-1]["km"]) >= TOLERANCIA_JUNTA_M:
                agrupadas.append(junta)
        juntas = agrupadas
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
) -> tuple[Any, int, float, float]:
    """Concatena as faixas de um bloco na escala de detecção.

    Devolve ``(strip, altura, km_inicio, fator_para_display)``. Quando a faixa
    crua está disponível (``arquivo_infer``), o strip fica em 512 px/m — a
    escala dos quadros de treino — e ``fator_para_display`` é 2 (o display tem
    1024 px/m). Sem ela, cai para o display com CLAHE e o fator é 1.
    """
    import cv2
    import numpy as np

    blocos = []
    altura_total = 0
    for faixa in faixas_bloco:
        caminho = Path(faixa.arquivo_deteccao)
        if not caminho.is_absolute():
            caminho = Path(base) / caminho
        imagem = cv2.imread(str(caminho), cv2.IMREAD_GRAYSCALE)
        if imagem is None:
            raise FileNotFoundError(f"Faixa não encontrada: {caminho}")
        blocos.append(imagem)
        altura_total += imagem.shape[0]
    strip = np.vstack(blocos)
    fator = float(faixas_bloco[0].fator_para_display)
    return strip, altura_total, float(faixas_bloco[0].km_inicio), fator


def _reta_para_display(reta: Any, fator: float) -> Any:
    """Leva uma reta longitudinal ``x = a*y + b`` da escala de detecção ao display."""
    from juntas_detector.longitudinais import Reta

    if fator == 1.0:
        return reta
    return Reta(
        a=reta.a / fator,
        b=reta.b,
        n=reta.n,
        comp=reta.comp * fator,
        y0=reta.y0 * fator,
        y1=reta.y1 * fator,
        cobertura=reta.cobertura,
        residuo=reta.residuo * fator,
        obs=[(inicio * fator, fim * fator) for inicio, fim in reta.obs],
    )


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
        strip, altura, km_inicio, fator = _strip_do_bloco(base, faixas_bloco)
        y0_global = (km_inicio - km0) * icp_placas.PX_POR_METRO
        blocos_ranges.append(
            (bloco_indice, y0_global, y0_global + altura * fator)
        )

        formas = modelo.detectar(strip)
        pontos = ProvedorTrincaDetector.para_pontos(formas, "junta")
        if pontos:
            grupos_geo = geometria.agrupar(
                pontos, largura=largura, altura=altura
            )
            for grupo in grupos_geo:
                if grupo.get("orientacao") != "h":
                    continue
                if float(grupo.get("t1", 0.0)) - float(grupo.get("t0", 0.0)) <= 0:
                    continue
                y_centro = float(grupo["a"]) * x_ref + float(grupo["b"])
                transversais.append(
                    {
                        "y": y0_global + y_centro * fator,
                        "angulo_graus": _angulo_para_display(
                            float(grupo.get("angulo_graus") or 0.0), fator
                        ),
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
            esq = _reta_para_display(par[0], fator)
            dir_ = _reta_para_display(par[1], fator)
            retas_por_bloco[bloco_indice] = (
                icp_blocos.reta_para_global(esq, y0_global),
                icp_blocos.reta_para_global(dir_, y0_global),
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
