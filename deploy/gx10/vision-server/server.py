"""
Babo vision worker — /vision/describe for Visual Cortex (LAN or via Nest proxy).

Matches nls.tools.visual_model.RemoteVLMBackend (form field image_base64).
"""

from __future__ import annotations

import base64
import io
import os
import time
from typing import Optional

from fastapi import FastAPI, Form, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from PIL import Image

VISION_SECRET = os.environ.get("VISION_SECRET", "") or os.environ.get(
    "BABO_VISION_SECRET", ""
)
VISION_MODEL = os.environ.get("VISION_MODEL", "moondream2")
VISION_DEVICE = os.environ.get("VISION_DEVICE", "cuda")
PORT = int(os.environ.get("VISION_PORT", "8443"))

app = FastAPI(title="Babo Vision Worker", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_model = None
_model_id = VISION_MODEL


def _check_secret(header: Optional[str]) -> None:
    if VISION_SECRET and header != VISION_SECRET:
        raise HTTPException(401, "Invalid X-GPU-Worker-Secret")


def _load_model():
    global _model, _model_id
    if _model is not None:
        return _model
    print(
        f"[vision] loading model={VISION_MODEL!r} device={VISION_DEVICE!r}",
        flush=True,
    )
    t0 = time.time()
    if VISION_MODEL.startswith("moondream"):
        from transformers import AutoModelForCausalLM, AutoTokenizer

        mid = "vikhyatk/moondream2"
        revision = "2024-08-26"
        tok = AutoTokenizer.from_pretrained(
            mid, revision=revision, trust_remote_code=True
        )
        model = AutoModelForCausalLM.from_pretrained(
            mid,
            revision=revision,
            trust_remote_code=True,
            device_map=VISION_DEVICE if VISION_DEVICE != "cpu" else None,
        )
        if VISION_DEVICE == "cpu":
            model = model.to("cpu")
        _model = (model, tok)
        _model_id = mid
    else:
        from transformers import AutoModelForVision2Seq, AutoProcessor

        mid = "HuggingFaceTB/SmolVLM-256M-Instruct"
        proc = AutoProcessor.from_pretrained(mid)
        model = AutoModelForVision2Seq.from_pretrained(
            mid,
            device_map=VISION_DEVICE if VISION_DEVICE != "cpu" else None,
        )
        if VISION_DEVICE == "cpu":
            model = model.to("cpu")
        _model = (model, proc)
        _model_id = mid
    print(f"[vision] loaded in {time.time() - t0:.1f}s", flush=True)
    return _model


def _describe(image: Image.Image) -> tuple[str, str]:
    bundle = _load_model()
    if VISION_MODEL.startswith("moondream"):
        model, tok = bundle
        enc = model.encode_image(image)
        desc = model.answer_question(
            enc,
            "Describe the screen in one concise sentence.",
            tok,
        )
        ocr = model.answer_question(
            enc,
            "List visible text briefly.",
            tok,
        )
        return str(desc or ""), str(ocr or "")
    model, proc = bundle
    import torch

    prompt = (
        "<image>Describe the active application and main content in one sentence."
    )
    inputs = proc(text=prompt, images=image, return_tensors="pt")
    device = next(model.parameters()).device
    inputs = {k: v.to(device) for k, v in inputs.items()}
    with torch.no_grad():
        out = model.generate(**inputs, max_new_tokens=80)
    desc = proc.decode(out[0], skip_special_tokens=True)
    return desc.strip(), ""


@app.get("/health")
def health(
    x_gpu_worker_secret: Optional[str] = Header(None, alias="X-GPU-Worker-Secret"),
):
    _check_secret(x_gpu_worker_secret)
    return {
        "ok": True,
        "model": _model_id,
        "device": VISION_DEVICE,
        "loaded": _model is not None,
    }


@app.post("/vision/describe")
def vision_describe(
    image_base64: str = Form(...),
    x_gpu_worker_secret: Optional[str] = Header(None, alias="X-GPU-Worker-Secret"),
):
    _check_secret(x_gpu_worker_secret)
    try:
        raw = base64.b64decode(image_base64)
        image = Image.open(io.BytesIO(raw)).convert("RGB")
    except Exception as exc:
        raise HTTPException(400, f"Invalid image_base64: {exc}") from exc
    t0 = time.time()
    description, ocr_text = _describe(image)
    return {
        "description": description,
        "ocr_text": ocr_text,
        "latency_ms": int((time.time() - t0) * 1000),
    }


@app.post("/vision/ask")
def vision_ask(
    image_base64: str = Form(...),
    question: str = Form("What is on this screen?"),
    x_gpu_worker_secret: Optional[str] = Header(None, alias="X-GPU-Worker-Secret"),
):
    _check_secret(x_gpu_worker_secret)
    try:
        raw = base64.b64decode(image_base64)
        image = Image.open(io.BytesIO(raw)).convert("RGB")
    except Exception as exc:
        raise HTTPException(400, f"Invalid image_base64: {exc}") from exc
    bundle = _load_model()
    if VISION_MODEL.startswith("moondream"):
        model, tok = bundle
        enc = model.encode_image(image)
        answer = model.answer_question(enc, question, tok)
    else:
        answer = _describe(image)[0]
    return {"answer": str(answer or "")}
