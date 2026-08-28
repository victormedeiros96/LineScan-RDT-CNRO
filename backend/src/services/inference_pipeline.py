from __future__ import annotations

import json
import os
import re
from pathlib import Path

import cv2
import numpy as np
import torch

from src.services.inference_engine import YOLOv8ONNX


# ── Constantes da imagem de 5m ──
IMAGE_WIDTH_PX = 4096
IMAGE_HEIGHT_INFERENCE = 2560    # 5m (resolução de inferência)
IMAGE_HEIGHT_DISPLAY = 5120      # 5m (resolução de display, 1024 px/m)
IMAGE_HEIGHT_METERS = 5
IMAGE_WIDTH_METERS = 4
Y_SCALE = IMAGE_HEIGHT_DISPLAY / IMAGE_HEIGHT_INFERENCE  # = 2.0
PIXELS_PER_METER = IMAGE_WIDTH_PX / IMAGE_WIDTH_METERS   # = 1024 (ambos eixos)
PIXELS_PER_TRANSVERSE_SLOT = IMAGE_WIDTH_PX / 3          # = 1365
PIXEL_AREA_M2 = (IMAGE_WIDTH_METERS / IMAGE_WIDTH_PX) * (IMAGE_HEIGHT_METERS / IMAGE_HEIGHT_DISPLAY)

CLASS_NAME_MAP = {
    "fc3": "Trincas",
    "couro_jacare": "Couro de Jacaré",
    "panela": "Panela",
    "remendo": "Remendo",
}

# Couro de Jacaré com largura OU altura menor que isso é descartado (falso positivo).
# Referencial de display (4096×5120 para 5m).
MIN_COURO_DIM_PX = 400


def y_to_longitudinal_line(y_center: float) -> int:
    """Converte coordenada Y (pixels display) para linha longitudinal (0-4, cada linha = 1m)."""
    return int((IMAGE_HEIGHT_DISPLAY - 1 - y_center) / PIXELS_PER_METER)


def _box_area(box: list[int]) -> float:
    if len(box) < 4:
        return 0.0
    return float(max(0, box[2] - box[0]) * max(0, box[3] - box[1]))


def _boxes_intersect(a: list[int], b: list[int]) -> bool:
    if len(a) < 4 or len(b) < 4:
        return False
    return max(a[0], b[0]) < min(a[2], b[2]) and max(a[1], b[1]) < min(a[3], b[3])


def _polygon_overlap_metrics(a: dict, b: dict) -> tuple[float, float, float]:
    """Retorna (IoU, intersecao/area_a, intersecao/area_b) usando máscaras locais."""
    box_a = a.get("global_box", [])
    box_b = b.get("global_box", [])
    if not _boxes_intersect(box_a, box_b):
        return 0.0, 0.0, 0.0

    x1 = int(max(0, min(box_a[0], box_b[0])))
    y1 = int(max(0, min(box_a[1], box_b[1])))
    x2 = int(min(IMAGE_WIDTH_PX, max(box_a[2], box_b[2])))
    y2 = int(min(IMAGE_HEIGHT_DISPLAY, max(box_a[3], box_b[3])))
    if x2 <= x1 or y2 <= y1:
        return 0.0, 0.0, 0.0

    def draw(det: dict) -> np.ndarray:
        mask = np.zeros((y2 - y1, x2 - x1), dtype=np.uint8)
        polygon = det.get("global_polygon") or []
        if len(polygon) >= 3:
            pts = np.array([[[int(px - x1), int(py - y1)] for px, py in polygon]], dtype=np.int32)
            cv2.fillPoly(mask, pts, 1)
        else:
            bx = det.get("global_box", [])
            if len(bx) >= 4:
                mask[max(0, bx[1] - y1):max(0, bx[3] - y1), max(0, bx[0] - x1):max(0, bx[2] - x1)] = 1
        return mask

    mask_a = draw(a)
    mask_b = draw(b)
    area_a = float(mask_a.sum())
    area_b = float(mask_b.sum())
    if area_a <= 0 or area_b <= 0:
        return 0.0, 0.0, 0.0
    intersection = float(np.logical_and(mask_a, mask_b).sum())
    union = float(np.logical_or(mask_a, mask_b).sum())
    if union <= 0:
        return 0.0, 0.0, 0.0
    return intersection / union, intersection / area_a, intersection / area_b


