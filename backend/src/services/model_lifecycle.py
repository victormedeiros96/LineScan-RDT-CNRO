"""Ciclo de vida dos modelos de visão (RDT1-16).

Um único ``ModelManager`` por processo mantém os modelos carregados sob demanda
(lazy), reutiliza a mesma instância enquanto houver uso (refcount) e libera
memória/VRAM depois de um tempo ocioso configurável (``MODEL_IDLE_TTL_SECONDS``).

O backend (uvicorn) e o worker (RQ) usam o mesmo gerenciador — cada processo tem
o seu, mas ambos seguem o mesmo comportamento. Nada é carregado no startup: a
primeira aquisição dispara o carregamento.

Nenhuma dependência pesada (torch/onnxruntime/ultralytics) é importada no topo
do módulo; tudo é importado dentro do loader/descarga para preservar o lazy load.
"""

from __future__ import annotations

import gc
import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

# TTL ocioso padrão: 5 min (faixa sugerida no card: 5–10 min).
DEFAULT_IDLE_TTL_SECONDS = 300


def load_model(path: str | Path, *, use_cuda: bool = True) -> Any:
    """Carrega um modelo ``.pt`` (Ultralytics) ou ``.onnx`` (ONNX Runtime)."""
    model_path = Path(path)
    if model_path.suffix.lower() == ".pt":
        from ultralytics import YOLO

        return YOLO(str(model_path))

    from src.services.inference_engine import YOLOv8ONNX

    return YOLOv8ONNX(str(model_path), use_cuda=use_cuda)


def _vram_mb() -> dict[str, float] | None:
    """Métricas de VRAM, sem inicializar o contexto CUDA (não quebra o lazy)."""
    try:
        import torch
    except Exception:  # pragma: no cover - ambiente sem torch
        return None

    try:
        if not torch.cuda.is_initialized():
            return None
        return {
            "allocated_mb": round(torch.cuda.memory_allocated() / (1024 * 1024), 1),
            "reserved_mb": round(torch.cuda.memory_reserved() / (1024 * 1024), 1),
        }
    except Exception:  # pragma: no cover - GPU indisponível
        return None


def _empty_cuda_cache() -> None:
    try:
        import torch
    except Exception:  # pragma: no cover - ambiente sem torch
        return

    try:
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:  # pragma: no cover - CUDA indisponível
        pass


@dataclass
class _Entry:
    model: Any
    refcount: int = 0
    last_used: float = field(default_factory=time.monotonic)


class ModelLease:
    """Alça de uso de um modelo; o refcount só cai quando é liberada."""

    def __init__(self, manager: "ModelManager", key: str, model: Any):
        self._manager = manager
        self.key = key
        self.model = model
        self._released = False

    def release(self) -> None:
        if not self._released:
            self._released = True
            self._manager._release(self.key)

    def __enter__(self) -> "ModelLease":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.release()


class ModelManager:
    """Cache de modelos por caminho, com refcount e descarga por ociosidade."""

    def __init__(self, ttl_seconds: float = DEFAULT_IDLE_TTL_SECONDS, *, start_reaper: bool = True):
        self.ttl_seconds = ttl_seconds
        self._start_reaper = start_reaper
        self._lock = threading.RLock()
        self._entries: dict[str, _Entry] = {}
        self._reaper: Optional[threading.Thread] = None

    @property
    def ttl(self) -> float:
        return float(self.ttl_seconds)

    def acquire(self, path: str | Path, loader: Callable[[], Any] | None = None) -> ModelLease:
        """Devolve o modelo (carregando na primeira vez) e incrementa o refcount."""
        key = str(Path(path).resolve())
        loader = loader or (lambda: load_model(path))

        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                logger.info("Carregando modelo sob demanda: %s", key)
                entry = _Entry(model=loader())
                self._entries[key] = entry
                logger.info("Modelo carregado: %s | VRAM=%s", key, _vram_mb())
            else:
                logger.debug("Reutilizando modelo já carregado: %s", key)

            entry.refcount += 1
            entry.last_used = time.monotonic()
            self._ensure_reaper()
            return ModelLease(self, key, entry.model)

    def _release(self, key: str) -> None:
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return
            if entry.refcount > 0:
                entry.refcount -= 1
            entry.last_used = time.monotonic()

    def reap_idle(self, now: float | None = None) -> list[str]:
        """Descarrega modelos sem uso há mais que o TTL. Retorna as chaves liberadas."""
        if self.ttl <= 0:
            return []

        now = time.monotonic() if now is None else now
        unloaded: list[str] = []

        with self._lock:
            for key, entry in list(self._entries.items()):
                if entry.refcount > 0:
                    continue
                if now - entry.last_used < self.ttl:
                    continue
                self._unload_locked(key, entry)
                unloaded.append(key)

        return unloaded

    def _unload_locked(self, key: str, entry: _Entry) -> None:
        model = entry.model
        entry.model = None  # type: ignore[assignment]
        self._entries.pop(key, None)

        close = getattr(model, "close", None)
        if callable(close):
            try:
                close()
            except Exception:  # pragma: no cover - descarga best-effort
                logger.warning("Falha ao fechar o modelo %s", key, exc_info=True)
        del model
        gc.collect()
        _empty_cuda_cache()
        logger.info("Modelo descarregado por ociosidade: %s | VRAM=%s", key, _vram_mb())

    def _ensure_reaper(self) -> None:
        if not self._start_reaper or self.ttl <= 0 or self._reaper is not None:
            return
        self._reaper = threading.Thread(
            target=self._reaper_loop,
            name="model-lifecycle-reaper",
            daemon=True,
        )
        self._reaper.start()

    def _reaper_loop(self) -> None:
        interval = max(0.5, min(self.ttl / 2, 60.0))
        while True:
            time.sleep(interval)
            try:
                self.reap_idle()
            except Exception:  # pragma: no cover - o reaper nunca deve morrer
                logger.warning("Falha ao descarregar modelos ociosos", exc_info=True)

    def snapshot(self) -> list[dict[str, Any]]:
        """Visão do estado atual, para observabilidade/testes."""
        now = time.monotonic()
        with self._lock:
            return [
                {
                    "path": key,
                    "refcount": entry.refcount,
                    "idle_seconds": round(now - entry.last_used, 3),
                }
                for key, entry in self._entries.items()
            ]


_manager: Optional[ModelManager] = None
_manager_lock = threading.Lock()


def get_model_manager() -> ModelManager:
    """Gerenciador do processo (backend ou worker), configurado pelo ambiente."""
    global _manager
    if _manager is None:
        with _manager_lock:
            if _manager is None:
                from src.core.config import get_settings

                settings = get_settings()
                _manager = ModelManager(settings.model_idle_ttl_seconds)
                logger.info(
                    "ModelManager inicializado (TTL ocioso=%ss)",
                    settings.model_idle_ttl_seconds,
                )
    return _manager
