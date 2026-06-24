"""Image cleaning: deskew, background whitening, denoise.

Everything here works on numpy arrays (OpenCV convention, uint8). The goal is
twofold: produce a clean grayscale that OCRs well, and produce a deskewed colour
image that figure crops can be lifted from. Both share the same rotation so the
OCR coordinates and the figure crops live in the same frame.
"""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np


@dataclass
class CleanedPage:
    """Result of cleaning a single scanned page."""

    gray: np.ndarray   # cleaned grayscale, for OCR (uint8, HxW)
    color: np.ndarray  # deskewed colour image, for figure crops (uint8, HxWx3 BGR)
    binary: np.ndarray  # ink mask, ink=255 (uint8, HxW) — used for figure detection
    angle: float       # skew angle removed, degrees


def _to_gray(img: np.ndarray) -> np.ndarray:
    if img.ndim == 2:
        return img
    if img.shape[2] == 4:
        img = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
    return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)


def _to_color(img: np.ndarray) -> np.ndarray:
    if img.ndim == 2:
        return cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    if img.shape[2] == 4:
        return cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
    return img


def estimate_skew(gray: np.ndarray, max_angle: float = 8.0, step: float = 0.2) -> float:
    """Estimate page skew via a projection-profile search.

    Rotating text so lines are horizontal maximises the variance of the
    row-sum profile (sharp peaks at text lines, troughs between). We search a
    small angle window on a downscaled binary for speed.
    """
    h, w = gray.shape
    scale = 800.0 / max(h, w)
    small = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA) if scale < 1 else gray
    binary = cv2.threshold(small, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)[1]

    best_angle, best_score = 0.0, -1.0
    angles = np.arange(-max_angle, max_angle + step, step)
    sh, sw = binary.shape
    center = (sw / 2, sh / 2)
    for angle in angles:
        m = cv2.getRotationMatrix2D(center, angle, 1.0)
        rot = cv2.warpAffine(binary, m, (sw, sh), flags=cv2.INTER_NEAREST, borderValue=0)
        profile = rot.sum(axis=1, dtype=np.float64)
        score = float(np.var(profile))
        if score > best_score:
            best_score, best_angle = score, float(angle)
    return best_angle


def _rotate(img: np.ndarray, angle: float, border_value) -> np.ndarray:
    if abs(angle) < 0.05:
        return img
    h, w = img.shape[:2]
    m = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    return cv2.warpAffine(
        img, m, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_CONSTANT, borderValue=border_value
    )


def _whiten_background(gray: np.ndarray) -> np.ndarray:
    """Flatten uneven lighting / grey cast so paper goes truly white.

    Estimate the local background by heavily blurring a dilated copy (which
    erases the thin dark text), then keep only how much darker each pixel is
    than its background. Result: crisp dark ink on a clean white field.
    """
    dilated = cv2.dilate(gray, np.ones((7, 7), np.uint8))
    bg = cv2.medianBlur(dilated, 21)
    diff = 255 - cv2.absdiff(gray, bg)
    norm = cv2.normalize(diff, None, 0, 255, cv2.NORM_MINMAX)
    return norm


def clean_figure_crop(bgr: np.ndarray) -> np.ndarray:
    """Clean a cropped figure (line art / illustration) to crisp grayscale on white.

    Whitens the grey scan cast, then knocks out the speckle/stipple scan noise
    with edge-preserving non-local-means denoising, and finally lifts the faint
    near-white background to pure white. Result: clean line strokes (and genuine
    shading) on a clean white field, and a tiny JPEG.
    """
    gray = _to_gray(bgr)
    if min(gray.shape[:2]) < 25:  # too small to process meaningfully
        return gray
    gray = _whiten_background(gray)
    # remove speckle/stipple scan noise while keeping ink edges sharp
    gray = cv2.fastNlMeansDenoising(gray, None, h=12, templateWindowSize=7, searchWindowSize=21)
    # snap the faint paper background to true white (kills residual grey haze)
    gray = np.where(gray > 200, 255, gray).astype(np.uint8)
    return gray


def clean_image(img: np.ndarray, deskew: bool = True) -> CleanedPage:
    """Clean one scanned page image (BGR or gray uint8)."""
    gray0 = _to_gray(img)
    color0 = _to_color(img)

    angle = estimate_skew(gray0) if deskew else 0.0
    gray = _rotate(gray0, angle, border_value=255)
    color = _rotate(color0, angle, border_value=(255, 255, 255))

    white = _whiten_background(gray)
    # gentle denoise: kill isolated speckle without eroding glyph strokes
    white = cv2.medianBlur(white, 3)
    # light unsharp to firm up text edges for OCR
    blur = cv2.GaussianBlur(white, (0, 0), 1.0)
    sharp = cv2.addWeighted(white, 1.5, blur, -0.5, 0)

    binary = cv2.adaptiveThreshold(
        sharp, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 31, 15
    )

    return CleanedPage(gray=sharp, color=color, binary=binary, angle=angle)
