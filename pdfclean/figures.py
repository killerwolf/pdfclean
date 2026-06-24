"""Detect non-text figure regions (illustrations, photos, logos).

Heuristic: ink that Tesseract did *not* claim as words is a figure candidate.
We mask out all OCR word boxes (padded), keep the remaining ink, dilate it into
blobs, and keep blobs that are large and dense enough to be a real picture
rather than stray speckle or punctuation Tesseract missed.
"""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from .ocr import Block, OCRResult, Paragraph


@dataclass
class Figure:
    x: int
    y: int
    w: int
    h: int


def detect_figures(
    binary: np.ndarray,
    ocr: OCRResult,
    min_area_frac: float = 0.012,
    max_area_frac: float = 0.85,
) -> list[Figure]:
    """Find figure bounding boxes in the ink mask, excluding OCR'd text."""
    H, W = binary.shape
    page_area = H * W

    # mask out text (padded a little so glyph edges don't leak through)
    text_mask = np.zeros((H, W), np.uint8)
    pad = max(2, int(0.004 * max(H, W)))
    for word in ocr.words:
        x0 = max(0, word.x - pad)
        y0 = max(0, word.y - pad)
        x1 = min(W, word.right + pad)
        y1 = min(H, word.bottom + pad)
        text_mask[y0:y1, x0:x1] = 255

    non_text = cv2.bitwise_and(binary, cv2.bitwise_not(text_mask))

    # join nearby strokes of a drawing into one blob
    k = max(5, int(0.012 * max(H, W)))
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))
    blobs = cv2.morphologyEx(non_text, cv2.MORPH_CLOSE, kernel)
    blobs = cv2.dilate(blobs, kernel)

    contours, _ = cv2.findContours(blobs, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    figures: list[Figure] = []
    for c in contours:
        x, y, w, h = cv2.boundingRect(c)
        area = w * h
        if area < min_area_frac * page_area or area > max_area_frac * page_area:
            continue
        # reject slivers: column rules / page edges are long and razor-thin
        aspect = max(w, h) / max(1, min(w, h))
        if aspect > 10:
            continue
        # density of ink inside the box — rules out big sparse boxes that are
        # really just whitespace spanning a gap between text columns
        roi = non_text[y : y + h, x : x + w]
        if roi.size == 0 or (roi > 0).mean() < 0.02:
            continue
        figures.append(Figure(x=x, y=y, w=w, h=h))

    return _merge_overlapping(figures)


def split_blocks_around_figures(ocr: OCRResult, figures: list[Figure]) -> None:
    """Split text blocks that a figure cuts through, so reflow won't run over it.

    A block whose bounding box straddles a figure (text above/below *and* beside
    it) gets sliced into y-bands at the figure's top and bottom edges. The
    beside-figure band keeps its naturally narrow width (the scan's words already
    stop at the figure), so when it is re-flowed it no longer stretches across
    the illustration. Runs before vision/bold so everything downstream sees the
    split blocks. Mutates ``ocr.blocks`` in place.
    """
    figs = [(f.x, f.y, f.x + f.w, f.y + f.h) for f in figures]
    out: list[Block] = []
    for b in ocr.blocks:
        fig = _splitting_figure(b, figs)
        if fig is None:
            out.append(b)
            continue
        _, fy0, _, fy1 = fig
        bands: dict[str, list] = {"top": [], "mid": [], "bot": []}
        for para in b.paragraphs:
            for line in para.lines:
                cy = (line.bbox[1] + line.bbox[3]) / 2
                key = "top" if cy < fy0 else ("bot" if cy > fy1 else "mid")
                bands[key].append((id(para), line))

        subs: list[Block] = []
        for key in ("top", "mid", "bot"):
            items = bands[key]
            if not items:
                continue
            sub = Block(bold=b.bold, italic=b.italic)
            cur_pid, cur_lines = None, []
            for pid, line in items:  # keep original paragraph boundaries
                if pid != cur_pid:
                    if cur_lines:
                        sub.paragraphs.append(Paragraph(cur_lines))
                        cur_lines = []
                    cur_pid = pid
                cur_lines.append(line)
            if cur_lines:
                sub.paragraphs.append(Paragraph(cur_lines))
            subs.append(sub)
        out.extend(subs or [b])
    ocr.blocks = out


def _splitting_figure(block: Block, figs: list[tuple[int, int, int, int]]):
    """A figure that cuts through ``block`` and whose mid-band text avoids it."""
    bx0, by0, bx1, by1 = block.bbox
    for fx0, fy0, fx1, fy1 in figs:
        if bx1 <= fx0 or bx0 >= fx1 or by1 <= fy0 or by0 >= fy1:
            continue  # no overlap
        if not (by0 < fy0 - 2 or by1 > fy1 + 2):
            continue  # block doesn't extend past the figure -> nothing to peel off
        mids = [
            ln for p in block.paragraphs for ln in p.lines
            if fy0 <= (ln.bbox[1] + ln.bbox[3]) / 2 <= fy1
        ]
        if not mids:
            continue
        # the beside-figure lines must sit cleanly on one side of the figure
        if max(ln.bbox[2] for ln in mids) <= fx0 + 2 or min(ln.bbox[0] for ln in mids) >= fx1 - 2:
            return (fx0, fy0, fx1, fy1)
    return None


def _merge_overlapping(figs: list[Figure]) -> list[Figure]:
    """Merge boxes that overlap so a single drawing isn't split in two."""
    boxes = [[f.x, f.y, f.x + f.w, f.y + f.h] for f in figs]
    merged = True
    while merged:
        merged = False
        out: list[list[int]] = []
        for b in boxes:
            placed = False
            for o in out:
                if not (b[2] < o[0] or b[0] > o[2] or b[3] < o[1] or b[1] > o[3]):
                    o[0], o[1] = min(o[0], b[0]), min(o[1], b[1])
                    o[2], o[3] = max(o[2], b[2]), max(o[3], b[3])
                    placed = True
                    merged = True
                    break
            if not placed:
                out.append(b[:])
        boxes = out
    return [Figure(x=b[0], y=b[1], w=b[2] - b[0], h=b[3] - b[1]) for b in boxes]
