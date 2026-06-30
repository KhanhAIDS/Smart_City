from __future__ import annotations

import io
import os

import torch
from fastapi import FastAPI, Request
from PIL import Image
from rfdetr import RFDETRLarge
from rfdetr.util.coco_classes import COCO_CLASSES

MODEL_NAME = "rfdetr-large"
DETECTION_THRESHOLD = float(os.getenv("DETECTION_THRESHOLD", "0.5"))

app = FastAPI()

model: RFDETRLarge | None = None
person_id: int = 0


@app.on_event("startup")
def load_model() -> None:
    global model, person_id
    cuda_ok = torch.cuda.is_available()
    device_name = torch.cuda.get_device_name(0) if cuda_ok else "CPU"
    print(f"[gpu_worker] torch={torch.__version__} cuda_available={cuda_ok} "
          f"device={device_name}", flush=True)
    if not cuda_ok:
        raise RuntimeError("CUDA not available; refusing CPU fallback")
    model = RFDETRLarge()
    items = (
        COCO_CLASSES.items()
        if isinstance(COCO_CLASSES, dict)
        else enumerate(COCO_CLASSES)
    )
    person_id = next(
        (cid for cid, name in items if str(name).lower() == "person"),
        0,
    )
    print(f"[gpu_worker] RFDETRLarge loaded on {device_name}; "
          f"person_id={person_id} threshold={DETECTION_THRESHOLD}", flush=True)


class Metrics:
    requests = 0
    failures = 0

@app.get("/metrics")
def metrics() -> dict:
    return {
        "requests": Metrics.requests,
        "failures": Metrics.failures,
        "model": MODEL_NAME,
    }

@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "cuda": torch.cuda.is_available(),
        "device": torch.cuda.get_device_name(0)
        if torch.cuda.is_available()
        else "CPU",
        "model": MODEL_NAME,
    }


@app.post("/detect")
async def detect(request: Request) -> dict:
    Metrics.requests += 1
    try:
        raw = await request.body()
    except Exception as exc:  # noqa: BLE001
        Metrics.failures += 1
        return {
            "person_count": 0,
            "detections": [],
            "model": MODEL_NAME,
            "error": f"body read failed: {exc}",
        }

    if not raw:
        Metrics.failures += 1
        return {
            "person_count": 0,
            "detections": [],
            "model": MODEL_NAME,
            "error": "empty body",
        }

    try:
        image = Image.open(io.BytesIO(raw)).convert("RGB")
    except Exception as exc:  # noqa: BLE001
        Metrics.failures += 1
        return {
            "person_count": 0,
            "detections": [],
            "model": MODEL_NAME,
            "error": f"decode failed: {exc}",
        }

    try:
        detections = model.predict(image, threshold=DETECTION_THRESHOLD)
    except Exception as exc:  # noqa: BLE001
        Metrics.failures += 1
        return {
            "person_count": 0,
            "detections": [],
            "model": MODEL_NAME,
            "error": f"inference failed: {exc}",
        }

    persons = []
    try:
        xyxy = detections.xyxy
        confidence = detections.confidence
        class_id = detections.class_id
        for i in range(len(detections)):
            if int(class_id[i]) != person_id:
                continue
            box = xyxy[i]
            persons.append(
                {
                    "bbox": [
                        float(box[0]),
                        float(box[1]),
                        float(box[2]),
                        float(box[3]),
                    ],
                    "confidence": float(confidence[i]),
                }
            )
    except Exception as exc:  # noqa: BLE001
        Metrics.failures += 1
        return {
            "person_count": 0,
            "detections": [],
            "model": MODEL_NAME,
            "error": f"postprocess failed: {exc}",
        }

    return {
        "person_count": len(persons),
        "detections": persons,
        "model": MODEL_NAME,
    }
