from __future__ import annotations

from pathlib import Path
import json

from src.models.project import ModelConfig, ModeloInfo


def listar_modelos(modelos_dir: Path) -> list[ModeloInfo]:
    modelos: list[ModeloInfo] = []

    for tipo in ("igg", "icp"):
        pasta = modelos_dir / tipo
        if not pasta.is_dir():
            continue

        config_path = pasta / "config.json"
        if not config_path.exists():
            continue

        raw = json.loads(config_path.read_text(encoding="utf-8"))

        sub_modelos = raw.get("modelos", [])

        if sub_modelos:
            all_classes = {}
            for sm in sub_modelos:
                sm.setdefault("janela", [1024, 1024])
                sm.setdefault("stride", [512, 512])
                for cid, cname in sm.get("classes", {}).items():
                    all_classes[int(cid)] = cname

            raw.setdefault("arquivo", "")
            raw.setdefault("classes", all_classes)
        else:
            raw.setdefault("arquivo", "")
            raw.setdefault("classes", {})

        raw.setdefault("modelos", sub_modelos)
        raw.setdefault("cores", {})
        raw.setdefault("input_shape", [1, 3, 640, 640])
        raw.setdefault("area_minima", {})

        cfg = ModelConfig(**raw)
        modelos.append(ModeloInfo(tipo=tipo, pasta=pasta, config=cfg))

    return modelos
