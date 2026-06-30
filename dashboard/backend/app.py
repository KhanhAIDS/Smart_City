import os
import json
import asyncio
import logging
import base64
import io
import time
from typing import List, Optional

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from PIL import Image
from pydantic import BaseModel
import httpx
import paho.mqtt.client as mqtt
from smart_city_common.clustering import compute_crowd_clusters
from datetime import datetime, timezone

from .frigate_proxy import proxy_frigate_api, proxy_frigate_ws

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

MQTT_HOST = os.getenv("MQTT_HOST", "mqtt")
MQTT_PORT = int(os.getenv("MQTT_PORT", 1883))
FRIGATE_API = os.getenv("FRIGATE_API", "http://frigate:5000")
ALERT_TOPIC_WILDCARD = os.getenv("ALERT_TOPIC_WILDCARD", "ai_worker/alerts/#")
EVENTS_TOPIC = os.getenv("EVENTS_TOPIC", "frigate/events")
STALE_SECONDS = int(os.getenv("STALE_SECONDS", 30))
BENCHMARK_ENDPOINTS_RAW = os.getenv("BENCHMARK_ENDPOINTS", "[]")

try:
    BENCHMARK_ENDPOINTS = json.loads(BENCHMARK_ENDPOINTS_RAW)
except:
    BENCHMARK_ENDPOINTS = []

CLUSTER_SIZE_RATIO_MIN = float(os.getenv("CLUSTER_SIZE_RATIO_MIN", 0.8))
CLUSTER_DISTANCE_FACTOR = float(os.getenv("CLUSTER_DISTANCE_FACTOR", 1.2))
CLUSTER_CROWD_THRESHOLD = int(os.getenv("CLUSTER_CROWD_THRESHOLD", 3))
REALTIME_OVERLAY_TTL_MS = int(os.getenv("REALTIME_OVERLAY_TTL_MS", "1200"))
REALTIME_SUPPRESS_SECONDS = max(REALTIME_OVERLAY_TTL_MS / 1000.0, 2.0)

app = FastAPI()

class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: dict):
        dead_connections = []
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except Exception:
                dead_connections.append(connection)
        for dead in dead_connections:
            self.disconnect(dead)

manager = ConnectionManager()

def on_mqtt_connect(client, userdata, flags, rc):
    logger.info(f"Connected to MQTT broker with code {rc}")
    topics = [
        ALERT_TOPIC_WILDCARD,
        EVENTS_TOPIC,
        "stream_core/frames/#",
        "perception/objects/#",
        "perception/tracks/#",
        "perception/crowd/#",
        "perception/fire_smoke/#",
        "perception/alerts/#",
    ]
    for topic in topics:
        client.subscribe(topic)


last_realtime_fire_seen = {}


def infer_realtime_alert_type(payload):
    if "person_count" in payload or "cluster_bbox" in payload or "cluster_member_indices" in payload:
        return "crowd_alert"
    if "object_id" in payload or "dwell_time" in payload:
        return "loitering_alert"
    if "fire_count" in payload or "smoke_count" in payload:
        return "fire_smoke_alert"
    return "realtime_alert"


def map_mqtt_topic(topic, payload):
    if topic.startswith("stream_core/frames/"):
        return "realtime_frame"
    if topic.startswith("perception/objects/"):
        return "realtime_objects"
    if topic.startswith("perception/tracks/"):
        return "realtime_tracks"
    if topic.startswith("perception/crowd/"):
        return "realtime_crowd"
    if topic.startswith("perception/fire_smoke/"):
        camera = payload.get("camera") or topic.rsplit("/", 1)[-1]
        last_realtime_fire_seen[camera] = time.monotonic()
        return "realtime_fire_smoke"
    if topic.startswith("perception/alerts/"):
        if topic.startswith("perception/alerts/fire_smoke"):
            camera = payload.get("camera")
            if camera:
                last_realtime_fire_seen[camera] = time.monotonic()
        return "realtime_alert"
    if topic.startswith("ai_worker/alerts/fire_smoke"):
        camera = payload.get("camera")
        if camera and time.monotonic() - last_realtime_fire_seen.get(camera, 0.0) <= REALTIME_SUPPRESS_SECONDS:
            return "unknown"
        return "fire_smoke_alert"
    if topic.startswith("ai_worker/alerts/loitering"):
        return "loitering_alert"
    if topic.startswith("ai_worker/alerts/"):
        return "crowd_alert"
    if topic == EVENTS_TOPIC:
        return "frigate_event"
    return "unknown"


