from __future__ import annotations

# Largura padrão das imagens concatenadas (pixels)
IMAGE_WIDTH_PX = 4096
# Altura padrão das imagens concatenadas (pixels)
IMAGE_HEIGHT_PX = 10240
# Altura em metros que a imagem representa
IMAGE_HEIGHT_METERS = 20
# Largura em metros que a imagem representa
IMAGE_WIDTH_METERS = 4
# Número de faixas transversais (subdivisões)
TRANSVERSE_QUADRANT_COLS = 3
# Fator de conversão pixels -> m²
PIXEL_AREA_M2 = 0.000002

PIXELS_PER_LONGITUDINAL_METER = IMAGE_HEIGHT_PX / IMAGE_HEIGHT_METERS
PIXELS_PER_TRANSVERSE_SLOT = IMAGE_WIDTH_PX / TRANSVERSE_QUADRANT_COLS


def y_to_longitudinal_line(y_center: float) -> int:
    return int((IMAGE_HEIGHT_PX - 1 - y_center) / PIXELS_PER_LONGITUDINAL_METER)


def area_pixels_to_m2(area_pixels: float | None) -> float:
    try:
        return float(area_pixels or 0) * PIXEL_AREA_M2
    except (TypeError, ValueError):
        return 0.0
