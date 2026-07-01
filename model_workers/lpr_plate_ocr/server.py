from __future__ import annotations

import io
import os
import re
import statistics

import cv2
import numpy as np
import torch
from fastapi import FastAPI, Request
from fast_plate_ocr import LicensePlateRecognizer
from huggingface_hub import hf_hub_download, list_repo_files
from PIL import Image
from ultralytics import YOLO

DEFAULT_DETECTORS = [
    ("morsetechlab/yolov11-license-plate-detection", "license-plate-finetune-v1s.pt"),
    ("MKgoud/License-Plate-Recognizer", "LP-detection.pt"),
    ("Koushim/yolov8-license-plate-detection", "best.pt"),
]


def _candidates() -> list[tuple[str, str | None]]:
    repo = os.getenv("LPR_DETECTOR_REPO", "").strip()
    if repo:
        return [(repo, os.getenv("LPR_DETECTOR_FILE", "").strip() or None)] + DEFAULT_DETECTORS
    return DEFAULT_DETECTORS


CANDIDATES = _candidates()
OCR_MODEL = os.getenv("LPR_OCR_MODEL", "cct-xs-v2-global-model")
DETECTOR_CONFIDENCE = float(os.getenv("LPR_DETECTOR_CONFIDENCE", "0.35"))
DETECTOR_IMGSZ = int(os.getenv("LPR_DETECTOR_IMGSZ", "640"))
MAX_PLATES = int(os.getenv("LPR_MAX_PLATES", "8"))
PLATE_CLASSES = {
    int(c) for c in os.getenv("LPR_PLATE_CLASSES", "").replace(" ", "").split(",") if c
}
PLATE_CHARS = re.compile(r"[^A-Z0-9]")

try:
    cv2.setNumThreads(int(os.getenv("OPENCV_NUM_THREADS", "2")))
except Exception:
    pass

app = FastAPI()
detector: YOLO | None = None
ocr: LicensePlateRecognizer | None = None
detector_id = "lpr-detector"
MODEL_ID = "lpr-detector+" + OCR_MODEL


class Metrics:
    requests = 0
    failures = 0


def normalize_text(value) -> str:
    if not value:
        return ""
    return PLATE_CHARS.sub("", str(value).upper())


def mean_prob(char_probs) -> float:
    if char_probs is None:
        return 0.0
    try:
        arr = np.asarray(char_probs, dtype=float).ravel()
        return float(arr.mean()) if arr.size else 0.0
    except Exception:
        return 0.0


def _resolve_weight(repo: str, filename: str | None) -> str | None:
    if filename:
        try:
            path = hf_hub_download(repo_id=repo, filename=filename)
            print(f"[lpr_worker] {repo} weights -> {filename}", flush=True)
            return path
        except Exception as exc:
            print(f"[lpr_worker] download {repo}/{filename} failed: {exc}", flush=True)
            return None
    try:
        files = list_repo_files(repo)
    except Exception as exc:
        print(f"[lpr_worker] list_repo_files({repo}) failed: {exc}", flush=True)
        return None
    pt_files = [f for f in files if f.endswith(".pt")]
    if not pt_files:
        print(f"[lpr_worker] no .pt in {repo}: {files}", flush=True)
        return None
    pt_files.sort(key=lambda f: ("best" not in f.lower(), len(f), f))
    for chosen in pt_files:
        try:
            path = hf_hub_download(repo_id=repo, filename=chosen)
            print(f"[lpr_worker] {repo} weights -> {chosen}", flush=True)
            return path
        except Exception as exc:
            print(f"[lpr_worker] download {repo}/{chosen} failed: {exc}", flush=True)
    return None


def _load_detector() -> tuple[YOLO, str]:
    for repo, filename in CANDIDATES:
        path = _resolve_weight(repo, filename)
        if not path:
            continue
        try:
            model = YOLO(path)
            model.to("cuda")
            model.predict(
                np.zeros((DETECTOR_IMGSZ, DETECTOR_IMGSZ, 3), np.uint8),
                imgsz=DETECTOR_IMGSZ,
                conf=DETECTOR_CONFIDENCE,
                device="cuda",
                verbose=False,
            )
            names = model.names if isinstance(model.names, dict) else dict(enumerate(model.names))
            print(f"[lpr_worker] loaded detector {repo} names={names}", flush=True)
            return model, repo.split("/")[-1]
        except Exception as exc:
            print(f"[lpr_worker] YOLO load failed for {repo}: {exc}", flush=True)
    raise RuntimeError("no license-plate detector weights could be downloaded/loaded")