def on_mqtt_message(client, userdata, msg):
    topic = msg.topic
    try:
        payload = json.loads(msg.payload.decode())
    except Exception:
        return

    msg_type = map_mqtt_topic(topic, payload)
    if msg_type == "unknown":
        return

    Metrics.mqtt_messages += 1
    if loop is None:
        return

    async def process_and_broadcast():
        Metrics.ws_broadcasts += 1
        await manager.broadcast({"type": msg_type, "data": payload})

    asyncio.run_coroutine_threadsafe(process_and_broadcast(), loop)


class Metrics:
    mqtt_messages = 0
    ws_broadcasts = 0


mqtt_client = mqtt.Client()
mqtt_client.on_connect = on_mqtt_connect
mqtt_client.on_message = on_mqtt_message

loop = None

@app.on_event("startup")
async def startup_event():
    global loop
    loop = asyncio.get_running_loop()
    try:
        mqtt_client.connect(MQTT_HOST, MQTT_PORT, 60)
        mqtt_client.loop_start()
    except Exception as e:
        logger.error(f"MQTT Connect failed: {e}")

@app.on_event("shutdown")
async def shutdown_event():
    mqtt_client.loop_stop()
    mqtt_client.disconnect()



@app.get("/dashboard/config")
async def get_config():
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{FRIGATE_API}/api/config", timeout=10)
            data = resp.json()
            cameras_data = data.get("cameras", {})
            
            cameras = []
            for name, c in cameras_data.items():
                if c.get("enabled", True):
                    detect = c.get("detect", {})
                    w = detect.get("width", 1280)
                    h = detect.get("height", 720)
                    cameras.append({
                        "name": name,
                        "enabled": True,
                        "width": w,
                        "height": h
                    })
    except Exception as e:
        logger.error(f"Error fetching frigate config: {e}")
        cameras = []

    return {
        "stale_seconds": STALE_SECONDS,
        "alert_topic": os.getenv("ALERT_TOPIC", "ai_worker/alerts/crowd"),
        "cameras": cameras
    }

class BenchmarkRequest(BaseModel):
    camera: Optional[str] = None

@app.post("/benchmark/run")
async def run_benchmark(req: BenchmarkRequest):
    camera = req.camera
    if not camera:
        try:
            async with httpx.AsyncClient() as client:
                cfg_resp = await client.get(f"{FRIGATE_API}/api/config")
                cams = [name for name, c in cfg_resp.json().get("cameras", {}).items() if c.get("enabled", True)]
                if cams: camera = cams[0]
        except:
            pass
            
    if not camera:
        raise HTTPException(status_code=400, detail="No camera specified and none enabled")

    try:
        async with httpx.AsyncClient() as client:
            img_resp = await client.get(f"{FRIGATE_API}/api/{camera}/latest.jpg", timeout=5.0)
            img_resp.raise_for_status()
            frame_bytes = img_resp.content
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch frame: {e}")

    try:
        pil_img = Image.open(io.BytesIO(frame_bytes))
        w, h = pil_img.size
    except:
        w, h = 1920, 1080

    async def query_endpoint(ep):
        import time
        start = time.time()
        result = {
            "model": ep["name"],
            "latency_ms": 0,
            "person_count": 0,
            "max_cluster_size": 0,
            "cluster_bbox": None,
            "detections": [],
            "error": None
        }
        try:
            async with httpx.AsyncClient(timeout=90.0) as client:
                res = await client.post(ep["url"], content=frame_bytes, headers={"Content-Type": "application/octet-stream"})
                res.raise_for_status()
                data = res.json()
                latency = int((time.time() - start) * 1000)
                
                dets = data.get("detections", [])
                clusters = compute_crowd_clusters(dets, CLUSTER_SIZE_RATIO_MIN, CLUSTER_DISTANCE_FACTOR, min_cluster_size=CLUSTER_CROWD_THRESHOLD)
                c_size = clusters[0]["size"] if clusters else 0
                c_members = clusters[0]["member_indices"] if clusters else []
                c_bbox = clusters[0]["bbox"] if clusters else None
                
                result.update({
                    "latency_ms": latency,
                    "person_count": len(dets),
                    "max_cluster_size": c_size,
                    "cluster_bbox": c_bbox,
                    "cluster_member_indices": c_members,
                    "detections": dets
                })
        except Exception as e:
            result["error"] = str(e)
        return result

    tasks = [query_endpoint(ep) for ep in BENCHMARK_ENDPOINTS]
    results = await asyncio.gather(*tasks)

    return {
        "frame_b64": base64.b64encode(frame_bytes).decode("utf-8"),
        "frame_width": w,
        "frame_height": h,
        "results": results
    }