def _shape_metrics(det: dict) -> tuple[float, float]:
    """Retorna (elongação, preenchimento_bbox) para distinguir trinca isolada de objeto compacto."""
    box = det.get("global_box", [])
    bbox_area = _box_area(box)
    fill_ratio = float(det.get("area_pixels", 0) or 0) / bbox_area if bbox_area > 0 else 1.0
    w = max(1.0, float(box[2] - box[0])) if len(box) >= 4 else 1.0
    h = max(1.0, float(box[3] - box[1])) if len(box) >= 4 else 1.0
    elongation = max(w / h, h / w)

    polygon = det.get("global_polygon") or []
    if len(polygon) >= 5:
        pts = np.array(polygon, dtype=np.float32)
        (_, _), (rw, rh), _ = cv2.minAreaRect(pts)
        if rw > 0 and rh > 0:
            elongation = max(elongation, float(max(rw, rh) / min(rw, rh)))

    return elongation, fill_ratio


def _is_isolated_crack_shape(det: dict) -> bool:
    elongation, fill_ratio = _shape_metrics(det)
    return elongation >= 3.0 or fill_ratio <= 0.28


def _is_strong_isolated_crack_shape(det: dict) -> bool:
    """Versão mais rígida para reclassificar jacaré sem uma trinca concorrente."""
    elongation, fill_ratio = _shape_metrics(det)
    return elongation >= 4.0 or fill_ratio <= 0.18 or (elongation >= 2.6 and fill_ratio <= 0.26)


