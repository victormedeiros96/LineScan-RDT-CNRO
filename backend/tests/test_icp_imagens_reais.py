"""ICP sobre imagens reais de linescan (integração, opt-in).

Diferente dos testes sintéticos de ``test_icp_placas``/``test_processamento_icp``,
aqui o pipeline roda sobre uma pasta real de imagens cruas do LineScan
(BMP 4096x1024, 2 m por frame): pré-processamento em faixas de 5 m, malha de
placas e amostras de 20, ROIs e inventário — conforme a DNIT 062/2004-PRO.

O teste não roda no CI padrão porque depende de um levantamento externo (as
imagens não vão para o git: ``dados/`` é ignorado). Para habilitar:

* ``RDT01_ICP_IMAGENS`` — pasta com os ``.bmp`` crus (padrão: ``dados/left``
  na raiz do repositório);
* ``RDT01_ICP_PERIODO_M`` — período das juntas em metros (calibração manual).
  Obrigatório para o teste de geometria: o período é a informação de campo que
  o teste não pode adivinhar (ver DNIT 062/2004-PRO — malha de placas).
* ``RDT01_ICP_N_IMAGENS`` — quantos frames usar (padrão ``10`` = 1 bloco de
  20 m; precisa ser múltiplo de 10 para formar blocos completos).

Exemplo:

    RDT01_ICP_IMAGENS=dados/left RDT01_ICP_PERIODO_M=5.12 \
        pytest backend/tests/test_icp_imagens_reais.py -q
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.jobs import _processar_placas_icp
from src.services import icp_placas
from src.services import image_processor as ip

REPO = Path(__file__).resolve().parents[2]
IMAGENS = Path(os.environ.get("RDT01_ICP_IMAGENS", REPO / "dados" / "left"))
N_IMAGENS = int(os.environ.get("RDT01_ICP_N_IMAGENS", "10"))
PERIODO_M = os.environ.get("RDT01_ICP_PERIODO_M")

IMAGENS_POR_BLOCO = ip.IMAGENS_POR_LOTE  # 10 frames de 2 m = 20 m
FAIXAS_POR_BLOCO = ip.FAIXAS_POR_LOTE  # 4 faixas de 5 m
N_BLOCOS = N_IMAGENS // IMAGENS_POR_BLOCO
TRECHO_M = N_BLOCOS * ip.METROS_POR_CONCAT

pytestmark = pytest.mark.skipif(
    not IMAGENS.is_dir(),
    reason=(
        "Sem levantamento real: defina RDT01_ICP_IMAGENS (pasta de .bmp do "
        "LineScan) para rodar a integração do ICP."
    ),
)


def _settings(base: Path) -> SimpleNamespace:
    """Settings mínimo: a calibração é manual, então o modelo não é usado."""
    return SimpleNamespace(
        dados_dir=base,
        modelos_dir=REPO / "modelos",
        icp_nuclio_dir=base / "nao-existe",
        icp_modelo_pt=base / "nao-existe.pt",
        icp_limiar_juntas=0.8,
        icp_ordem_invertida=True,
        icp_mm_por_px_y=1.953125,
    )


@pytest.fixture(scope="module")
def trecho(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, list]:
    """Copia (symlink) os primeiros N frames e roda o pré-processamento real."""
    tmp_path = tmp_path_factory.mktemp("icp_real")
    origem = tmp_path / "origem"
    origem.mkdir()
    imagens = ip.listar_imagens_origem(IMAGENS)[:N_IMAGENS]
    assert len(imagens) == N_IMAGENS, (
        f"Esperava {N_IMAGENS} imagens em {IMAGENS}, achei {len(imagens)}."
    )
    for imagem in imagens:
        os.symlink(imagem.resolve(), origem / imagem.name)

    destino = tmp_path / "viagem"
    ip.processar_pasta(
        origem,
        destino,
        km_inicial=100.0,
        sentido="crescente",
        tipo_pista="simples",
        todas_faixas=True,
        workers=1,
    )
    return destino, icp_placas.listar_faixas(destino)


def test_preprocessa_imagens_reais_em_faixas_continuas(trecho: tuple[Path, list]) -> None:
    """1 bloco real de 20 m vira 4 faixas de 5 m, contínuas e no KM informado."""
    destino, faixas = trecho

    assert len(faixas) == N_BLOCOS * FAIXAS_POR_BLOCO
    # KM contínuo: o fim de cada faixa é o início da seguinte.
    for anterior, atual in zip(faixas, faixas[1:]):
        assert atual.km_inicio == pytest.approx(anterior.km_fim)
    assert faixas[0].km_inicio == pytest.approx(100.0)
    assert faixas[-1].km_fim == pytest.approx(100.0 + TRECHO_M / 1000)
    # Cada faixa de display tem a escala do LineScan: 4096 x 5120 px (1024 px/m).
    for faixa in faixas:
        assert faixa.altura_px == icp_placas.ALTURA_FAIXA_PX
        assert (destino / faixa.arquivo).is_file()

    from PIL import Image

    largura, altura = Image.open(destino / faixas[0].arquivo).size
    assert (largura, altura) == (icp_placas.LARGURA_FAIXA_PX, icp_placas.ALTURA_FAIXA_PX)


@pytest.mark.skipif(
    PERIODO_M is None,
    reason="Defina RDT01_ICP_PERIODO_M (período das juntas em metros) para validar a geometria.",
)
def test_geometria_do_icp_no_trecho_real(trecho: tuple[Path, list]) -> None:
    """Com o período de campo, a malha de placas cobre o trecho sem lacunas.

    Trava as invariantes da DNIT 062/2004-PRO implementadas em ``icp_placas``:
    uma placa por intervalo entre juntas, malha contínua, amostras de até 20
    placas, uma ROI por placa e nenhum descarte quando o trecho tem cobertura.
    """
    periodo = float(PERIODO_M)
    assert periodo > 0
    destino, faixas = trecho
    km0 = faixas[0].km_inicio

    resultado = _processar_placas_icp(
        destino, _settings(destino), {"periodo_m": periodo, "fase_m": 0.0}
    )

    # floor(trecho / período): a última placa precisa caber inteira no trecho.
    placas_esperadas = int(TRECHO_M / periodo)
    assert resultado["total_faixas"] == len(faixas)
    assert resultado["total_placas"] == placas_esperadas
    assert resultado["total_rois"] == placas_esperadas
    assert resultado["placas_descartadas"] == 0
    assert resultado["total_amostras"] == (placas_esperadas + 19) // 20
    assert resultado["calibracao"]["fonte"] == "manual"
    assert resultado["calibracao"]["periodo_m"] == pytest.approx(periodo)
    assert resultado["calibracao"]["norma"] == "DNIT 062/2004-PRO"

    inventario = json.loads((destino / "icp_placas.json").read_text(encoding="utf-8"))
    assert inventario["norma"] == "DNIT 062/2004-PRO"
    assert inventario["trecho"]["km_inicio"] == pytest.approx(km0)
    assert inventario["trecho"]["km_fim"] == pytest.approx(km0 + TRECHO_M / 1000)
    assert inventario["trecho"]["n_faixas"] == len(faixas)

    placas = inventario["placas"]
    assert len(placas) == placas_esperadas
    for anterior, atual in zip(placas, placas[1:]):
        assert atual["km_inicio"] == pytest.approx(anterior["km_fim"])
    for placa in placas:
        assert placa["comprimento_m"] == pytest.approx(periodo)
        assert placa["roi"] is not None
        assert (destino / "icp_rois" / f"icp_placa{placa['id'][1:].zfill(4)}.png").is_file()
