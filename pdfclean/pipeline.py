"""Per-document orchestration: scan PDF in, born-digital PDF out."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import cv2
import fitz
import numpy as np

from .clean import clean_image
from .figures import detect_figures, split_blocks_around_figures
from .ocr import dehyphenate, run_ocr
from .reconstruct import add_page
from .style import mark_bold_blocks
from .vision import VisionConfig, refine_blocks


@dataclass
class PageStat:
    index: int
    words: int
    figures: int
    mean_conf: float
    skew: float


@dataclass
class DocResult:
    src: Path
    dst: Path
    pages: list[PageStat]

    @property
    def total_words(self) -> int:
        return sum(p.words for p in self.pages)

    @property
    def mean_conf(self) -> float:
        vals = [p.mean_conf for p in self.pages if p.words]
        return float(np.mean(vals)) if vals else 0.0


def _largest_image_xref(page: fitz.Page) -> int | None:
    """Return the xref of the image that covers the most of the page."""
    best, best_area = None, 0.0
    for img in page.get_images(full=True):
        xref = img[0]
        try:
            rects = page.get_image_rects(xref)
        except Exception:
            rects = []
        area = max((r.width * r.height for r in rects), default=0.0)
        if area > best_area:
            best, best_area = xref, area
    return best


def _page_to_bgr(doc: fitz.Document, page: fitz.Page, fallback_dpi: int = 220) -> np.ndarray:
    """Pull the page's scan as a BGR numpy array.

    Prefer the embedded full-page image at native resolution; if the page has no
    usable image, rasterise the page itself as a fallback.
    """
    xref = _largest_image_xref(page)
    if xref is not None:
        data = doc.extract_image(xref)["image"]
        arr = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
        if arr is not None:
            return arr
    pix = page.get_pixmap(dpi=fallback_dpi)
    arr = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, pix.n)
    if pix.n == 4:
        return cv2.cvtColor(arr, cv2.COLOR_RGBA2BGR)
    if pix.n == 1:
        return cv2.cvtColor(arr, cv2.COLOR_GRAY2BGR)
    return cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)


def process_pdf(
    src: Path,
    dst: Path,
    *,
    lang: str = "eng",
    min_conf: float = 40.0,
    deskew: bool = True,
    figures: bool = True,
    psm: int = 3,
    engine: str = "tesseract",
    vision_cfg: VisionConfig | None = None,
    max_pages: int = 0,
    on_page=None,
) -> DocResult:
    """Convert one scanned PDF at ``src`` into a born-digital PDF at ``dst``.

    ``engine="vision"`` additionally sends each page to a hosted vision model
    (see vision.py) to correct the OCR text and detect italics, keeping the
    Tesseract-derived layout.
    """
    doc = fitz.open(src)
    out = fitz.open()
    stats: list[PageStat] = []
    use_vision = engine == "vision"
    vcfg = vision_cfg or VisionConfig()

    n_pages = doc.page_count if max_pages <= 0 else min(max_pages, doc.page_count)
    for i in range(n_pages):
        page = doc[i]
        bgr = _page_to_bgr(doc, page)
        cleaned = clean_image(bgr, deskew=deskew)
        ocr = run_ocr(cleaned.gray, lang=lang, min_conf=min_conf, psm=psm)
        dehyphenate(ocr)  # rejoin line-break-hyphenated words before reflow
        figs = detect_figures(cleaned.binary, ocr) if figures else []
        if figs:
            # peel apart blocks a figure cuts through so text won't reflow over it
            split_blocks_around_figures(ocr, figs)
        mark_bold_blocks(cleaned.binary, ocr)
        if use_vision:
            try:
                refine_blocks(cleaned.gray, ocr, vcfg)
            except Exception as exc:
                # keep Tesseract text for this page rather than aborting the doc
                print(f"    ! vision OCR failed on page {i + 1}, kept Tesseract: {exc}")
        add_page(out, page.rect.width, page.rect.height, ocr, figs, cleaned.color)

        stat = PageStat(
            index=i,
            words=len(ocr.words),
            figures=len(figs),
            mean_conf=ocr.mean_conf,
            skew=cleaned.angle,
        )
        stats.append(stat)
        if on_page:
            on_page(stat)

    dst.parent.mkdir(parents=True, exist_ok=True)
    out.save(dst, garbage=4, deflate=True)
    out.close()
    doc.close()
    return DocResult(src=src, dst=dst, pages=stats)
