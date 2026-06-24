"""Command-line interface: batch a folder (or a single file) of scanned PDFs."""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

from .pipeline import process_pdf
from .vision import PROVIDERS, VisionConfig


def _load_dotenv(*names: str) -> None:
    """Load simple KEY=VALUE lines from .env.local / .env in the CWD.

    Lets you keep API keys in a gitignored .env.local instead of exporting them.
    Existing environment variables win (we don't overwrite them).
    """
    for name in names or (".env.local", ".env"):
        path = Path(name)
        if not path.is_file():
            continue
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key, val = key.strip(), val.strip().strip('"').strip("'")
            if key:
                os.environ.setdefault(key, val)


def _gather_inputs(path: Path) -> list[Path]:
    if path.is_dir():
        return sorted(p for p in path.rglob("*.pdf") if p.is_file())
    if path.is_file() and path.suffix.lower() == ".pdf":
        return [path]
    return []


def _out_path(src: Path, in_root: Path, out_root: Path) -> Path:
    if in_root.is_dir():
        rel = src.relative_to(in_root)
        return (out_root / rel).with_suffix(".pdf")
    return out_root / f"{src.stem}.clean.pdf"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="pdfclean",
        description="Turn scanned (image-only) PDFs into clean, born-digital text PDFs.",
    )
    p.add_argument("input", type=Path, help="input PDF file or folder of PDFs")
    p.add_argument("-o", "--output", type=Path, required=True, help="output folder")
    p.add_argument("-l", "--lang", default="eng", help="Tesseract language(s), e.g. eng or eng+fra")
    p.add_argument("--min-conf", type=float, default=40.0, help="drop OCR words below this confidence")
    p.add_argument("--psm", type=int, default=3, help="Tesseract page-segmentation mode")
    p.add_argument("--no-deskew", action="store_true", help="skip skew correction")
    p.add_argument("--no-figures", action="store_true", help="do not re-embed detected figures")
    p.add_argument("--overwrite", action="store_true", help="overwrite existing outputs")
    p.add_argument(
        "--max-pages", type=int, default=0,
        help="only process the first N pages of each PDF (0 = all); handy for a cheap vision test",
    )

    g = p.add_argument_group("vision OCR (optional, hosted — uploads page images)")
    g.add_argument(
        "--engine", choices=["tesseract", "vision"], default="tesseract",
        help="OCR engine: local Tesseract (default) or a hosted vision model",
    )
    g.add_argument(
        "--provider", choices=sorted(PROVIDERS), default="mistral",
        help="vision provider (default: mistral)",
    )
    g.add_argument("--model", default=None, help="override the provider's default model")
    g.add_argument(
        "--api-key", default=None,
        help="API key (else read from the provider's env var, e.g. MISTRAL_API_KEY)",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _load_dotenv()

    inputs = _gather_inputs(args.input)
    if not inputs:
        print(f"error: no PDF found at {args.input}", file=sys.stderr)
        return 2

    vision_cfg = VisionConfig(provider=args.provider, model=args.model, api_key=args.api_key)
    if args.engine == "vision":
        try:  # fail fast on a missing key before processing anything
            vision_cfg.resolved()
        except Exception as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        print(f"Vision OCR via {args.provider} — page images will be uploaded to the provider.")

    print(f"Found {len(inputs)} PDF(s). Writing to {args.output}/")
    failures = 0
    for src in inputs:
        dst = _out_path(src, args.input, args.output)
        if dst.exists() and not args.overwrite:
            print(f"  · skip (exists): {dst.name}  (use --overwrite)")
            continue
        t0 = time.time()
        try:
            result = process_pdf(
                src,
                dst,
                lang=args.lang,
                min_conf=args.min_conf,
                deskew=not args.no_deskew,
                figures=not args.no_figures,
                psm=args.psm,
                engine=args.engine,
                vision_cfg=vision_cfg,
                max_pages=args.max_pages,
            )
        except Exception as exc:  # keep the batch going
            failures += 1
            print(f"  ✗ {src.name}: {type(exc).__name__}: {exc}", file=sys.stderr)
            continue
        dt = time.time() - t0
        print(
            f"  ✓ {src.name} → {dst.name}  "
            f"[{len(result.pages)}p, {result.total_words} words, "
            f"conf {result.mean_conf:.0f}, {dt:.1f}s]"
        )

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
