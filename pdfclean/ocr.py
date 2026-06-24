"""OCR with layout: run Tesseract and return a block / paragraph / line / word tree.

We use Tesseract's TSV output (``image_to_data``) which tags every word with a
bounding box, confidence, and its block / paragraph / line numbers. Keeping that
hierarchy lets the reconstructor re-flow each column block as natural wrapped
text instead of scattering words at absolute positions. All coordinates are in
pixels of the image that was passed in.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pytesseract


@dataclass
class Word:
    text: str
    x: int
    y: int
    w: int
    h: int
    conf: float

    @property
    def right(self) -> int:
        return self.x + self.w

    @property
    def bottom(self) -> int:
        return self.y + self.h


def _bbox(words: list[Word]) -> tuple[int, int, int, int]:
    return (
        min(w.x for w in words),
        min(w.y for w in words),
        max(w.right for w in words),
        max(w.bottom for w in words),
    )


@dataclass
class Line:
    words: list[Word] = field(default_factory=list)

    @property
    def bbox(self) -> tuple[int, int, int, int]:
        return _bbox(self.words)

    @property
    def height(self) -> float:
        return float(np.median([w.h for w in self.words]))

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words)


@dataclass
class Paragraph:
    lines: list[Line] = field(default_factory=list)

    @property
    def text(self) -> str:
        return " ".join(ln.text for ln in self.lines)


@dataclass
class Block:
    paragraphs: list[Paragraph] = field(default_factory=list)
    bold: bool = False   # filled in by style detection (style.py)
    italic: bool = False
    override_text: str | None = None  # set by vision OCR to replace Tesseract text

    @property
    def lines(self) -> list[Line]:
        return [ln for p in self.paragraphs for ln in p.lines]

    @property
    def words(self) -> list[Word]:
        return [w for ln in self.lines for w in ln.words]

    @property
    def bbox(self) -> tuple[int, int, int, int]:
        return _bbox(self.words)

    @property
    def median_line_height(self) -> float:
        return float(np.median([ln.height for ln in self.lines]))

    @property
    def median_leading(self) -> float:
        """Baseline-to-baseline pitch in px (matches the scan's line spacing)."""
        tops = sorted(ln.bbox[1] for ln in self.lines)
        gaps = [tops[i + 1] - tops[i] for i in range(len(tops) - 1)]
        gaps = [g for g in gaps if g > 0]
        return float(np.median(gaps)) if gaps else self.median_line_height * 1.45

    @property
    def n_lines(self) -> int:
        return len(self.lines)

    @property
    def text(self) -> str:
        """Block text — vision override if present, else the Tesseract text."""
        if self.override_text is not None:
            return self.override_text
        return "\n".join(p.text for p in self.paragraphs)


@dataclass
class OCRResult:
    blocks: list[Block]
    img_w: int
    img_h: int

    @property
    def words(self) -> list[Word]:
        return [w for b in self.blocks for w in b.words]

    @property
    def lines(self) -> list[Line]:
        return [ln for b in self.blocks for ln in b.lines]

    @property
    def mean_conf(self) -> float:
        ws = self.words
        return float(np.mean([w.conf for w in ws])) if ws else 0.0


def dehyphenate(ocr: OCRResult) -> None:
    """Join words the scan broke across a line with a hyphen.

    Print columns hyphenate at line ends ("inte-" / "grated"). After we re-flow
    the text those hyphens would otherwise sit mid-line ("inte- grated"). We merge
    the two halves back together: drop the hyphen when the next line starts
    lowercase (a soft break, ``discover``), keep it for a proper/compound word
    (``non-European``). Runs before figure-splitting so a word broken right at a
    figure edge is rejoined too. Mutates the tree in place.
    """
    for block in ocr.blocks:
        for para in block.paragraphs:
            lines = para.lines
            i = 0
            while i < len(lines) - 1:
                cur, nxt = lines[i], lines[i + 1]
                if cur.words and nxt.words:
                    last, first = cur.words[-1], nxt.words[0]
                    if (
                        len(last.text) >= 2 and last.text.endswith("-")
                        and last.text[-2].isalpha() and first.text[:1].isalpha()
                    ):
                        stem = last.text[:-1] if first.text[:1].islower() else last.text
                        last.text = stem + first.text
                        last.w = max(last.w, first.right - last.x)  # cover merged span
                        nxt.words.pop(0)
                        if not nxt.words:        # line emptied -> drop it
                            lines.pop(i + 1)
                            continue
                i += 1


def run_ocr(gray: np.ndarray, lang: str = "eng", min_conf: float = 40.0, psm: int = 3) -> OCRResult:
    """OCR a (cleaned) grayscale image into a block/paragraph/line/word tree."""
    h, w = gray.shape[:2]
    config = f"--oem 1 --psm {psm}"
    data = pytesseract.image_to_data(
        gray, lang=lang, config=config, output_type=pytesseract.Output.DICT
    )

    # build nested dicts keyed by Tesseract's hierarchy numbers, preserving order
    blocks: dict[int, dict[int, dict[int, Line]]] = {}
    order: list[int] = []
    n = len(data["text"])
    for i in range(n):
        text = (data["text"][i] or "").strip()
        if not text:
            continue
        try:
            conf = float(data["conf"][i])
        except (TypeError, ValueError):
            conf = -1.0
        if conf < min_conf:
            continue
        word = Word(
            text=text,
            x=int(data["left"][i]),
            y=int(data["top"][i]),
            w=int(data["width"][i]),
            h=int(data["height"][i]),
            conf=conf,
        )
        b, p, ln = int(data["block_num"][i]), int(data["par_num"][i]), int(data["line_num"][i])
        if b not in blocks:
            blocks[b] = {}
            order.append(b)
        blocks[b].setdefault(p, {}).setdefault(ln, Line()).words.append(word)

    result_blocks: list[Block] = []
    for b in order:
        block = Block()
        for p in sorted(blocks[b]):
            para = Paragraph()
            for ln in sorted(blocks[b][p]):
                line = blocks[b][p][ln]
                line.words.sort(key=lambda wd: wd.x)
                para.lines.append(line)
            if para.lines:
                block.paragraphs.append(para)
        if block.paragraphs:
            result_blocks.append(block)

    # reading order: column-major (left column top-to-bottom, then right column)
    def col_key(blk: Block) -> tuple[int, int]:
        x0, y0, _, _ = blk.bbox
        return (int(x0 / (w / 2)), y0)  # 0 = left half, 1 = right half

    result_blocks.sort(key=col_key)
    return OCRResult(blocks=result_blocks, img_w=w, img_h=h)
