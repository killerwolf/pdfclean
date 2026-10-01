# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.1.0] - 2026-06-24

### Added

- First release: `python -m pdfclean input/ -o output/` turns scanned,
  image-only PDFs into born-digital ones — real selectable text, real figures,
  no page photo.
- Per page: extract the embedded scan, clean it (deskew, background whitening,
  denoise), OCR it with Tesseract, detect figures the OCR didn't claim as text,
  then rebuild the page with reflowed text at a font size chosen to fill each
  block's rectangle.
- Batch a folder of PDFs; the output tree mirrors the input tree.
- Optional `--engine vision` corrects OCR errors and detects italics using a
  hosted multimodal model (Mistral by default, OpenRouter supported). Falls
  back to the Tesseract text per page if a call fails, so a batch always
  finishes.
- `--no-figures` for text-only output, `--max-pages` to test cheaply,
  `--min-conf` and `--psm` to trade OCR quality against speed.
- `--api-key`, or the provider's env var, or a gitignored `.env.local`.

<!--
Writing entries:

Sections, in this order, omitting the empty ones:
  Added / Changed / Deprecated / Removed / Fixed / Security

Write for the person who has to decide whether to upgrade. That means the effect,
then the reason — not the file that changed.

  Good: "Partial style overrides no longer discard the defaults. Passing
         `{ style: { width: '40px' } }` previously replaced the whole object,
         leaving the marker with no border or background."
  Bad:  "Fixed merge bug in options handling."

Anything that breaks a caller belongs under Changed or Removed with the migration
spelled out, even when the version number already implies it.
-->