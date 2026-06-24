"""Reconstruct a born-digital PDF page from OCR results + detected figures.

Readability-first: rather than dropping each word at its absolute x (which leaves
airy, scattered gaps), we re-flow every OCR *block* — a column of text — as real,
wrapped, justified paragraphs inside that block's rectangle. The two-column layout,
paragraph breaks and headings survive, but the text reads like a normal document.
Detected figures are re-embedded as small cleaned crops; OCR junk that landed on
top of a figure is dropped.
"""
from __future__ import annotations

import cv2
import fitz

from .clean import clean_figure_crop
from .figures import Figure
from .ocr import Block, OCRResult

# Base-14 PDF fonts, PyMuPDF reserved codes (Times family): never embedded.
BODY_FONT = "tiro"  # Times-Roman
_FONTS = {
    (False, False): "tiro",  # regular
    (True, False): "tibo",   # bold
    (False, True): "tiit",   # italic
    (True, True): "tibi",    # bold-italic
}

JUSTIFY = fitz.TEXT_ALIGN_JUSTIFY
LEFT = fitz.TEXT_ALIGN_LEFT


def _font_for(block: Block, base: str) -> str:
    if base != BODY_FONT:  # caller forced a specific font
        return base
    return _FONTS.get((block.bold, block.italic), BODY_FONT)


# Base-14 Times only covers Latin-1, so smart quotes / dashes the vision model
# returns (', ', ", ", —, …) would render as "?". Fold them to ASCII equivalents.
_PUNCT = {
    0x2018: "'", 0x2019: "'", 0x201A: "'", 0x201B: "'", 0x2032: "'",
    0x201C: '"', 0x201D: '"', 0x201E: '"', 0x201F: '"', 0x2033: '"',
    0x2013: "-", 0x2014: "-", 0x2015: "-", 0x2212: "-",
    0x2026: "...", 0x2022: "-",
    0x00A0: " ", 0x2009: " ", 0x202F: " ", 0x200B: "",
}


def _normalize_punct(text: str) -> str:
    return text.translate(_PUNCT)


def add_page(
    out: fitz.Document,
    page_w: float,
    page_h: float,
    ocr: OCRResult,
    figures: list[Figure],
    color_img,
    font: str = BODY_FONT,
) -> None:
    """Append one reconstructed page to ``out``."""
    page = out.new_page(width=page_w, height=page_h)
    sx = page_w / ocr.img_w
    sy = page_h / ocr.img_h

    fig_rects_px = [(f.x, f.y, f.x + f.w, f.y + f.h) for f in figures]

    # figures first, so text never hides them
    if color_img is not None:
        for f in figures:
            crop = color_img[f.y : f.y + f.h, f.x : f.x + f.w]
            if crop.size == 0:
                continue
            clean = clean_figure_crop(crop)  # denoised grayscale on white -> small JPEG
            ok, buf = cv2.imencode(".jpg", clean, [cv2.IMWRITE_JPEG_QUALITY, 80])
            if not ok:
                continue
            rect = fitz.Rect(f.x * sx, f.y * sy, (f.x + f.w) * sx, (f.y + f.h) * sy)
            page.insert_image(rect, stream=buf.tobytes())

    for block in ocr.blocks:
        if _mostly_inside_figure(block, fig_rects_px):
            continue  # OCR noise over an illustration
        _place_block(page, block, sx, sy, font)


def _mostly_inside_figure(block: Block, fig_rects_px, thresh: float = 0.6) -> bool:
    x0, y0, x1, y1 = block.bbox
    area = max(1, (x1 - x0) * (y1 - y0))
    for fx0, fy0, fx1, fy1 in fig_rects_px:
        ix = max(0, min(x1, fx1) - max(x0, fx0))
        iy = max(0, min(y1, fy1) - max(y0, fy0))
        if (ix * iy) / area > thresh:
            return True
    return False


def _place_block(page: fitz.Page, block: Block, sx: float, sy: float, font: str) -> None:
    x0, y0, x1, y1 = block.bbox
    # Keep the box within the block's own y-extent (no bottom padding) so a block
    # never bleeds into the one below it — e.g. the next band of a figure-split
    # block. A hair of side padding keeps glyph edges off the border.
    rect = fitz.Rect(x0 * sx - 1, y0 * sy - 0.5, x1 * sx + 1, y1 * sy)

    text = _normalize_punct(block.text)  # vision-corrected if available, else Tesseract
    if not text.strip():
        return

    fontname = _font_for(block, font)
    align = JUSTIFY if block.n_lines > 1 else LEFT
    # line spacing as a multiple of font size, taken from the scan (leading
    # relative to glyph height) so spacing tracks the original.
    lineheight = min(1.7, max(1.1, block.median_leading / max(1.0, block.median_line_height)))

    # Times is narrower than the scan's face, so the same text reflows into fewer
    # lines and would leave the column half-empty. Grow the font to the largest
    # size that still fills the block's rectangle — this kills the negative space
    # and matches the original's text size/density.
    fontsize = _fill_fontsize(text, rect, fontname, align, lineheight)
    page.insert_textbox(
        rect, text, fontname=fontname, fontsize=fontsize,
        align=align, lineheight=lineheight, color=(0, 0, 0),
    )


def _fill_fontsize(
    text: str, rect: fitz.Rect, font: str, align: int, lineheight: float,
    min_fs: float = 4.0, max_fs: float = 36.0,
) -> float:
    """Largest font size whose wrapped text actually renders inside the rectangle.

    We can't trust ``insert_textbox``'s leftover return value — it reports "fits"
    while the last line is still drawn several points below the box. So we render
    to a throwaway page and check the true glyph bottom. "Fits" is monotonic in
    font size, so we binary search the largest size that stays inside.
    """
    lo, hi = min_fs, max_fs
    for _ in range(14):
        mid = (lo + hi) / 2
        if _fits(text, rect, font, mid, align, lineheight):
            lo = mid
        else:
            hi = mid
    return max(min_fs, lo)


def _fits(text: str, rect: fitz.Rect, font: str, fs: float, align: int, lineheight: float) -> bool:
    """True iff all of ``text`` renders inside ``rect`` at this font size.

    Two independent checks, because neither alone is reliable: ``insert_textbox``
    drops overflowing text and reports a negative leftover (so a tiny leftover
    means nothing was dropped), *and* the lowest drawn glyph must actually sit
    within the box (the leftover value lies by up to a line about this).
    """
    tmp = fitz.open()
    page = tmp.new_page(width=rect.x1 + 50, height=rect.y1 + 600)
    leftover = page.insert_textbox(
        rect, text, fontname=font, fontsize=fs, align=align, lineheight=lineheight
    )
    info = page.get_text("dict")
    bottoms = [ln["bbox"][3] for blk in info["blocks"] for ln in blk.get("lines", [])]
    tmp.close()
    if leftover < 0:          # some text was dropped -> too big
        return False
    if not bottoms:           # nothing to draw (blank text) -> trivially fits
        return True
    return max(bottoms) <= rect.y1