@app.get("/health")
async def health():
    return {"status": "ok"}

@app.get("/metrics")
async def metrics():
    return {
        "mqtt_messages": Metrics.mqtt_messages,
        "ws_broadcasts": Metrics.ws_broadcasts
    }

@app.get("/system/health")
async def system_health():
    status = "ok"
    details = {}
    
    # Check frigate
    try:
        async with httpx.AsyncClient() as client:
            res = await client.get(f"{FRIGATE_API}/api/version", timeout=3)
            details["frigate"] = "ok" if res.status_code == 200 else "error"
    except Exception:
        details["frigate"] = "error"
        
    # Check ai_worker
    try:
        async with httpx.AsyncClient() as client:
            res = await client.get(f"http://ai_worker:8091/health", timeout=3)
            details["ai_worker"] = res.json().get("status", "error")
    except Exception:
        details["ai_worker"] = "error"

    try:
        async with httpx.AsyncClient() as client:
            res = await client.get("http://perception_worker:8093/health", timeout=3)
            details["perception_worker"] = res.json().get("status", "error")
    except Exception:
        details["perception_worker"] = "error"
        
    # Check model endpoints directly
    for model, url in [("crowd_gpu", "http://crowd_gpu:8000/health"), ("fire_gpu", "http://fire_gpu:8000/health")]:
        try:
            async with httpx.AsyncClient() as client:
                res = await client.get(url, timeout=3)
                details[model] = "ok" if res.status_code == 200 else "error"
        except Exception:
            details[model] = "error"
            
    if any(s != "ok" for s in details.values()):
        status = "degraded"
        
    return {"status": status, "details": details}

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            data = await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)

@app.post("/api/webrtc")
async def proxy_webrtc(request: Request):
    return await proxy_frigate_api(request, "webrtc", FRIGATE_API)

@app.api_route("/api/{path:path}", methods=["GET", "HEAD", "POST", "PUT", "DELETE", "OPTIONS", "PATCH"])
async def proxy_api(request: Request, path: str):
    return await proxy_frigate_api(request, path, FRIGATE_API)

@app.websocket("/live/{path:path}")
async def proxy_live_ws(websocket: WebSocket, path: str):
    await proxy_frigate_ws(websocket, path, FRIGATE_API)

frontend_dist = os.path.join(os.path.dirname(__file__), "..", "frontend", "dist")

if os.path.exists(frontend_dist):
    app.mount("/assets", StaticFiles(directory=os.path.join(frontend_dist, "assets")), name="assets")

    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str):
        path = os.path.join(frontend_dist, full_path)
        if os.path.isfile(path):
            return FileResponse(path)
        return FileResponse(os.path.join(frontend_dist, "index.html"))
