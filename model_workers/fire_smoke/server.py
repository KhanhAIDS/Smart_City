from __future__ import annotations

import io
import os

import torch
from fastapi import FastAPI, Request
from huggingface_hub import hf_hub_download, list_repo_files
from PIL import Image
from ultralytics import YOLO

DEFAULT_REPOS = [
    "JJUNHYEOK/yolov8n_wildfire_detection",
    "AarishBangash/FIre-smoke-model",
    "rabahdev/fire-smoke-yolov8n",
    "odiug77/wildfire-smoke-fire",
]
CANDIDATE_REPOS = [
    r.strip() for r in os.getenv("FIRE_MODEL_REPOS", ",".join(DEFAULT_REPOS)).split(",")
    if r.strip()
]
DETECTION_THRESHOLD = float(os.getenv("DETECTION_THRESHOLD", "0.25"))
IMG_SIZE = int(os.getenv("FIRE_IMG_SIZE", "640"))

app = FastAPI()

model: YOLO | None = None
model_id: str = "fire-smoke"
class_kind: dict[int, str] = {}


def _resolve_weight(repo: str) -> str | None:
    try:
        files = list_repo_files(repo)
    except Exception as exc:  # noqa: BLE001
        print(f"[fire_worker] list_repo_files({repo}) failed: {exc}", flush=True)
        return None
    pt_files = [f for f in files if f.endswith(".pt")]
    if not pt_files:
        print(f"[fire_worker] no .pt in {repo}: {files}", flush=True)
        return None
    pt_files.sort(key=lambda f: ("best" not in f.lower(), len(f)))
    for chosen in pt_files:
        try:
            path = hf_hub_download(repo_id=repo, filename=chosen)
            print(f"[fire_worker] {repo} weights -> {chosen}", flush=True)
            return path
        except Exception as exc:  # noqa: BLE001
            print(f"[fire_worker] download {repo}/{chosen} failed: {exc}", flush=True)
    return None


def _classify(name: str) -> str:
    low = name.lower()
    if "smoke" in low:
        return "smoke"
    if "fire" in low or "flame" in low:
        return "fire"
    return "other"


def _load() -> YOLO:
    for repo in CANDIDATE_REPOS:
        path = _resolve_weight(repo)
        if not path:
            continue
        try:
            m = YOLO(path)
            names = m.names if isinstance(m.names, dict) else dict(enumerate(m.names))
            kinds = {_classify(str(n)) for n in names.values()}
            if "fire" not in kinds and "smoke" not in kinds:
                print(f"[fire_worker] {repo} has no fire/smoke class ({names}); skipping",
                      flush=True)
                continue
            print(f"[fire_worker] loaded {repo} names={names}", flush=True)
            globals()["model_id"] = repo.split("/")[-1].lower()
            return m
        except Exception as exc:  # noqa: BLE001
            print(f"[fire_worker] YOLO load failed for {repo}: {exc}", flush=True)
    raise RuntimeError("no fire/smoke weights could be downloaded/loaded")


@app.on_event("startup")
def load_model() -> None:
    global model, class_kind
    cuda_ok = torch.cuda.is_available()
    device_name = torch.cuda.get_device_name(0) if cuda_ok else "CPU"
    print(f"[fire_worker] torch={torch.__version__} cuda_available={cuda_ok} "
          f"device={device_name}", flush=True)
    if not cuda_ok:
        raise RuntimeError("CUDA not available; refusing CPU fallback")
    model = _load()
    model.to("cuda")
    names = model.names if isinstance(model.names, dict) else dict(enumerate(model.names))
    class_kind = {int(cid): _classify(str(n)) for cid, n in names.items()}
    print(f"[fire_worker] class_kind={class_kind} threshold={DETECTION_THRESHOLD} "
          f"imgsz={IMG_SIZE}", flush=True)


class Metrics:
    requests = 0
    failures = 0

@app.get("/metrics")
def metrics() -> dict:
    return {
        "requests": Metrics.requests,
        "failures": Metrics.failures,
        "model": model_id,
    }

@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "cuda": torch.cuda.is_available(),
        "device": torch.cuda.get_device_name(0)
        if torch.cuda.is_available()
        else "CPU",
        "model": model_id,
    }


def _empty(error: str | None = None) -> dict:
    if error:
        Metrics.failures += 1
    out = {"detections": [], "fire_count": 0, "smoke_count": 0, "model": model_id}
    if error:
        out["error"] = error
    return out


@app.post("/detect")
async def detect(request: Request) -> dict:
    Metrics.requests += 1
    try:
        raw = await request.body()
    except Exception as exc:  # noqa: BLE001
        return _empty(f"body read failed: {exc}")
    if not raw:
        return _empty("empty body")

    try:
        image = Image.open(io.BytesIO(raw)).convert("RGB")
    except Exception as exc:  # noqa: BLE001
        return _empty(f"decode failed: {exc}")

    try:
        results = model.predict(
            image,
            conf=DETECTION_THRESHOLD,
            imgsz=IMG_SIZE,
            device="cuda",
            verbose=False,
        )
    except Exception as exc:  # noqa: BLE001
        return _empty(f"inference failed: {exc}")

    detections = []
    fire_count = 0
    smoke_count = 0
    try:
        for res in results:
            boxes = res.boxes
            if boxes is None:
                continue
            xyxy = boxes.xyxy.cpu().tolist()
            conf = boxes.conf.cpu().tolist()
            cls = boxes.cls.cpu().tolist()
            for box, c, k in zip(xyxy, conf, cls):
                kind = class_kind.get(int(k), "other")
                if kind == "other":
                    continue
                detections.append(
                    {
                        "bbox": [float(box[0]), float(box[1]),
                                 float(box[2]), float(box[3])],
                        "confidence": float(c),
                        "class": kind,
                    }
                )
                if kind == "fire":
                    fire_count += 1
                else:
                    smoke_count += 1
    except Exception as exc:  # noqa: BLE001
        return _empty(f"postprocess failed: {exc}")

    return {
        "detections": detections,
        "fire_count": fire_count,
        "smoke_count": smoke_count,
        "model": model_id,
    }
