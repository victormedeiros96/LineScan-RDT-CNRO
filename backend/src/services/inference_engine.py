from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import onnxruntime as ort
import torch


class LetterBox:
    def __init__(self, new_shape=(640, 640), auto=False, scaleFill=False, scaleup=True, center=True, stride=32):
        self.new_shape = new_shape
        self.auto = auto
        self.scaleFill = scaleFill
        self.scaleup = scaleup
        self.stride = stride
        self.center = center

    def __call__(self, img: np.ndarray) -> tuple[np.ndarray, tuple[float, float], tuple[int, int]]:
        shape = img.shape[:2]
        if isinstance(self.new_shape, int):
            self.new_shape = (self.new_shape, self.new_shape)
        r = min(self.new_shape[0] / shape[0], self.new_shape[1] / shape[1])
        if not self.scaleup:
            r = min(r, 1.0)
        ratio = r, r
        new_unpad = int(round(shape[1] * r)), int(round(shape[0] * r))
        dw, dh = self.new_shape[1] - new_unpad[0], self.new_shape[0] - new_unpad[1]
        if self.auto:
            dw, dh = np.mod(dw, self.stride), np.mod(dh, self.stride)
        elif self.scaleFill:
            dw, dh = 0.0, 0.0
            new_unpad = (self.new_shape[1], self.new_shape[0])
            ratio = self.new_shape[1] / shape[1], self.new_shape[0] / shape[0]
        dw /= 2
        dh /= 2
        if shape[::-1] != new_unpad:
            img = cv2.resize(img, new_unpad, interpolation=cv2.INTER_LINEAR)
        top, bottom = int(round(dh - 0.1)), int(round(dh + 0.1))
        left, right = int(round(dw - 0.1)), int(round(dw + 0.1))
        img = cv2.copyMakeBorder(img, top, bottom, left, right, cv2.BORDER_CONSTANT, value=(114, 114, 114))
        return img, ratio, (dw, dh)


class YOLOv8ONNX:
    def __init__(self, model_path: str | Path, use_cuda: bool = True):
        providers = ["CUDAExecutionProvider", "CPUExecutionProvider"] if use_cuda else ["CPUExecutionProvider"]
        self.session = ort.InferenceSession(str(model_path), providers=providers)
        self.input_name = self.session.get_inputs()[0].name
        raw_shape = self.session.get_inputs()[0].shape
        # Os modelos antigos foram exportados/rodados com entrada 1024x1024.
        self.input_shape = [
            d if isinstance(d, int) else 1024 for d in raw_shape
        ]
        self.num_outputs = len(self.session.get_outputs())

    def predict(self, image: np.ndarray, conf_threshold: float = 0.25, iou_threshold: float = 0.45):
        orig_shape = image.shape[:2]
        letterbox = LetterBox(new_shape=(self.input_shape[2], self.input_shape[3]))
        img, ratio, (dw, dh) = letterbox(image)
        img = img.astype(np.float32) / 255.0
        img = img.transpose(2, 0, 1)[np.newaxis, ...]

        outputs = self.session.run(None, {self.input_name: img})

        pred = torch.from_numpy(outputs[0])
        # Output do ONNX: (batch, num_detections, features) = (1, 300, 38)
        # features = 4(box) + 1(obj) + nc(classes) + nm(masks)
        last_dim = pred.shape[-1]
        is_seg = last_dim > 7  # 4 + 1 + 2 = 7 para detecção pura
        if is_seg:
            proto = outputs[1] if len(outputs) > 1 else None
            pred = self._non_max_suppression(pred, conf_threshold, iou_threshold, nm=32)
            result = YOLOResult(pred, orig_shape, ratio, (dw, dh), input_shape=(img.shape[2], img.shape[3]), proto=proto)
        else:
            pred = self._non_max_suppression(pred, conf_threshold, iou_threshold)
            result = YOLOResult(pred, orig_shape, ratio, (dw, dh), input_shape=(img.shape[2], img.shape[3]))

        return result

    def _non_max_suppression(
        self, prediction, conf_thres=0.25, iou_thres=0.45, max_det=300, nc=0, nm=0, max_wh=7680,
    ):
        import torchvision

        if isinstance(prediction, (list, tuple)):
            prediction = prediction[0]

        # prediction shape: (batch, num_detections, features)
        bs = prediction.shape[0]
        nc = nc or (prediction.shape[-1] - 4 - nm)
        mi = 4 + nc
        xc = prediction[:, :, 4:mi].amax(2) > conf_thres

        output = [torch.zeros((0, 6 + nm), device=prediction.device)] * bs

        for xi, x in enumerate(prediction):
            x = x[xc[xi]]
            if not x.shape[0]:
                continue

            box = self._xywh2xyxy(x[:, :4])
            conf, j = x[:, 4:mi].max(1, keepdim=True)
            x = torch.cat((box, conf, j.float(), x[:, mi:]), 1)[conf.view(-1) > conf_thres]

            n = x.shape[0]
            if not n:
                continue

            x = x[x[:, 4].argsort(descending=True)[:max_det]]
            c = x[:, 5:6] * max_wh
            boxes, scores = x[:, :4] + c, x[:, 4]
            i = torchvision.ops.nms(boxes, scores, iou_thres)
            i = i[:max_det]
            output[xi] = x[i]

        return output

    @staticmethod
    def _xywh2xyxy(x):
        y = x.clone() if isinstance(x, torch.Tensor) else np.copy(x)
        y[..., 0] = x[..., 0] - x[..., 2] / 2
        y[..., 1] = x[..., 1] - x[..., 3] / 2
        y[..., 2] = x[..., 0] + x[..., 2] / 2
        y[..., 3] = x[..., 1] + x[..., 3] / 2
        return y


