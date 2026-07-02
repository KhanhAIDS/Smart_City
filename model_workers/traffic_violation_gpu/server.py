from __future__ import annotations

import io
import os

import torch
from fastapi import FastAPI, Request
from huggingface_hub import hf_hub_download
from PIL import Image
from ultralytics import YOLO

app = FastAPI()

vehicle_model: YOLO | None = None
helmet_model: YOLO | None = None

VEHICLE_MODEL_PATH = os.getenv("TRAFFIC_VEHICLE_MODEL", "yolo11m.pt")
HELMET_MODEL_PATH = os.getenv("TRAFFIC_HELMET_MODEL", "/opt/hf/helmet_yolo.pt")
HELMET_REPO = os.getenv("TRAFFIC_HELMET_REPO", "")
HELMET_FILE = os.getenv("TRAFFIC_HELMET_FILE", "")
DETECTION_THRESHOLD = float(os.getenv("DETECTION_THRESHOLD", "0.25"))

VEHICLE_CLASSES = {"car", "truck", "bus", "motorcycle", "bicycle"}

# Try to map common helmet dataset classes to our schema
def _normalize_helmet_class(name: str) -> str | None:
    low = name.lower().replace(" ", "_")
    if "no_helmet" in low or "without" in low or "unhelmeted" in low:
        return "no_helmet"
    if "helmet" in low:
        return "helmet"
    if "rider" in low or "person" in low:
        return "rider"
    if "motorcycle" in low or "bike" in low:
        return "motorcycle"
    return None

def _resolve_helmet_model() -> str:
    if HELMET_REPO and HELMET_FILE:
        try:
            path = hf_hub_download(repo_id=HELMET_REPO, filename=HELMET_FILE)
            print(f"[traffic_worker] Downloaded helmet model from HF: {path}", flush=True)
            return path
        except Exception as exc:
            print(f"[traffic_worker] HF download failed: {exc}", flush=True)
    return HELMET_MODEL_PATH

@app.on_event("startup")
def load_models() -> None:
    global vehicle_model, helmet_model
    cuda_ok = torch.cuda.is_available()
    device_name = torch.cuda.get_device_name(0) if cuda_ok else "CPU"
    print(f"[traffic_worker] torch={torch.__version__} cuda={cuda_ok} device={device_name}", flush=True)
    if not cuda_ok:
        raise RuntimeError("CUDA not available; refusing CPU fallback")
    
    print(f"[traffic_worker] Loading vehicle model {VEHICLE_MODEL_PATH}", flush=True)
    vehicle_model = YOLO(VEHICLE_MODEL_PATH)
    vehicle_model.to("cuda")

    helmet_path = _resolve_helmet_model()
    print(f"[traffic_worker] Loading helmet model {helmet_path}", flush=True)
    if os.path.exists(helmet_path):
        helmet_model = YOLO(helmet_path)
        helmet_model.to("cuda")
    else:
        print(f"[traffic_worker] WARNING: Helmet model not found at {helmet_path}", flush=True)

class Metrics:
    requests = 0
    failures = 0
    vehicle_count = 0
    no_helmet_count = 0

def _get_model_id() -> str:
    v_id = os.path.basename(VEHICLE_MODEL_PATH)
    h_id = os.path.basename(HELMET_MODEL_PATH) if helmet_model else "no-helmet-model"
    return f"{v_id}+{h_id}"

@app.get("/metrics")
def metrics() -> dict:
    return {
        "requests": Metrics.requests,
        "failures": Metrics.failures,
        "vehicle_count": Metrics.vehicle_count,
        "no_helmet_count": Metrics.no_helmet_count,
        "model": _get_model_id(),
    }

@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "cuda": torch.cuda.is_available(),
        "device": torch.cuda.get_device_name(0) if torch.cuda.is_available() else "CPU",
        "model": _get_model_id(),
        "vehicle_model_loaded": vehicle_model is not None,
        "helmet_model_loaded": helmet_model is not None,
    }

def _empty(error: str | None = None) -> dict:
    if error:
        Metrics.failures += 1
    out = {
        "schema": "traffic_violation.v1",
        "vehicles": [],
        "riders": [],
        "helmets": [],
        "no_helmets": [],
        "model": _get_model_id()
    }
    if error:
        out["error"] = error
    return out

@app.post("/detect")
async def detect(request: Request) -> dict:
    Metrics.requests += 1
    try:
        raw = await request.body()
    except Exception as exc:
        return _empty(f"body read failed: {exc}")
    if not raw:
        return _empty("empty body")

    try:
        image = Image.open(io.BytesIO(raw)).convert("RGB")
    except Exception as exc:
        return _empty(f"decode failed: {exc}")

    vehicles = []
    riders = []
    helmets = []
    no_helmets = []

    # Vehicle detection
    if vehicle_model:
        try:
            v_results = vehicle_model.predict(image, conf=DETECTION_THRESHOLD, device="cuda", verbose=False)
            v_names = vehicle_model.names
            for res in v_results:
                if res.boxes is None: continue
                xyxy = res.boxes.xyxy.cpu().tolist()
                conf = res.boxes.conf.cpu().tolist()
                cls = res.boxes.cls.cpu().tolist()
                for box, c, k in zip(xyxy, conf, cls):
                    kind = v_names[int(k)].lower()
                    if kind in VEHICLE_CLASSES:
                        vehicles.append({
                            "bbox": [float(box[0]), float(box[1]), float(box[2]), float(box[3])],
                            "confidence": float(c),
                            "class": kind,
                            "source": "vehicle"
                        })
                        Metrics.vehicle_count += 1
        except Exception as exc:
            return _empty(f"vehicle inference failed: {exc}")

    # Helmet detection
    if helmet_model:
        try:
            h_results = helmet_model.predict(image, conf=DETECTION_THRESHOLD, device="cuda", verbose=False)
            h_names = helmet_model.names
            for res in h_results:
                if res.boxes is None: continue
                xyxy = res.boxes.xyxy.cpu().tolist()
                conf = res.boxes.conf.cpu().tolist()
                cls = res.boxes.cls.cpu().tolist()
                for box, c, k in zip(xyxy, conf, cls):
                    raw_name = h_names[int(k)]
                    norm_name = _normalize_helmet_class(raw_name)
                    if not norm_name: continue
                    obj = {
                        "bbox": [float(box[0]), float(box[1]), float(box[2]), float(box[3])],
                        "confidence": float(c),
                        "class": norm_name,
                        "source": "helmet"
                    }
                    if norm_name == "rider":
                        riders.append(obj)
                    elif norm_name == "helmet":
                        helmets.append(obj)
                    elif norm_name == "no_helmet":
                        no_helmets.append(obj)
                        Metrics.no_helmet_count += 1
                    elif norm_name == "motorcycle":
                        # Some helmet models output motorcycle too. We can append them to vehicles if not redundant, or just ignore. 
                        # Let's just output them as riders so perception worker can associate them if it wants.
                        # Wait, the plan schema allows `class:"car|truck|bus|motorcycle|rider|helmet|no_helmet"`.
                        # If the helmet model outputs motorcycle, let's treat it as a vehicle from helmet source, or rider.
                        # Let's map it to vehicles.
                        vehicles.append(obj)
        except Exception as exc:
            return _empty(f"helmet inference failed: {exc}")

    return {
        "schema": "traffic_violation.v1",
        "vehicles": vehicles,
        "riders": riders,
        "helmets": helmets,
        "no_helmets": no_helmets,
        "model": _get_model_id(),
    }