@app.on_event("startup")
def load_models() -> None:
    global detector, ocr, detector_id, MODEL_ID
    cuda_ok = torch.cuda.is_available()
    device_name = torch.cuda.get_device_name(0) if cuda_ok else "CPU"
    print(f"[lpr_worker] torch={torch.__version__} cuda_available={cuda_ok} device={device_name}", flush=True)
    if not cuda_ok:
        raise RuntimeError("CUDA not available; refusing CPU fallback")
    detector, detector_id = _load_detector()
    ocr = LicensePlateRecognizer(hub_ocr_model=OCR_MODEL, device="cpu")
    MODEL_ID = f"{detector_id}+{OCR_MODEL}"
    print(f"[lpr_worker] ready model={MODEL_ID} conf={DETECTOR_CONFIDENCE} imgsz={DETECTOR_IMGSZ}", flush=True)


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok" if detector is not None and ocr is not None else "loading",
        "cuda": torch.cuda.is_available(),
        "device": torch.cuda.get_device_name(0) if torch.cuda.is_available() else "CPU",
        "model": MODEL_ID,
    }


@app.get("/metrics")
def metrics() -> dict:
    return {"requests": Metrics.requests, "failures": Metrics.failures, "model": MODEL_ID}


def empty(error: str | None = None) -> dict:
    if error:
        Metrics.failures += 1
    out = {"schema": "lpr.v1", "plate_count": 0, "plates": [], "model": MODEL_ID}
    if error:
        out["error"] = error
    return out


def run_ocr(crop) -> tuple[str, float]:
    if crop is None or crop.size == 0:
        return "", 0.0
    try:
        pred = ocr.run_one(crop, return_confidence=True)
    except Exception as exc:
        print(f"[lpr_worker] ocr failed: {exc}", flush=True)
        return "", 0.0
    return normalize_text(pred.plate), mean_prob(pred.char_probs)


@app.post("/detect")
async def detect(request: Request) -> dict:
    Metrics.requests += 1
    try:
        raw = await request.body()
    except Exception as exc:
        return empty(f"body read failed: {exc}")
    if not raw:
        return empty("empty body")

    try:
        image = Image.open(io.BytesIO(raw)).convert("RGB")
        frame = cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)
    except Exception as exc:
        return empty(f"decode failed: {exc}")

    try:
        result = detector.predict(
            frame,
            imgsz=DETECTOR_IMGSZ,
            conf=DETECTOR_CONFIDENCE,
            device="cuda",
            verbose=False,
        )[0]
    except Exception as exc:
        return empty(f"inference failed: {exc}")

    h, w = frame.shape[:2]
    boxes = result.boxes
    dets = []
    if boxes is not None:
        for b in boxes:
            cls = int(b.cls[0]) if b.cls is not None else -1
            if PLATE_CLASSES and cls not in PLATE_CLASSES:
                continue
            x1, y1, x2, y2 = (float(v) for v in b.xyxy[0].tolist())
            dets.append((float(b.conf[0]), x1, y1, x2, y2))
    dets.sort(key=lambda d: d[0], reverse=True)

    plates = []
    for det_conf, x1, y1, x2, y2 in dets[:MAX_PLATES]:
        cx1, cy1 = max(0, int(x1)), max(0, int(y1))
        cx2, cy2 = min(w, int(x2)), min(h, int(y2))
        if cx2 <= cx1 or cy2 <= cy1:
            continue
        text, ocr_conf = run_ocr(frame[cy1:cy2, cx1:cx2])
        plates.append(
            {
                "bbox": [x1, y1, x2, y2],
                "det_confidence": det_conf,
                "text": text,
                "raw_text": text,
                "ocr_confidence": ocr_conf,
                "confidence": min(det_conf, ocr_conf) if text else det_conf,
            }
        )

    return {"schema": "lpr.v1", "plate_count": len(plates), "plates": plates, "model": MODEL_ID}
