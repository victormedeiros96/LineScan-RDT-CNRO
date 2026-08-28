from fastapi import APIRouter, UploadFile, File, HTTPException
from fastapi.responses import JSONResponse
import cv2
import numpy as np
from pathlib import Path
import tempfile
import os

from src.services.inference_pipeline import InferencePipeline
from src.services.model_loader import listar_modelos
from src.core.config import get_settings

router = APIRouter(prefix="/debug", tags=["debug"])


@router.post("/inferir-imagem")
async def inferir_imagem(file: UploadFile = File(...)):
    """
    Recebe uma imagem, roda inferência e retorna as detecções.
    """
    settings = get_settings()
    
    # Lê a imagem
    contents = await file.read()
    nparr = np.frombuffer(contents, np.uint8)
    img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    
    if img is None:
        raise HTTPException(status_code=400, detail="Não foi possível decodificar a imagem")
    
    H, W = img.shape[:2]
    
    # Salva temporariamente
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        img_path = tmp_path / "input.png"
        cv2.imwrite(str(img_path), img)
        
        # Carrega modelos
        modelos = listar_modelos(settings.modelos_dir)
        modelo_info = next((m for m in modelos if m.tipo == "igg"), None)
        
        if modelo_info is None:
            raise HTTPException(status_code=500, detail="Modelo IGG não encontrado")
        
        cfg = modelo_info.config
        
        # Cria pipeline
        pipeline = InferencePipeline(
            modelos_dir=modelo_info.pasta,
            input_folder=tmp_path,
            output_folder=tmp_path,
            sub_modelos=[m.model_dump() for m in cfg.modelos] if cfg.modelos else [],
            area_minima=cfg.area_minima,
        )
        
        # Carrega modelos
        pipeline._load_models()
        
        # Processa a imagem
        img_bgr = cv2.imread(str(img_path))
        
        # Downscale para inferência (se necessário)
        from src.services.inference_pipeline import IMAGE_WIDTH_PX, IMAGE_HEIGHT_INFERENCE, IMAGE_HEIGHT_DISPLAY
        img_inference = cv2.resize(img_bgr, (IMAGE_WIDTH_PX, IMAGE_HEIGHT_INFERENCE), interpolation=cv2.INTER_LINEAR)
        
        # Roda inferência
        all_masks = {}
        all_scores = {}
        model_y_scales = {}
        
        for sm in pipeline.sub_modelos:
            nome = sm["nome"]
            if nome not in pipeline.models:
                continue
            
            classes = sm["classes"]
            janela = sm.get("janela", [1024, 1024])
            stride = sm.get("stride", [512, 512])
            
            if nome == "panelas":
                model_image = img_bgr
                y_scale = 1.0
            else:
                model_image = img_inference
                y_scale = 2.0

            H_MODEL, W_MODEL = model_image.shape[:2]
            global_masks = {int(k): np.zeros((H_MODEL, W_MODEL), dtype=np.uint8) for k in classes}
            score_maps = {int(k): np.zeros((H_MODEL, W_MODEL), dtype=np.float32) for k in classes}
            
            pipeline._run_model_on_image(
                model=pipeline.models[nome],
                model_name=nome,
                crop_image=model_image,
                offset_y=0,
                janela=janela,
                stride=stride,
                global_masks=global_masks,
                score_maps=score_maps,
            )
            
            all_masks[nome] = global_masks
            all_scores[nome] = score_maps
            model_y_scales[nome] = y_scale
        
        # Extrai detecções
        detections = []
        for sm in pipeline.sub_modelos:
            nome = sm["nome"]
            if nome not in all_masks:
                continue
            
            classes = sm["classes"]
            for cls_id_str, class_name in classes.items():
                cls_id = int(cls_id_str)
                mask = all_masks[nome].get(cls_id)
                if mask is not None and mask.any():
                    score = all_scores[nome].get(cls_id)
                    detections.extend(pipeline._extract_polygons(
                        mask,
                        class_name,
                        score,
                        "debug",
                        y_scale=model_y_scales.get(nome, 2.0),
                    ))
        detections = pipeline._postprocess_trinca_jacare(detections)
    
    return JSONResponse({
        "width": W,
        "height": H,
        "detections": detections,
        "total": len(detections),
    })


