# Contributing

## Development environment

### Requirements

- Miniconda (or any conda/mamba distribution) — the project targets conda, not pip
- Tesseract comes from conda; no system-level install needed

### Setup

```bash
git clone https://github.com/killerwolf/pdfclean.git
cd pdfclean
conda env create -f environment.yml
conda activate pdf-ocr
```

Verify the toolchain end to end before changing anything:

```bash
python -m pdfclean assets/ -o output/ --max-pages 1 --overwrite
```

> **Note on channels.** `environment.yml` lists `conda-forge`, but conda still
> consults `defaults` (repo.anaconda.com) unless you override. If `conda env create`
> stops with `CondaToSNonInteractiveError`, either accept the Anaconda ToS
> (`conda tos accept --override-channels --channel https://repo.anaconda.com/pkgs/main`),
> or skip those channels entirely:
>
> ```bash
> conda env create -f environment.yml --override-channels -c conda-forge
> ```
>
> The `--override-channels -c conda-forge` form needs no ToS acceptance.

## Project structure

```
pdfclean/
├── pdfclean/
│   ├── cli.py           # argument parsing, .env.local loading, batch loop
│   ├── pipeline.py      # per-document orchestration
│   ├── clean.py         # deskew, background whitening, denoise
│   ├── ocr.py           # Tesseract + the Block → Paragraph → Line → Word tree
│   ├── figures.py       # figure detection, block splitting around figures
│   ├── style.py         # bold detection from stroke width
│   ├── vision.py        # optional hosted vision OCR
│   └── reconstruct.py   # rebuilds the page with real text + figures
├── assets/              # sample scanned PDF
└── environment.yml
```

## Making a change

1. Branch from `main`.
2. Make the change. The pipeline order in `pipeline.py` is the contract everything
   else depends on — if you add a step, keep it there and update the README's
   flow diagram to match.
3. Run a one-page conversion locally (command above); CI runs a real conversion
   on the bundled sample, so a broken pipeline fails the build.
4. Add a `CHANGELOG.md` entry under `## [Unreleased]` if the change is user-visible.
5. Open a pull request describing what changed and why.

### Things that are easy to get wrong

- **Don't invent numbers.** The README quotes file sizes, word counts and OCR
  confidence. If you change the pipeline, re-measure them on the bundled sample
  and update the README rather than leaving stale figures.
- **Privacy.** `--engine vision` uploads page images to a third party. Keep it
  opt-in, and never make it the default.
- **Base-14 fonts.** Output uses Times/Helvetica/Courier so the files stay tiny.
  Text containing characters those fonts can't map renders as `?` — normalise
  punctuation (see `_normalize_punct` in `reconstruct.py`) rather than embedding
  a font.
- **Base64 blobs don't belong in the README.** Embedded base64 images don't render
  on GitHub and bloat the file. Commit PNGs under `.github/assets/`.

## Releasing

1. Move the `## [Unreleased]` entries in `CHANGELOG.md` into a new version section
   with today's date.
2. Bump `__version__` in `pdfclean/__init__.py`.
3. Commit, merge to `main`.
4. Tag and push: `git tag v<VERSION> && git push origin v<VERSION>`.
5. Create the GitHub Release from the tag.

## Code of conduct

Be decent to people. Report problems as a GitHub issue on the repo.