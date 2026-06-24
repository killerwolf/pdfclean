"""Optional hosted vision-OCR refinement.

Tesseract gives us the page *layout* (block rectangles, font size, line pitch).
This module sends the page image to a hosted multimodal model and asks it to
**correct the text** of each block and flag italics — fixing OCR mistakes
("in" -> "mm", a script drop-cap "The" -> junk) that Tesseract can't. The
corrected text is written back onto each block via ``override_text``; the rest
of the reconstruction (flow, size, spacing, figures) is unchanged.

No self-hosting: it calls a free-tier hosted API (Mistral by default; any
OpenAI-compatible vision endpoint such as OpenRouter works too). The API key is
read from an environment variable — nothing is hard-coded.

Privacy note: with ``--engine vision`` each page image is uploaded to the chosen
provider. That's inherent to using a hosted model.
"""
from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass

import cv2
import numpy as np

from .ocr import OCRResult

# ---- providers -------------------------------------------------------------

@dataclass
class Provider:
    name: str
    url: str
    model: str
    key_env: str


PROVIDERS = {
    # Mistral "La Plateforme" — free tier; get a key at https://console.mistral.ai
    "mistral": Provider(
        name="mistral",
        url="https://api.mistral.ai/v1/chat/completions",
        model="pixtral-12b-2409",
        key_env="MISTRAL_API_KEY",
    ),
    # OpenRouter — has free vision models; https://openrouter.ai/keys
    "openrouter": Provider(
        name="openrouter",
        url="https://openrouter.ai/api/v1/chat/completions",
        model="meta-llama/llama-3.2-11b-vision-instruct:free",
        key_env="OPENROUTER_API_KEY",
    ),
}


@dataclass
class VisionConfig:
    provider: str = "mistral"
    model: str | None = None      # override the provider default
    api_key: str | None = None    # override the env var
    max_px: int = 1600            # longest page-image edge sent to the model
    timeout: float = 120.0

    def resolved(self) -> tuple[Provider, str]:
        if self.provider not in PROVIDERS:
            raise ValueError(
                f"unknown provider '{self.provider}'. Options: {', '.join(PROVIDERS)}"
            )
        prov = PROVIDERS[self.provider]
        key = self.api_key or os.environ.get(prov.key_env)
        if not key:
            raise RuntimeError(
                f"no API key for provider '{prov.name}'. "
                f"Set ${prov.key_env} or pass --api-key."
            )
        return prov, key


# ---- core ------------------------------------------------------------------

_PROMPT = (
    "You are correcting OCR output from a scanned page. The attached image is the "
    "page. Below is the rough OCR text split into numbered blocks, each with its "
    "bounding box on a 0-1000 grid (x0,y0,x1,y1).\n"
    "For EACH block, return the exact text as printed in that region of the image, "
    "fixing OCR errors. Rules:\n"
    "- Transcribe verbatim; do not translate, summarise, or add words.\n"
    "- Keep paragraph breaks as newlines (\\n) inside a block's text.\n"
    "- Join hyphenated line-break words back together (e.g. 'inte- grated' -> 'integrated').\n"
    "- Set \"italic\": true only if that block is printed in italic type.\n"
    "Return STRICT JSON only, no prose:\n"
    '{"blocks":[{"i":<index>,"text":"<corrected>","italic":<bool>}, ...]}\n'
    "Return one entry per input block, same indices.\n\n"
    "BLOCKS:\n"
)


def _encode_image(gray: np.ndarray, max_px: int) -> str:
    h, w = gray.shape[:2]
    scale = max_px / max(h, w)
    if scale < 1:
        gray = cv2.resize(gray, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", gray, [cv2.IMWRITE_JPEG_QUALITY, 85])
    if not ok:
        raise RuntimeError("failed to JPEG-encode page image")
    return base64.b64encode(buf.tobytes()).decode("ascii")


def _blocks_payload(ocr: OCRResult) -> str:
    lines = []
    for i, b in enumerate(ocr.blocks):
        x0, y0, x1, y1 = b.bbox
        bx = (
            round(1000 * x0 / ocr.img_w), round(1000 * y0 / ocr.img_h),
            round(1000 * x1 / ocr.img_w), round(1000 * y1 / ocr.img_h),
        )
        text = b.text.replace("\n", " ")
        lines.append(f"[{i}] bbox={bx} text={text!r}")
    return "\n".join(lines)


def _call_api(prov: Provider, key: str, model: str, prompt: str, img_b64: str, timeout: float) -> str:
    import requests

    payload = {
        "model": model,
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"},
                    },
                ],
            }
        ],
    }
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    resp = requests.post(prov.url, headers=headers, json=payload, timeout=timeout)
    if resp.status_code != 200:
        raise RuntimeError(f"{prov.name} API {resp.status_code}: {resp.text[:300]}")
    data = resp.json()
    return data["choices"][0]["message"]["content"]


def _apply(ocr: OCRResult, content: str) -> int:
    """Parse the model JSON and write corrections onto blocks. Returns #updated.

    Tolerates the shapes models actually return: a top-level list, or a dict
    wrapping the list under ``blocks``/``data``/``results``.
    """
    obj = json.loads(_strip_fences(content))
    if isinstance(obj, list):
        items = obj
    elif isinstance(obj, dict):
        items = obj.get("blocks") or obj.get("data") or obj.get("results") or []
    else:
        items = []

    updated = 0
    for pos, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        idx = item.get("i", item.get("index", pos))  # fall back to array position
        try:
            i = int(idx)
        except (TypeError, ValueError):
            continue
        if not (0 <= i < len(ocr.blocks)):
            continue
        text = item.get("text")
        if isinstance(text, str) and text.strip():
            ocr.blocks[i].override_text = text.strip()
            updated += 1
        if isinstance(item.get("italic"), bool):
            ocr.blocks[i].italic = item["italic"]
    return updated


def _strip_fences(s: str) -> str:
    s = s.strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[-1]
        if s.endswith("```"):
            s = s.rsplit("```", 1)[0]
    return s.strip()


def refine_blocks(gray: np.ndarray, ocr: OCRResult, cfg: VisionConfig) -> int:
    """Correct each block's text via a hosted vision model. Returns #blocks updated.

    On any failure the OCR result is left untouched (Tesseract text is kept) and
    the exception propagates to the caller to decide whether to warn-and-continue.
    """
    if not ocr.blocks:
        return 0
    prov, key = cfg.resolved()
    model = cfg.model or prov.model
    img_b64 = _encode_image(gray, cfg.max_px)
    prompt = _PROMPT + _blocks_payload(ocr)
    content = _call_api(prov, key, model, prompt, img_b64, cfg.timeout)
    return _apply(ocr, content)