class YOLOResult:
    def __init__(self, pred, orig_shape, ratio, pad, input_shape, proto=None):
        self.pred = pred
        self.orig_shape = orig_shape
        self.ratio = ratio
        self.pad = pad
        self.input_shape = input_shape
        self.proto = proto
        self._boxes = None
        self._masks = None
        self._cls = None
        self._conf = None

    @property
    def boxes(self):
        if self._boxes is None and len(self.pred[0]):
            self._boxes = self.pred[0][:, :4]
        return self._boxes

    @property
    def masks(self):
        if self._masks is not None:
            return self._masks
        if self.proto is None or not len(self.pred[0]):
            return None
        import torch

        dets = self.pred[0]
        coeffs = dets[:, 6:]
        boxes = dets[:, :4]
        proto = torch.from_numpy(self.proto) if isinstance(self.proto, np.ndarray) else self.proto
        if proto.dim() == 3:
            proto = proto.unsqueeze(0)

        c, mh, mw = proto.shape[1], proto.shape[2], proto.shape[3]
        masks = (coeffs @ proto.float().view(c, -1)).view(-1, mh, mw)

        h, w = self.orig_shape
        input_h, input_w = self.input_shape
        width_ratio = mw / input_w if input_w else 1
        height_ratio = mh / input_h if input_h else 1

        downsampled_bboxes = boxes.clone()
        downsampled_bboxes[:, 0] *= width_ratio
        downsampled_bboxes[:, 2] *= width_ratio
        downsampled_bboxes[:, 1] *= height_ratio
        downsampled_bboxes[:, 3] *= height_ratio

        masks = self._crop_mask(masks, downsampled_bboxes)

        import torch.nn.functional as F
        masks = F.interpolate(masks.unsqueeze(0), (input_h, input_w), mode="bilinear", align_corners=False)[0]

        pad_w, pad_h = self.pad
        top = int(round(pad_h - 0.1))
        left = int(round(pad_w - 0.1))
        unpad_h = max(1, int(round(h * self.ratio[0])))
        unpad_w = max(1, int(round(w * self.ratio[1])))
        masks = masks[:, top:top + unpad_h, left:left + unpad_w]
        masks = F.interpolate(masks.unsqueeze(0), (h, w), mode="nearest")[0]
        self._masks = masks.gt_(0.0)

        return self._masks

    @staticmethod
    def _crop_mask(masks, boxes):
        import torch
        _, h, w = masks.shape
        x1, y1, x2, y2 = torch.chunk(boxes[:, :, None], 4, 1)
        r = torch.arange(w, device=masks.device, dtype=x1.dtype)[None, None, :]
        c = torch.arange(h, device=masks.device, dtype=x1.dtype)[None, :, None]
        return masks * ((r >= x1) * (r < x2) * (c >= y1) * (c < y2))

    @property
    def cls(self):
        if self._cls is None and len(self.pred[0]):
            self._cls = self.pred[0][:, 5]
        return self._cls

    @property
    def conf(self):
        if self._conf is None and len(self.pred[0]):
            self._conf = self.pred[0][:, 4]
        return self._conf
