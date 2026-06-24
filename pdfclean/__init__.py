"""pdfclean — turn scanned (image-only) PDFs into clean, born-digital text PDFs.

Pipeline per page:
  1. extract the embedded scan image
  2. clean it (deskew, background whitening, denoise)   -> clean.py
  3. OCR with layout/positions                          -> ocr.py
  4. reconstruct a new page with real text + figures    -> reconstruct.py

Orchestrated by pipeline.py, driven from the CLI in cli.py.
"""

__version__ = "0.1.0"