class InferencePipeline:
    def __init__(
        self,
        modelos_dir: Path,
        input_folder: Path,
        output_folder: Path,
        sub_modelos: list[dict],
        area_minima: dict[str, int],
    ):
        self.modelos_dir = modelos_dir
        self.input_folder = input_folder
        self.output_folder = output_folder
        self.sub_modelos = sub_modelos
        self.area_minima = area_minima
        self.models: dict[str, object] = {}

    def _load_models(self):
        for sm in self.sub_modelos:
            nome = sm["nome"]
            arquivo = sm["arquivo"]
            model_path = self.modelos_dir / arquivo
            if not model_path.exists():
                raise FileNotFoundError(f"Modelo não encontrado: {model_path}")
            if model_path.suffix.lower() == ".pt":
                from ultralytics import YOLO
                self.models[nome] = YOLO(str(model_path))
            else:
                self.models[nome] = YOLOv8ONNX(str(model_path), use_cuda=True)

    def _load_faixas(self) -> list[dict]:
        """Carrega metadados de todas as faixas disponíveis."""
        faixas = []
        meta_files = sorted(self.input_folder.glob("lote_*_meta.json"))
        for meta_path in meta_files:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            for faixa in meta.get("faixas", []):
                faixa["_bloco"] = meta["bloco"]
                faixas.append(faixa)
        return faixas

    def _run_model_on_image(
        self,
        model: YOLOv8ONNX,
        model_name: str,
        crop_image: np.ndarray,
        offset_y: int,
        janela: list[int],
        stride: list[int],
        global_masks: dict[int, np.ndarray],
        score_maps: dict[int, np.ndarray],
    ):
        ch, cw = crop_image.shape[:2]
        janela_w, janela_h = janela
        stride_x, stride_y = stride

        for y in range(0, ch, stride_y):
            for x in range(0, cw, stride_x):
                y_end = min(y + janela_h, ch)
                x_end = min(x + janela_w, cw)
                slice_img = crop_image[y:y_end, x:x_end]
                if slice_img.size == 0:
                    continue

                if model.__class__.__module__.startswith("ultralytics"):
                    result = model.predict(slice_img, imgsz=1024, conf=0.25, iou=0.45, verbose=False)[0]
                    if result.boxes is None or result.masks is None:
                        continue
                    boxes = range(len(result.boxes))
                    classes_iter = result.boxes.cls.cpu().numpy().astype(int)
                    conf_iter = result.boxes.conf.cpu().numpy()
                    masks_iter = result.masks.data.cpu().numpy()
                else:
                    res = model.predict(slice_img)
                    if res.boxes is None or res.masks is None:
                        continue
                    boxes = range(len(res.boxes))
                    classes_iter = [int(res.cls[i]) for i in boxes]
                    conf_iter = [float(res.conf[i]) for i in boxes]
                    masks_iter = [res.masks[i].cpu().numpy() for i in boxes]

                for mask_raw, cls_id, conf_val in zip(masks_iter, classes_iter, conf_iter):
                    if cls_id not in global_masks:
                        continue
                    mask_np = (mask_raw > 0).astype(np.uint8) * 255
                    sh, sw = mask_np.shape[:2]
                    slice_h, slice_w = slice_img.shape[:2]
                    if sh != slice_h or sw != slice_w:
                        mask_np = cv2.resize(mask_np, (slice_w, slice_h), interpolation=cv2.INTER_NEAREST)
                    mask_crop = mask_np[:slice_h, :slice_w]
                    gy_start = offset_y + y
                    gy_end = offset_y + y_end
                    gx_start = x
                    gx_end = x_end
                    global_masks[cls_id][gy_start:gy_end, gx_start:gx_end] = np.maximum(
                        global_masks[cls_id][gy_start:gy_end, gx_start:gx_end], mask_crop
                    )
                    mask_bool = mask_crop > 0
                    roi = score_maps[cls_id][gy_start:gy_end, gx_start:gx_end]
                    roi[mask_bool] = np.maximum(roi[mask_bool], float(conf_val))

    def _extract_polygons(
        self,
        global_mask: np.ndarray,
        class_name: str,
        score_map: np.ndarray | None = None,
        image_stem: str = "debug",
        y_scale: float = Y_SCALE,
    ):
        min_area = self.area_minima.get(class_name, 100)
        debug_dir = self.output_folder / "debug_masks"
        debug_dir.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(debug_dir / f"{image_stem}_{class_name}_mask.png"), global_mask)
        contours, _ = cv2.findContours(global_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        polygons = []
        for contour in contours:
            area = cv2.contourArea(contour)
            if area < min_area:
                continue
            epsilon = 0.005 * cv2.arcLength(contour, True)
            approx = cv2.approxPolyDP(contour, epsilon, True)
            x, y, w, h = cv2.boundingRect(contour)
            # Escala Y para resolução de display
            global_box = [int(x), int(y * y_scale), int(x + w), int((y + h) * y_scale)]
            global_polygon = [[pt[0], pt[1] * y_scale] for pt in approx.reshape(-1, 2).tolist()]
            confidence = 1.0
            confidence_max = 1.0
            if score_map is not None:
                contour_mask = np.zeros(global_mask.shape, dtype=np.uint8)
                cv2.drawContours(contour_mask, [contour], -1, 255, thickness=cv2.FILLED)
                scores = score_map[contour_mask > 0]
                scores = scores[scores > 0]
                if scores.size:
                    confidence = float(np.mean(scores))
                    confidence_max = float(np.max(scores))
            x_center = x + w / 2
            y_center_display = (y + h / 2) * y_scale
            linha = y_to_longitudinal_line(y_center_display)
            coluna = int(x_center / PIXELS_PER_TRANSVERSE_SLOT)
            linha = max(0, min(4, linha))      # 0-4 (5 metros = 5 linhas de 1m)
            coluna = max(0, min(2, coluna))    # 0-2 (3 sub-colunas)
            display_name = CLASS_NAME_MAP.get(class_name, class_name)
            area_display = area * y_scale  # escala da área para display
            polygons.append({
                "classe": display_name,
                "area": int(area_display),
                "area_pixels": int(area_display),
                "area_m2": round(float(area_display) * PIXEL_AREA_M2, 6),
                "confidence": round(confidence, 4),
                "score": round(confidence, 4),
                "confidence_max": round(confidence_max, 4),
                "linha": linha,
                "coluna": coluna,
                "global_box": global_box,
                "global_polygon": global_polygon,
                "direction": "",
            })
        return polygons

    def _filtrar_couro_pequeno(self, detections: list[dict]) -> list[dict]:
        """Remove Couro de Jacaré muito pequeno (falso positivo provável).

        Um couro de jacaré real nunca teria largura OU altura menor que o
        mínimo configurado (padrão 400 px no referencial de display).
        """
        resultado: list[dict] = []
        for det in detections:
            if det.get("classe") == "Couro de Jacaré":
                box = det.get("global_box") or []
                if len(box) == 4:
                    largura = box[2] - box[0]
                    altura = box[3] - box[1]
                    if largura < MIN_COURO_DIM_PX or altura < MIN_COURO_DIM_PX:
                        continue
            resultado.append(det)
        return resultado

    def _postprocess_trinca_jacare(self, detections: list[dict]) -> list[dict]:
        """
        Resolve conflito entre Trincas e Couro de Jacaré.

        Regra geral: trinca dentro de couro é absorvida pelo couro. Exceção:
        quando ambos representam praticamente o mesmo objeto e a geometria é
        claramente de trinca isolada, mantém Trincas e remove Couro de Jacaré.

        Também corrige Couro de Jacaré isolado quando sua geometria é muito
        mais compatível com trinca isolada do que com um padrão interligado.
        """
        trincas = [i for i, d in enumerate(detections) if d.get("classe") == "Trincas"]
        jacares = [i for i, d in enumerate(detections) if d.get("classe") == "Couro de Jacaré"]
        if not jacares:
            return detections

        remove: set[int] = set()
        if trincas:
            for ti in trincas:
                if ti in remove:
                    continue
                trinca = detections[ti]
                for ji in jacares:
                    if ji in remove:
                        continue
                    jacare = detections[ji]
                    iou, trinca_overlap, jacare_overlap = _polygon_overlap_metrics(trinca, jacare)
                    if iou <= 0 and trinca_overlap <= 0:
                        continue

                    nearly_same_object = iou >= 0.72 or (trinca_overlap >= 0.82 and jacare_overlap >= 0.65)
                    if nearly_same_object and _is_isolated_crack_shape(trinca):
                        remove.add(ji)
                        continue

                    trinca_inside_jacare = trinca_overlap >= 0.55 or (trinca_overlap >= 0.35 and iou >= 0.18)
                    if trinca_inside_jacare:
                        remove.add(ti)
                        break

        corrected = []
        for i, det in enumerate(detections):
            if i in remove:
                continue
            if det.get("classe") == "Couro de Jacaré" and _is_strong_isolated_crack_shape(det):
                det = {**det, "classe": "Trincas"}
            corrected.append(det)
        return corrected

    def _save_json(self, results: dict):
        output_path = self.output_folder / "analise_completa.json"
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)

    def run(
        self,
        tipo_pista: str = "simples",
        sentido: str = "crescente",
        faixa: int | None = None,
        progress_callback=None,
    ):
        self._load_models()

        # Carrega faixas de 5m a partir dos metadados
        faixas = self._load_faixas()
        if not faixas:
            raise ValueError("Nenhuma faixa encontrada para análise")

        output_path = self.output_folder / "analise_completa.json"
        all_results: dict = {}
        if output_path.exists():
            try:
                all_results = json.loads(output_path.read_text(encoding="utf-8"))
            except Exception:
                all_results = {}

        total = len(faixas)
        for idx, faixa_meta in enumerate(faixas):
            arquivo_faixa = faixa_meta["arquivo"]
            bloco = faixa_meta["_bloco"]

            if progress_callback:
                km_info = f"KM {faixa_meta.get('km_inicio', '?'):.3f}"
                progress_callback(idx + 1, total, f"Analisando {arquivo_faixa} ({km_info})")

            # Pula se já foi analisado
            if arquivo_faixa in all_results:
                continue

            # Lê a imagem de 5m
            faixa_path = self.input_folder / arquivo_faixa
            if not faixa_path.is_file():
                continue

            faixa_image = cv2.imread(str(faixa_path))
            if faixa_image is None:
                continue

            H, W = faixa_image.shape[:2]

            # Downscale para resolução de inferência (4096×2560)
            faixa_inference = cv2.resize(faixa_image, (IMAGE_WIDTH_PX, IMAGE_HEIGHT_INFERENCE), interpolation=cv2.INTER_LINEAR)
            H_INF, W_INF = faixa_inference.shape[:2]

            all_masks: dict[str, dict[int, np.ndarray]] = {}
            all_scores: dict[str, dict[int, np.ndarray]] = {}
            model_y_scales: dict[str, float] = {}

            for sm in self.sub_modelos:
                nome = sm["nome"]
                if nome not in self.models:
                    continue
                classes = sm["classes"]
                janela = sm.get("janela", [1024, 1024])
                stride = sm.get("stride", [512, 512])

                if nome == "panelas":
                    model_image = faixa_image
                    y_scale = 1.0
                else:
                    model_image = faixa_inference
                    y_scale = Y_SCALE

                H_MODEL, W_MODEL = model_image.shape[:2]
                global_masks = {int(k): np.zeros((H_MODEL, W_MODEL), dtype=np.uint8) for k in classes}
                score_maps = {int(k): np.zeros((H_MODEL, W_MODEL), dtype=np.float32) for k in classes}

                self._run_model_on_image(
                    model=self.models[nome],
                    model_name=nome,
                    crop_image=model_image,
                    offset_y=0,  # sem offset, imagem já é 5m
                    janela=janela,
                    stride=stride,
                    global_masks=global_masks,
                    score_maps=score_maps,
                )

                all_masks[nome] = global_masks
                all_scores[nome] = score_maps
                model_y_scales[nome] = y_scale

            detections = []
            image_stem = arquivo_faixa.rsplit(".", 1)[0]
            for sm in self.sub_modelos:
                nome = sm["nome"]
                if nome not in all_masks:
                    continue
                classes = sm["classes"]
                for cls_id_str, class_name in classes.items():
                    cls_id = int(cls_id_str)
                    mask = all_masks[nome].get(cls_id)
                    if mask is not None and mask.any():
                        score = all_scores[nome].get(cls_id)
                        detections.extend(self._extract_polygons(
                            mask,
                            class_name,
                            score,
                            image_stem,
                            y_scale=model_y_scales.get(nome, Y_SCALE),
                        ))

            detections = self._postprocess_trinca_jacare(detections)
            detections = self._filtrar_couro_pequeno(detections)

            # Salva resultado com metadados da faixa
            all_results[arquivo_faixa] = {
                "deteccoes": detections,
                "faixa_meta": {
                    "arquivo": arquivo_faixa,
                    "bloco_indice": bloco["indice"],
                    "km_inicio": faixa_meta.get("km_inicio"),
                    "km_fim": faixa_meta.get("km_fim"),
                    "altura_px": faixa_meta.get("altura_px"),
                },
            }
            self._save_json(all_results)

        return all_results
