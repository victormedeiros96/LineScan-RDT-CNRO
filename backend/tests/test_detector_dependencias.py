"""Dependências da detecção automática de juntas do ICP (RDT1-28).

O ``trinca-detector`` embutido na imagem importa ``skimage.morphology.skeletonize``
em ``nuclio/model_handler.py``, mas o wheel do ``juntas-detector`` não declara
``scikit-image``. Sem ele o worker falhava com ``ModuleNotFoundError: No module
named 'skimage'`` apenas no caminho de detecção automática (sem juntas manuais).

Este teste falha no build/CI se ``scikit-image`` sair das dependências, pegando a
regressão antes do deploy no Coolify.
"""
from __future__ import annotations


def test_skimage_skeletonize_disponivel() -> None:
    from skimage.morphology import skeletonize

    assert callable(skeletonize)


def test_juntas_detector_importavel() -> None:
    from juntas_detector import ProvedorTrincaDetector

    assert hasattr(ProvedorTrincaDetector, "detectar")
