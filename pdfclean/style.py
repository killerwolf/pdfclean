"""Per-block style detection (bold) so headings/bylines match the source better.

We can't ask Tesseract for reliable font attributes, so we estimate stroke
thickness directly from the ink: a distance transform gives each ink pixel its
distance to the nearest background pixel, i.e. half the local stroke width. Bold
type has thicker strokes relative to its size, so a block whose normalised stroke
radius stands out from the page median is flagged bold.
"""
from __future__ import annotations

import cv2
import numpy as np

from .ocr import OCRResult


def _stroke_ratio(binary: np.ndarray, bbox: tuple[int, int, int, int], line_h: float) -> float:
    x0, y0, x1, y1 = bbox
    crop = binary[y0:y1, x0:x1]
    ink = crop > 0
    if ink.sum() < 50:
        return 0.0
    dist = cv2.distanceTransform((ink * 255).astype(np.uint8), cv2.DIST_L2, 3)
    mean_radius = float(dist[ink].mean())
    return mean_radius / max(1.0, line_h)  # normalise by font size so it's scale-free


def mark_bold_blocks(binary: np.ndarray, ocr: OCRResult, factor: float = 1.3) -> None:
    """Flag blocks whose strokes are noticeably heavier than the page norm."""
    ratios: list[tuple] = []
    for b in ocr.blocks:
        r = _stroke_ratio(binary, b.bbox, b.median_line_height)
        ratios.append((b, r))

    valid = [r for _, r in ratios if r > 0]
    if not valid:
        return
    median = float(np.median(valid))
    for b, r in ratios:
        if r > 0 and r > median * factor:
            b.bold = True
