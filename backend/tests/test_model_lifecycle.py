"""Testes do ciclo de vida dos modelos (RDT1-16).

Não carregam torch/onnxruntime/ultralytics: o loader é injetado e devolve um
objeto falso, então a suíte roda em qualquer ambiente.
"""

from __future__ import annotations

import time
from pathlib import Path

from src.services.model_lifecycle import ModelManager


class FakeModel:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class CountingLoader:
    def __init__(self) -> None:
        self.calls = 0
        self.models: list[FakeModel] = []

    def __call__(self) -> FakeModel:
        self.calls += 1
        model = FakeModel()
        self.models.append(model)
        return model


def _manager(ttl: float = 60.0) -> ModelManager:
    return ModelManager(ttl_seconds=ttl, start_reaper=False)


def _path(tmp_path: Path) -> Path:
    return tmp_path / "modelo.onnx"


def test_lazy_load_nao_carrega_antes_do_primeiro_uso(tmp_path: Path) -> None:
    manager = _manager()
    loader = CountingLoader()
    path = _path(tmp_path)

    assert manager.snapshot() == []
    assert loader.calls == 0

    lease = manager.acquire(path, loader)
    try:
        assert loader.calls == 1
    finally:
        lease.release()


def test_reuso_reaproveita_a_mesma_instancia_entre_usos(tmp_path: Path) -> None:
    manager = _manager()
    loader = CountingLoader()
    path = _path(tmp_path)

    with manager.acquire(path, loader) as first:
        pass
    with manager.acquire(path, loader) as second:
        assert second.model is first.model

    assert loader.calls == 1
    assert len(loader.models) == 1


def test_ttl_descarrega_modelo_ocioso(tmp_path: Path) -> None:
    manager = _manager(ttl=5.0)
    loader = CountingLoader()
    path = _path(tmp_path)

    with manager.acquire(path, loader):
        pass

    assert manager.reap_idle(now=time.monotonic() + 1.0) == []
    assert len(manager.snapshot()) == 1

    unloaded = manager.reap_idle(now=time.monotonic() + 10.0)
    assert len(unloaded) == 1
    assert manager.snapshot() == []
    assert loader.models[0].closed is True

    with manager.acquire(path, loader):
        pass
    assert loader.calls == 2


def test_refcount_impede_descarga_durante_uso(tmp_path: Path) -> None:
    manager = _manager(ttl=5.0)
    loader = CountingLoader()
    path = _path(tmp_path)

    lease = manager.acquire(path, loader)
    try:
        assert manager.reap_idle(now=time.monotonic() + 10.0) == []
        assert len(manager.snapshot()) == 1
        assert loader.models[0].closed is False
    finally:
        lease.release()

    assert len(manager.reap_idle(now=time.monotonic() + 10.0)) == 1
    assert loader.models[0].closed is True


def test_ttl_zero_desabilita_descarga(tmp_path: Path) -> None:
    manager = _manager(ttl=0)
    loader = CountingLoader()
    path = _path(tmp_path)

    with manager.acquire(path, loader):
        pass

    assert manager.reap_idle(now=time.monotonic() + 10_000.0) == []
    assert len(manager.snapshot()) == 1