@router.post("/inferir-imagem-pt")
async def inferir_imagem_pt(file: UploadFile = File(...)):
    """Recebe uma imagem e roda inferência usando os modelos .pt via Ultralytics."""
    from ultralytics import YOLO
    from src.services.inference_pipeline import IMAGE_WIDTH_PX, IMAGE_HEIGHT_INFERENCE, Y_SCALE

    settings = get_settings()
    contents = await file.read()
    nparr = np.frombuffer(contents, np.uint8)
    img_bgr = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    if img_bgr is None:
        raise HTTPException(status_code=400, detail="Não foi possível decodificar a imagem")

    h_display, w_display = img_bgr.shape[:2]
    modelos_dir = settings.modelos_dir / "igg"
    pt_models = {
        "trincas": {
            "path": modelos_dir / "trincas.pt",
            "classes": {0: "fc3", 1: "couro_jacare"},
            "image": cv2.resize(img_bgr, (IMAGE_WIDTH_PX, IMAGE_HEIGHT_INFERENCE), interpolation=cv2.INTER_LINEAR),
            "janela": [1024, 1024],
            "stride": [512, 512],
            "y_scale": Y_SCALE,
        },
        "panelas": {
            "path": modelos_dir / "panela_remendo.pt",
            "classes": {0: "panela", 1: "remendo"},
            "image": img_bgr,
            "janela": [4096, 4096],
            "stride": [4096, 1024],
            "y_scale": 1.0,
        },
    }

    for info in pt_models.values():
        if not info["path"].exists():
            raise HTTPException(status_code=500, detail=f"Modelo .pt não encontrado: {info['path']}")

    pipeline = InferencePipeline(
        modelos_dir=modelos_dir,
        input_folder=Path("/tmp"),
        output_folder=Path("/tmp/debug_pt"),
        sub_modelos=[],
        area_minima={"fc3": 500, "couro_jacare": 500, "panela": 200, "remendo": 500},
    )

    all_detections = []
    for model_name, info in pt_models.items():
        model = YOLO(str(info["path"]))
        model_image = info["image"]
        h_model, w_model = model_image.shape[:2]
        classes = info["classes"]
        global_masks = {cls_id: np.zeros((h_model, w_model), dtype=np.uint8) for cls_id in classes}
        score_maps = {cls_id: np.zeros((h_model, w_model), dtype=np.float32) for cls_id in classes}
        janela_w, janela_h = info["janela"]
        stride_x, stride_y = info["stride"]

        for y in range(0, h_model, stride_y):
            for x in range(0, w_model, stride_x):
                y_end = min(y + janela_h, h_model)
                x_end = min(x + janela_w, w_model)
                slice_img = model_image[y:y_end, x:x_end]
                if slice_img.size == 0:
                    continue

                result = model.predict(slice_img, imgsz=1024, conf=0.25, iou=0.45, verbose=False)[0]
                if result.masks is None or result.boxes is None:
                    continue

                masks = result.masks.data.cpu().numpy()
                cls_values = result.boxes.cls.cpu().numpy().astype(int)
                conf_values = result.boxes.conf.cpu().numpy()
                slice_h, slice_w = slice_img.shape[:2]

                for mask, cls_id, conf_val in zip(masks, cls_values, conf_values):
                    if cls_id not in global_masks:
                        continue
                    mask_np = (mask > 0).astype(np.uint8) * 255
                    if mask_np.shape[:2] != (slice_h, slice_w):
                        mask_np = cv2.resize(mask_np, (slice_w, slice_h), interpolation=cv2.INTER_NEAREST)
                    global_masks[cls_id][y:y_end, x:x_end] = np.maximum(
                        global_masks[cls_id][y:y_end, x:x_end],
                        mask_np[: y_end - y, : x_end - x],
                    )
                    roi = score_maps[cls_id][y:y_end, x:x_end]
                    mask_bool = mask_np[: y_end - y, : x_end - x] > 0
                    roi[mask_bool] = np.maximum(roi[mask_bool], float(conf_val))

        for cls_id, class_name in classes.items():
            mask = global_masks[cls_id]
            if mask.any():
                all_detections.extend(
                    pipeline._extract_polygons(
                        mask,
                        class_name,
                        score_maps[cls_id],
                        image_stem=f"debug_pt_{model_name}",
                        y_scale=info["y_scale"],
                    )
                )

    all_detections = pipeline._postprocess_trinca_jacare(all_detections)

    return JSONResponse({
        "width": w_display,
        "height": h_display,
        "detections": all_detections,
        "total": len(all_detections),
    })
