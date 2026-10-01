# pdfclean

<p align="center">
  <a href="https://github.com/killerwolf/pdfclean/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/killerwolf/pdfclean/actions/workflows/ci.yml/badge.svg"></a>
  <a href="https://github.com/killerwolf/pdfclean/blob/main/LICENSE"><img alt="MIT" src="https://img.shields.io/github/license/killerwolf/pdfclean"></a>
  <img alt="Python 3.11" src="https://img.shields.io/badge/python-3.11-3776ab?logo=python&logoColor=white">
  <a href="https://github.com/killerwolf/pdfclean/releases"><img alt="Changelog" src="https://img.shields.io/badge/changelog-0.1.0-informational"></a>
</p>

Turn **scanned, image-only PDFs** into **clean, born-digital PDFs** — real
embedded text you can select, search and copy, with the full-page scan image
thrown away and only true figures re-embedded.

[![in / out](https://raw.githubusercontent.com/killerwolf/pdfclean/main/.github/assets/before-after.png)](https://raw.githubusercontent.com/killerwolf/pdfclean/main/.github/assets/before-after.png)

*Page 1 of the bundled sample, before and after. Same 593×841 pt page: the
photo of the scan is gone, replaced by selectable text and the real figure.*

This is the aggressive "reconstruct from scratch" path: the output contains no
page photo, just positioned text + cropped illustrations. Files come out small
and editable. The trade-off is honesty about OCR — recognition mistakes appear
as real text, and layout is approximated rather than pixel-perfect.

**Yes, if** you have a pile of image-only scans — a book chapter, a paper, a
French admin form — and want small, searchable, editable PDFs without paying
per page to a hosted service. The default engine is local Tesseract: nothing
leaves your machine.

**No, if** you need pixel-perfect layout, or the original text layer exactly as
it was, or colour figures. For layout-preserving OCR with invisible text over the
image, `ocrmypdf` is the better tool. For exact extraction of an existing text
layer, you don't need OCR at all — `pdfplumber` or `pypdf` will do.

## What it does, per page

1. **Extract** the embedded full-page scan at native resolution.
2. **Clean** — deskew, whiten the grey paper background, denoise/despeckle,
   sharpen text edges (`pdfclean/clean.py`).
3. **OCR with layout** — Tesseract gives every word a bounding box + confidence
   (`pdfclean/ocr.py`).
4. **Detect figures** — ink the OCR didn't claim as text becomes a figure
   candidate; slivers and speckle are filtered out. A text block that a figure
   cuts through is split into y-bands so the reflowed text wraps *around* the
   illustration instead of running over it (`pdfclean/figures.py`).
5. **Reconstruct** — a brand-new page. Each OCR *block* (a column of text) is
   re-flowed as real, wrapped, justified paragraphs in that block's rectangle.
   The font is grown to the **largest size that fills the block** (Times is
   narrower than the scan's face, so without this the column would be left
   half-empty), and line spacing is taken from the scan — so text size, density
   and the heading/body hierarchy track the original. Heavier-stroked blocks are
   rendered bold (`pdfclean/style.py`). Figures are denoised (non-local-means +
   white-point) and re-embedded as small JPEGs (`pdfclean/reconstruct.py`).

On the bundled sample (`assets/…silent-language….pdf`): 10 pages,
2,147 kB image-only scan → **310 kB** fully selectable PDF (a 7× reduction), 6 761
words, mean OCR confidence 95. Measured with:

```bash
python -m pdfclean assets/ -o output/ --overwrite
```

The numbers above are what that run prints — re-measure rather than trusting
them if you change the pipeline.

## How it works

### High level

The big picture: read a page of pixels, understand it, and rebuild it as real
text. The optional hosted vision model only makes the "understand" step more
accurate — the rest is unchanged.

```mermaid
flowchart LR
    IN["Scanned PDF<br/>(image-only pages,<br/>no real text)"] --> READ["Clean &amp; understand<br/>each page<br/>(image cleanup + OCR)"]
    READ --> BUILD["Rebuild the page<br/>with real positioned text<br/>+ cropped figures"]
    BUILD --> OUT["Born-digital PDF<br/>(selectable · searchable<br/>· tiny · editable)"]
    BOOST["Optional hosted vision model<br/>--engine vision:<br/>fixes OCR errors + italics"] -. enhances .-> READ
```

### Low level

Every step, with the module/function that does it and the data it passes along.
The only fork is the OCR **engine**; everything after it is identical.

```mermaid
flowchart TD
    A["Scanned PDF"] --> CLI["cli.main<br/>load .env.local · gather PDFs · batch loop"]
    CLI --> PP["pipeline.process_pdf — per page:"]
    PP --> B["_page_to_bgr<br/>extract embedded full-page scan"]
    B --> C["clean.clean_image<br/>deskew · whiten bg · denoise → CleanedPage"]
    C --> D["ocr.run_ocr<br/>Tesseract → OCRResult tree"]
    D --> DH["ocr.dehyphenate<br/>rejoin line-break hyphens (inte- grated → integrated)"]
    DH --> F["figures.detect_figures<br/>ink OCR didn't claim → figure boxes"]
    F --> SP["figures.split_blocks_around_figures<br/>y-band slicing so text wraps, not overlaps"]
    SP --> BO["style.mark_bold_blocks<br/>stroke-width per block"]
    BO --> ENG{"--engine ?"}
    ENG -->|"vision"| VIS["vision.refine_blocks<br/>upload page + blocks to Mistral / OpenRouter<br/>→ block.override_text + italic"]
    ENG -->|"tesseract (default)"| KEEP["keep Tesseract text"]
    VIS --> RC
    KEEP --> RC["reconstruct.add_page — per block:"]
    RC --> EF["embed denoised figure crops<br/>clean_figure_crop → JPEG"]
    EF --> NP["_normalize_punct<br/>smart quotes / dashes → ASCII"]
    NP --> FF["_fill_fontsize<br/>binary-search largest font that _fits the rect"]
    FF --> IT["page.insert_textbox<br/>real Times / Bold / Italic, justified"]
    IT --> SAVE["out.save · garbage-collect + deflate"]
    SAVE --> Z["Born-digital PDF"]
    D -. "builds" .-> DM["Data model<br/>OCRResult → Block → Paragraph → Line → Word<br/>Block.bold / italic / override_text"]
    DM -. "read by" .-> RC
```

### Module map

| module | responsibility |
|--------|----------------|
| `pdfclean/cli.py` | argument parsing, `.env.local` loading, batch loop over files |
| `pdfclean/pipeline.py` | per-document orchestration: pulls each page's scan, runs the steps above, assembles the output PDF |
| `pdfclean/clean.py` | image cleanup — deskew, background whitening, denoise; also cleans figure crops |
| `pdfclean/ocr.py` | runs Tesseract, builds the `Block → Paragraph → Line → Word` tree with bounding boxes |
| `pdfclean/figures.py` | detects illustration regions and splits text blocks that a figure cuts through |
| `pdfclean/style.py` | per-block **bold** detection from stroke thickness |
| `pdfclean/vision.py` | optional hosted-model text correction + italic detection (`--engine vision`) |
| `pdfclean/reconstruct.py` | builds the new page: reflows each block's text to fill its rectangle, embeds figures |

### The data model

OCR produces a tree that every later step reads from. A page is a list of
**blocks** (≈ a column or a heading); each block holds **paragraphs → lines →
words**, and every word carries its pixel bounding box and confidence:

```
OCRResult
└── blocks: list[Block]            # one per column / heading, in reading order
    ├── bold / italic / override_text   # set by style.py / vision.py
    └── paragraphs: list[Paragraph]
        └── lines: list[Line]
            └── words: list[Word]  # text + (x, y, w, h) + confidence
```

`reconstruct.py` walks the blocks, takes each block's rectangle (the bounding box
of its words), and re-flows the block's text into it at the largest font that
fits — so the geometry comes from the scan but the glyphs are real, embedded
font text.

## Setup

Requires Python 3.11 and Tesseract. The conda route installs both:

```bash
conda env create -f environment.yml
conda activate pdf-ocr
```

<details>
<summary>Conda asks you to accept Anaconda's terms of service</summary>

`environment.yml` only lists `conda-forge`, but conda still consults its
`defaults` channels (repo.anaconda.com), which now require ToS acceptance. Point
conda at a config that replaces those defaults — nothing else is consulted
early enough to prevent the prompt:

```bash
printf 'channels:\n  - conda-forge\ndefault_channels:\n  - conda-forge\nchannel_priority: strict\n' > .condarc
CONDARC=$PWD/.condarc conda env create -f environment.yml
```
</details>

Prefer pip? The Python packages are ordinary wheels; only Tesseract itself is a
system binary (`brew install tesseract`, `apt install tesseract-ocr`):

```bash
python -m venv .venv && source .venv/bin/activate
pip install pymupdf "opencv-python-headless<4.13.0" pytesseract requests pillow numpy
```

> The `opencv-python-headless` bound matters on macOS 13 Intel, where the newer
> releases ship no wheel and pip silently falls back to compiling from source.

## Usage

```bash
# batch a folder (recurses, mirrors structure into output/)
python -m pdfclean input/ -o output/

# a single file
python -m pdfclean scan.pdf -o output/        # -> output/scan.clean.pdf

# every flag, in your terminal
python -m pdfclean --help
```

Nothing is overwritten unless you pass `--overwrite`, so re-running a batch is
safe. If no PDF is found at the path you gave, it exits `2` with the reason on
stderr; if some files in a batch fail, the rest still run and the exit code is
`1`.

### Options

| flag | default | meaning |
|------|---------|---------|
| `-o, --output` | (required) | output folder |
| `-l, --lang` | `eng` | Tesseract language(s), e.g. `eng+fra` |
| `--min-conf` | `40` | drop OCR words below this confidence |
| `--psm` | `3` | Tesseract page-segmentation mode |
| `--no-deskew` | off | skip skew correction |
| `--no-figures` | off | text only, don't re-embed figures |
| `--overwrite` | off | overwrite existing outputs |
| `--max-pages` | `0` (all) | only process the first N pages (cheap vision test) |
| `--engine` | `tesseract` | `tesseract` (local) or `vision` (hosted, see below) |
| `--provider` | `mistral` | vision provider: `mistral` or `openrouter` |
| `--model` | provider default | override the vision model |
| `--api-key` | env var | API key (else from `MISTRAL_API_KEY` / `OPENROUTER_API_KEY`) |

## Higher-accuracy OCR with a hosted vision model (optional)

Tesseract is local and free but makes mistakes (a script drop-cap *The* → junk,
an italic *in* → `mm`). `--engine vision` keeps the Tesseract **layout** (block
rectangles, font size, line pitch) but sends each page image to a hosted
multimodal model to **correct the text** and **detect italics**. No self-hosting.

Default provider is **Mistral** (free tier):

```bash
# 1. get a free key at https://console.mistral.ai  ->  API Keys
export MISTRAL_API_KEY=sk-...
#    ...or drop it in a gitignored .env.local (KEY=VALUE), auto-loaded by the CLI:
#    echo 'MISTRAL_API_KEY=sk-...' > .env.local

# 2. test on one page first (one API call), then run the whole thing
python -m pdfclean assets/ -o output/ --engine vision --max-pages 1
python -m pdfclean assets/ -o output/ --engine vision
```

Smart quotes / dashes the model returns are folded to ASCII so they render in the
base-14 font (no `?` boxes, files stay tiny).

Or use **OpenRouter**'s free vision models:

```bash
export OPENROUTER_API_KEY=sk-or-...
python -m pdfclean assets/ -o output/ --engine vision --provider openrouter
# pick a specific free model (the default may get rate-limited):
python -m pdfclean assets/ -o output/ --engine vision --provider openrouter \
  --model nvidia/nemotron-nano-12b-v2-vl:free
```

OpenRouter's free vision tier works but is **slow (~30–60 s/page) and heavily
rate-limited** (shared free pools return 429 / 504). The tool retries transient
errors and falls back to Tesseract per page if they persist, so a batch always
finishes. Check the current free list at
<https://openrouter.ai/api/v1/models> (filter for `:free` + image input) and
pass it with `--model`. For reliable throughput, Mistral's free tier is steadier.

If a page's API call fails (rate limit, network), that page silently falls back
to the Tesseract text so the batch still completes.

> **Privacy:** with `--engine vision`, each page image is uploaded to the chosen
> provider. Don't use it on confidential scans unless that's acceptable. The
> default `--engine tesseract` is fully local.

## Known limits & upgrade paths

- **OCR errors are visible** with the default local engine (e.g. an italic *in*
  read as `mm`, a decorative drop-cap *The* read as junk). Use `--engine vision`
  (above) to fix most of these and recover italics.
- **Layout is approximated, not pixel-perfect** — text is re-flowed per column
  block, so line breaks and vertical positions differ slightly from the original.
  This is deliberate: readability is prioritised over exact placement.
- **Italics:** detected by the `vision` engine; with the local `tesseract`
  engine italic type is rendered upright (bold is still detected via stroke
  weight). Local slant detection was too noisy to ship safely.
- **Figures are grayscale.** Fine for line art; a `--color-figures` flag could
  preserve colour photos.
- **Non-Latin scripts.** Tesseract needs language packs installed (`-l fra`, and
  so on). Output is reflowed with Times/Helvetica/Courier so the files stay tiny,
  which means glyphs those base-14 fonts lack render as `?` — CJK output needs an
  embedded font, not just a `--lang` flag.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for the dev environment and the checks CI
runs. [CHANGELOG.md](CHANGELOG.md) tracks what changed for users.

## Licence

MIT — see [LICENSE](LICENSE).
