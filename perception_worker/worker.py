import base64
import json
import os
import sys
import threading
import time
from collections import deque
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer

import cv2
import numpy as np
import paho.mqtt.client as mqtt
import requests
import supervision as sv

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from smart_city_common.clustering import compute_crowd_clusters


def csv_env(name, default):
    return [x.strip() for x in os.getenv(name, default).split(",") if x.strip()]


CAMERAS = csv_env("PERCEPTION_CAMERAS", "cam1_VIRAT_1,cam_loiter,cam_fire")
PERSON_CAMERAS = set(csv_env("PERCEPTION_PERSON_CAMERAS", "cam1_VIRAT_1,cam_loiter"))
FIRE_CAMERAS = set(csv_env("PERCEPTION_FIRE_CAMERAS", "cam_fire"))
LPR_CAMERAS = set(csv_env("PERCEPTION_LPR_CAMERAS", ""))
RTSP_TEMPLATE = os.getenv("PERCEPTION_RTSP_TEMPLATE", "rtsp://frigate:8554/{}")
FPS = float(os.getenv("PERCEPTION_FPS", "8"))
STALE_SECONDS = float(os.getenv("PERCEPTION_STALE_SECONDS", "30"))
RECONNECT_SECONDS = float(os.getenv("PERCEPTION_RECONNECT_SECONDS", "5"))
MQTT_HOST = os.getenv("MQTT_HOST", "mqtt")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
TOPIC_PREFIX = os.getenv("PERCEPTION_TOPIC_PREFIX", "perception")
PERSON_DETECT_URL = os.getenv("PERSON_DETECT_URL", "http://crowd_gpu:8000/detect")
FIRE_SMOKE_ENDPOINT_URL = os.getenv("FIRE_SMOKE_ENDPOINT_URL", "http://fire_gpu:8000/detect")
LPR_ENDPOINT_URL = os.getenv("LPR_ENDPOINT_URL", "http://lpr_gpu:8000/detect")
LPR_FPS = float(os.getenv("LPR_FPS", "1"))
PERSON_DETECT_TIMEOUT = float(os.getenv("PERSON_DETECT_TIMEOUT", "10"))
FIRE_DETECT_TIMEOUT = float(os.getenv("FIRE_DETECT_TIMEOUT", os.getenv("PERSON_DETECT_TIMEOUT", "10")))
LPR_DETECT_TIMEOUT = float(os.getenv("LPR_DETECT_TIMEOUT", os.getenv("PERSON_DETECT_TIMEOUT", "10")))
JPEG_QUALITY = int(os.getenv("PERCEPTION_JPEG_QUALITY", "80"))
MIN_CONFIDENCE = float(os.getenv("PERCEPTION_MIN_CONFIDENCE", "0.5"))
FIRE_CONFIDENCE = float(os.getenv("FIRE_CONFIDENCE", "0.40"))
LOITERING_DWELL_SECONDS = float(os.getenv("LOITERING_DWELL_SECONDS", "40"))
TRACK_LOST_SECONDS = float(os.getenv("PERCEPTION_TRACK_LOST_SECONDS", "2"))
CROWD_THRESHOLD = int(os.getenv("CROWD_THRESHOLD", "3"))
CROWD_PERSIST_SECONDS = float(os.getenv("CROWD_PERSIST_SECONDS", "5"))
CROWD_ALERT_REPEAT_SECONDS = float(os.getenv("REALTIME_ALERT_REPEAT_SECONDS", os.getenv("CROWD_ALERT_REPEAT_SECONDS", "15")))
REALTIME_ALERT_REPEAT_SECONDS = float(os.getenv("REALTIME_ALERT_REPEAT_SECONDS", "15"))
CLUSTER_SIZE_RATIO_MIN = float(os.getenv("CLUSTER_SIZE_RATIO_MIN", os.getenv("SIZE_RATIO_MIN", "0.8")))
CLUSTER_DISTANCE_FACTOR = float(os.getenv("CLUSTER_DISTANCE_FACTOR", os.getenv("DISTANCE_FACTOR", "1.2")))
FIRE_PERSIST_N = int(os.getenv("FIRE_PERSIST_N", "2"))
FIRE_PERSIST_M = int(os.getenv("FIRE_PERSIST_M", "5"))
FIRE_CLEAR_SECONDS = float(os.getenv("FIRE_CLEAR_SECONDS", "4"))
LPR_STABLE_N = max(1, int(os.getenv("LPR_STABLE_N", "3")))
LPR_STABLE_M = max(LPR_STABLE_N, int(os.getenv("LPR_STABLE_M", "5")))
LPR_ALERT_REPEAT_SECONDS = float(os.getenv("LPR_ALERT_REPEAT_SECONDS", "30"))
LPR_MIN_OCR_CONF = float(os.getenv("LPR_MIN_OCR_CONF", "0.6"))
LPR_MIN_DET_CONF = float(os.getenv("LPR_MIN_DET_CONF", "0.5"))
LPR_CROP_PAD_RATIO = float(os.getenv("LPR_CROP_PAD_RATIO", "0.12"))
LPR_CROP_MAX_WIDTH = int(os.getenv("LPR_CROP_MAX_WIDTH", "320"))
LPR_CROP_JPEG_QUALITY = int(os.getenv("LPR_CROP_JPEG_QUALITY", "80"))
DETECTOR_HEALTH_TIMEOUT = float(os.getenv("DETECTOR_HEALTH_TIMEOUT", "5"))
ALLOW_HTTP_FRAME_FETCH = os.getenv("PERCEPTION_ALLOW_HTTP_FETCH", "false").strip().lower() in {"1", "true", "yes", "on"}
os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;tcp|fflags;nobuffer|flags;low_delay|max_delay;500000")


class CameraState:
    def __init__(self):
        self.tracker = sv.ByteTrack()
        self.frames_read = 0
        self.frames_processed = 0
        self.detect_errors = 0
        self.reconnects = 0
        self.publish_count = 0
        self.active_tracks = 0
        self.latencies = []
        self.last_seen = time.time()
        self.stale = False
        self.force_reconnect = False
        self.grabber = None
        self.track_states = {}
        self.crowd_since = None
        self.crowd_active = False
        self.last_crowd_alert_time = 0.0
        self.fire_history = deque(maxlen=FIRE_PERSIST_M)
        self.fire_active = False
        self.fire_seen = 0.0
        self.last_fire_alert_time = 0.0
        self.lpr_history = deque(maxlen=LPR_STABLE_M)
        self.last_lpr_alert_times = {}


states = {cam: CameraState() for cam in CAMERAS}
metrics_lock = threading.Lock()
mqtt_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
mqtt_connected = False


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def iso_from_epoch(ts):
    return datetime.fromtimestamp(ts, timezone.utc).isoformat()


def on_connect(client, userdata, flags, reason_code, properties=None):
    global mqtt_connected
    mqtt_connected = reason_code == 0
    print(f"[perception] mqtt_connected={mqtt_connected} reason={reason_code}")


def on_disconnect(client, userdata, flags, reason_code, properties=None):
    global mqtt_connected
    mqtt_connected = False
    print(f"[perception] mqtt disconnected reason={reason_code}")


mqtt_client.on_connect = on_connect
mqtt_client.on_disconnect = on_disconnect


class FrameGrabber:
    def __init__(self, url):
        self.url = url
        if self.url.startswith("http") and not ALLOW_HTTP_FRAME_FETCH:
            raise ValueError(f"HTTP frame fetch disabled for {self.url}; use RTSP source")
        self.cap = None
        self.lock = threading.Lock()
        self.latest_frame = None
        self.seq = 0
        self.running = True
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _open(self):
        if self.cap is not None:
            try:
                self.cap.release()
            except Exception:
                pass
        if not self.url.startswith("http"):
            self.cap = cv2.VideoCapture(self.url, cv2.CAP_FFMPEG)
            self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

    def _run(self):
        failures = 0
        while self.running:
            if self.url.startswith("http"):
                try:
                    resp = requests.get(self.url, timeout=2)
                    if resp.status_code == 200:
                        frame = cv2.imdecode(np.frombuffer(resp.content, np.uint8), cv2.IMREAD_COLOR)
                        if frame is not None:
                            failures = 0
                            with self.lock:
                                self.seq += 1
                                self.latest_frame = frame
                        else:
                            failures += 1
                    else:
                        failures += 1
                except Exception as e:
                    print(f"FrameGrabber requests exception: {e}")
                    failures += 1
                time.sleep(1.0 / FPS)  # Prevent spamming Frigate API
                continue

            if self.cap is None or not self.cap.isOpened():
                self._open()
                if self.cap is None or not self.cap.isOpened():
                    time.sleep(0.5)
                    continue
            ret, frame = self.cap.read()
            if ret and frame is not None:
                failures = 0
                with self.lock:
                    self.seq += 1
                    self.latest_frame = frame
            else:
                failures += 1
                if failures >= 10:
                    self._open()
                    failures = 0
                time.sleep(0.1)

    def read(self):
        with self.lock:
            if self.latest_frame is None:
                return 0, None
            return self.seq, self.latest_frame.copy()

    def release(self):
        self.running = False
        if self.cap is not None:
            try:
                self.cap.release()
            except Exception:
                pass


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            with metrics_lock:
                stale = any(s.stale for s in states.values())
            detector_ok = check_detector_health()
            ok = mqtt_connected and detector_ok and not stale
            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"status": "ok" if ok else "degraded"}).encode())
            return
        if self.path == "/metrics":
            payload = {}
            with metrics_lock:
                for cam, st in states.items():
                    lat = st.latencies[-100:]
                    payload[cam] = {
                        "frames_read": st.frames_read,
                        "frames_processed": st.frames_processed,
                        "detect_errors": st.detect_errors,
                        "reconnects": st.reconnects,
                        "active_tracks": st.active_tracks,
                        "publish_count": st.publish_count,
                        "stale": st.stale,
                        "last_frame_age": time.time() - st.last_seen,
                        "detector_latency_ms_p50": int(np.percentile(lat, 50)) if lat else 0,
                        "detector_latency_ms_p95": int(np.percentile(lat, 95)) if lat else 0,
                    }
            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(payload).encode())
            return
        self.send_response(404)
        self.end_headers()


def start_health_server():
    server = HTTPServer(("0.0.0.0", 8093), HealthHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()


def check_detector_health():
    urls = []
    if PERSON_CAMERAS:
        urls.append(PERSON_DETECT_URL.replace("/detect", "/health"))
    if FIRE_CAMERAS:
        urls.append(FIRE_SMOKE_ENDPOINT_URL.replace("/detect", "/health"))
    if LPR_CAMERAS:
        urls.append(LPR_ENDPOINT_URL.replace("/detect", "/health"))
    try:
        return all(requests.get(url, timeout=DETECTOR_HEALTH_TIMEOUT).ok for url in urls)
    except Exception:
        return False


def publish(topic, payload):
    mqtt_client.publish(topic, json.dumps(payload, separators=(",", ":")))


def encode_jpeg(frame):
    ok, enc = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_QUALITY])
    return enc.tobytes() if ok else None


def crop_plate(frame, bbox):
    if frame is None or not bbox:
        return ""
    try:
        h, w = frame.shape[:2]
        x1, y1, x2, y2 = bbox
        pad_x = (x2 - x1) * LPR_CROP_PAD_RATIO
        pad_y = (y2 - y1) * LPR_CROP_PAD_RATIO
        x1 = max(0, int(x1 - pad_x))
        y1 = max(0, int(y1 - pad_y))
        x2 = min(w, int(x2 + pad_x))
        y2 = min(h, int(y2 + pad_y))
        if x2 <= x1 or y2 <= y1:
            return ""
        crop = frame[y1:y2, x1:x2]
        ch, cw = crop.shape[:2]
        if cw > LPR_CROP_MAX_WIDTH:
            scale = LPR_CROP_MAX_WIDTH / float(cw)
            crop = cv2.resize(crop, (LPR_CROP_MAX_WIDTH, max(1, int(ch * scale))), interpolation=cv2.INTER_AREA)
        ok, enc = cv2.imencode(".jpg", crop, [int(cv2.IMWRITE_JPEG_QUALITY), LPR_CROP_JPEG_QUALITY])
        if not ok:
            return ""
        return "data:image/jpeg;base64," + base64.b64encode(enc.tobytes()).decode("ascii")
    except Exception:
        return ""


def query_detector(session, url, jpeg_bytes, timeout):
    start = time.perf_counter()
    r = session.post(url, data=jpeg_bytes, headers={"Content-Type": "application/octet-stream"}, timeout=timeout)
    r.raise_for_status()
    return r.json(), (time.perf_counter() - start) * 1000.0


def clean_bbox(value):
    if not value or len(value) != 4:
        return None
    try:
        x1, y1, x2, y2 = [float(x) for x in value]
    except (TypeError, ValueError):
        return None
    if x2 <= x1 or y2 <= y1:
        return None
    return [x1, y1, x2, y2]


def update_latency(st, latency_ms):
    with metrics_lock:
        st.latencies.append(latency_ms)
        if len(st.latencies) > 200:
            st.latencies = st.latencies[-100:]


def base_payload(camera, frame_id, wall_ts, monotonic_ts, width, height, url, model):
    return {
        "source": "realtime",
        "stream_source": url,
        "camera": camera,
        "frame_id": frame_id,
        "wall_ts": wall_ts,
        "monotonic_ts": monotonic_ts,
        "width": width,
        "height": height,
        "model": model,
    }


def person_detections(result):
    out = []
    for det in result.get("detections", []):
        bbox = clean_bbox(det.get("bbox"))
        conf = float(det.get("confidence", 0.0))
        label = det.get("class", "person")
        if bbox is None or conf < MIN_CONFIDENCE or label != "person":
            continue
        out.append({"bbox": bbox, "confidence": conf, "class": "person"})
    return out


def track_people(st, detections, camera, frame_id, now):
    if detections:
        xyxy = np.array([d["bbox"] for d in detections], dtype=float)
        confidence = np.array([d["confidence"] for d in detections], dtype=float)
        class_id = np.zeros(len(detections), dtype=int)
        sv_dets = sv.Detections(xyxy=xyxy, confidence=confidence, class_id=class_id)
    else:
        sv_dets = sv.Detections.empty()

    tracked = st.tracker.update_with_detections(sv_dets)
    objects = []
    current = set()
    if len(tracked) > 0:
        for i in range(len(tracked)):
            bbox = [float(x) for x in tracked.xyxy[i].tolist()]
            conf = float(tracked.confidence[i]) if tracked.confidence is not None else 1.0
            raw_id = int(tracked.tracker_id[i]) if tracked.tracker_id is not None else i + 1
            current.add(raw_id)
            state = st.track_states.setdefault(
                raw_id,
                {
                    "first_seen": now,
                    "first_seen_wall": utc_now(),
                    "last_seen": now,
                    "last_seen_wall": utc_now(),
                    "hits": 0,
                    "loiter_active": False,
                    "last_loiter_alert_time": 0.0,
                },
            )
            state["last_seen"] = now
            state["last_seen_wall"] = utc_now()
            state["bbox"] = bbox
            state["confidence"] = conf
            state["hits"] += 1
            age = now - state["first_seen"]
            objects.append(
                {
                    "id": f"{camera}:{frame_id}:{i}",
                    "track_id": f"{camera}:{raw_id}",
                    "class": "person",
                    "bbox": bbox,
                    "confidence": conf,
                    "age_seconds": age,
                }
            )
    return objects, current


def publish_tracks(camera, st, frame_meta, current_ids):
    tracks = []
    for tid in sorted(current_ids):
        state = st.track_states.get(tid)
        if not state:
            continue
        tracks.append(
            {
                "track_id": f"{camera}:{tid}",
                "class": "person",
                "bbox": state.get("bbox"),
                "confidence": state.get("confidence", 0.0),
                "first_seen": state["first_seen_wall"],
                "last_seen": state["last_seen_wall"],
                "age_seconds": time.time() - state["first_seen"],
                "hits": state["hits"],
                "state": "active",
            }
        )
    payload = {"schema": "tracks.v1", **frame_meta, "tracks": tracks}
    publish(f"{TOPIC_PREFIX}/tracks/{camera}", payload)


def handle_loiter_alerts(camera, st, frame_meta, objects, current_ids, now):
    for obj in objects:
        raw_id = int(obj["track_id"].rsplit(":", 1)[1])
        state = st.track_states[raw_id]
        age = now - state["first_seen"]
        if age < LOITERING_DWELL_SECONDS:
            continue
        state["loiter_active"] = True
        if now - state["last_loiter_alert_time"] < REALTIME_ALERT_REPEAT_SECONDS:
            continue
        payload = {
            **frame_meta,
            "timestamp": frame_meta["wall_ts"],
            "active": True,
            "object_id": obj["track_id"],
            "dwell_time": age,
            "bbox": obj["bbox"],
        }
        publish(f"{TOPIC_PREFIX}/alerts/loitering", payload)
        state["last_loiter_alert_time"] = now

    missing = [tid for tid in st.track_states if tid not in current_ids]
    for tid in missing:
        state = st.track_states[tid]
        if now - state["last_seen"] <= TRACK_LOST_SECONDS:
            continue
        if state.get("loiter_active"):
            payload = {
                **frame_meta,
                "timestamp": frame_meta["wall_ts"],
                "active": False,
                "object_id": f"{camera}:{tid}",
                "dwell_time": state["last_seen"] - state["first_seen"],
                "bbox": None,
            }
            publish(f"{TOPIC_PREFIX}/alerts/loitering", payload)
        del st.track_states[tid]


def handle_crowd(camera, st, frame_meta, detections, now):
    clusters = compute_crowd_clusters(detections, CLUSTER_SIZE_RATIO_MIN, CLUSTER_DISTANCE_FACTOR, min_cluster_size=CROWD_THRESHOLD)
    max_cluster = clusters[0] if clusters else None
    max_cluster_size = max_cluster["size"] if max_cluster else 0
    payload = {
        "schema": "crowd.v1",
        **frame_meta,
        "person_count": max_cluster_size,
        "threshold": CROWD_THRESHOLD,
        "clusters": clusters,
        "detections": [{"bbox": o["bbox"], "confidence": o["confidence"]} for o in detections]
    }
    publish(f"{TOPIC_PREFIX}/crowd/{camera}", payload)

    eligible = max_cluster is not None and max_cluster_size >= CROWD_THRESHOLD
    if eligible:
        if st.crowd_since is None:
            st.crowd_since = now
        persisted = now - st.crowd_since >= CROWD_PERSIST_SECONDS
        due = now - st.last_crowd_alert_time >= CROWD_ALERT_REPEAT_SECONDS
        if persisted and (not st.crowd_active or due):
            alert = {
                **frame_meta,
                "timestamp": frame_meta["wall_ts"],
                "active": True,
                "person_count": max_cluster_size,
                "total_persons": len(detections),
                "threshold": CROWD_THRESHOLD,
                "cluster_bbox": max_cluster["bbox"],
                "cluster_member_indices": max_cluster["member_indices"],
                "clusters": clusters,
                "detections": [{"bbox": o["bbox"], "confidence": o["confidence"]} for o in detections],
                "inference_resolution": [frame_meta["width"], frame_meta["height"]],
            }
            publish(f"{TOPIC_PREFIX}/alerts/crowd", alert)
            st.crowd_active = True
            st.last_crowd_alert_time = now
        return

    st.crowd_since = None
    if st.crowd_active:
        alert = {**frame_meta, "timestamp": frame_meta["wall_ts"], "active": False, "person_count": 0}
        publish(f"{TOPIC_PREFIX}/alerts/crowd", alert)
        st.crowd_active = False
    st.last_crowd_alert_time = 0.0


def process_person_frame(camera, st, session, jpeg_bytes, frame_meta, now):
    result, latency = query_detector(session, PERSON_DETECT_URL, jpeg_bytes, PERSON_DETECT_TIMEOUT)
    update_latency(st, latency)
    model = result.get("model", "person-detector")
    frame_meta["model"] = model
    detections = person_detections(result)
    objects, current_ids = track_people(st, detections, camera, frame_meta["frame_id"], now)
    objects_payload = {"schema": "objects.v1", **frame_meta, "objects": objects}
    publish(f"{TOPIC_PREFIX}/objects/{camera}", objects_payload)
    publish_tracks(camera, st, frame_meta, current_ids)
    handle_loiter_alerts(camera, st, frame_meta, objects, current_ids, now)
    handle_crowd(camera, st, frame_meta, detections, now)
    with metrics_lock:
        st.active_tracks = len(current_ids)


def fire_detections(result):
    out = []
    for det in result.get("detections", []):
        cls = det.get("class")
        bbox = clean_bbox(det.get("bbox"))
        conf = float(det.get("confidence", 0.0))
        if cls not in {"fire", "smoke"} or bbox is None or conf < FIRE_CONFIDENCE:
            continue
        out.append({"bbox": bbox, "confidence": conf, "class": cls})
    return out


def publish_fire_clear(camera, st, frame_meta):
    payload = {**frame_meta, "timestamp": frame_meta["wall_ts"], "active": False, "fire_count": 0, "smoke_count": 0, "detections": []}
    publish(f"{TOPIC_PREFIX}/alerts/fire_smoke", payload)
    st.fire_active = False
    st.last_fire_alert_time = 0.0


def process_fire_frame(camera, st, session, jpeg_bytes, frame_meta, now):
    result, latency = query_detector(session, FIRE_SMOKE_ENDPOINT_URL, jpeg_bytes, FIRE_DETECT_TIMEOUT)
    update_latency(st, latency)
    model = result.get("model", "fire-smoke-detector")
    frame_meta["model"] = model
    detections = fire_detections(result)
    fire_count = sum(1 for d in detections if d["class"] == "fire")
    smoke_count = sum(1 for d in detections if d["class"] == "smoke")
    payload = {
        "schema": "fire_smoke.v1",
        **frame_meta,
        "detections": detections,
        "fire_count": fire_count,
        "smoke_count": smoke_count,
    }
    publish(f"{TOPIC_PREFIX}/fire_smoke/{camera}", payload)

    st.fire_history.append({"fire": fire_count > 0, "smoke": smoke_count > 0})
    fire_hits = sum(1 for x in st.fire_history if x["fire"])
    smoke_hits = sum(1 for x in st.fire_history if x["smoke"])
    active = fire_hits >= FIRE_PERSIST_N or smoke_hits >= FIRE_PERSIST_N
    if active:
        st.fire_seen = now
        due = now - st.last_fire_alert_time >= REALTIME_ALERT_REPEAT_SECONDS
        if not st.fire_active or due:
            alert = {
                **frame_meta,
                "timestamp": frame_meta["wall_ts"],
                "active": True,
                "fire_count": fire_count,
                "smoke_count": smoke_count,
                "detections": detections,
                "inference_resolution": [frame_meta["width"], frame_meta["height"]],
            }
            publish(f"{TOPIC_PREFIX}/alerts/fire_smoke", alert)
            st.fire_active = True
            st.last_fire_alert_time = now
    elif st.fire_active and now - st.fire_seen >= FIRE_CLEAR_SECONDS:
        publish_fire_clear(camera, st, frame_meta)



def clean_plate_text(value):
    if not value:
        return ""
    text = str(value).upper()
    return "".join(ch for ch in text if ("A" <= ch <= "Z") or ("0" <= ch <= "9"))


def lpr_plates(result):
    out = []
    for plate in result.get("plates", []):
        bbox = clean_bbox(plate.get("bbox"))
        if bbox is None:
            continue
        text = clean_plate_text(plate.get("text") or plate.get("raw_text"))
        raw_text = str(plate.get("raw_text") or plate.get("text") or "")
        det_conf = float(plate.get("det_confidence", 0.0))
        ocr_conf = float(plate.get("ocr_confidence", 0.0))
        conf = float(plate.get("confidence", min(det_conf, ocr_conf)))
        out.append(
            {
                "bbox": bbox,
                "det_confidence": det_conf,
                "text": text,
                "raw_text": raw_text,
                "ocr_confidence": ocr_conf,
                "confidence": conf,
            }
        )
    return out


def process_lpr_frame(camera, st, session, jpeg_bytes, frame, frame_meta, now):
    result, latency = query_detector(session, LPR_ENDPOINT_URL, jpeg_bytes, LPR_DETECT_TIMEOUT)
    update_latency(st, latency)
    model = result.get("model", "lpr-detector")
    frame_meta["model"] = model
    plates = lpr_plates(result)
    payload = {
        "schema": "lpr.v1",
        **frame_meta,
        "plates": plates,
        "plate_count": len(plates),
    }
    publish(f"{TOPIC_PREFIX}/lpr/{camera}", payload)

    current_texts = {p["text"] for p in plates if p["text"]}
    st.lpr_history.append(current_texts)
    if not current_texts:
        return

    best = {}
    for plate in plates:
        text = plate["text"]
        if not text:
            continue
        if text not in best or plate["confidence"] > best[text]["confidence"]:
            best[text] = plate

    for text, plate in best.items():
        stable_hits = sum(1 for texts in st.lpr_history if text in texts)
        confident = plate["ocr_confidence"] >= LPR_MIN_OCR_CONF and plate["det_confidence"] >= LPR_MIN_DET_CONF
        if stable_hits < LPR_STABLE_N and not confident:
            continue
        last_alert = st.last_lpr_alert_times.get(text, 0.0)
        if now - last_alert < LPR_ALERT_REPEAT_SECONDS:
            continue
        alert = {
            **frame_meta,
            "timestamp": frame_meta["wall_ts"],
            "active": True,
            "plate_text": text,
            "bbox": plate["bbox"],
            "det_confidence": plate["det_confidence"],
            "ocr_confidence": plate["ocr_confidence"],
            "confidence": plate["confidence"],
            "stable_hits": stable_hits,
            "inference_resolution": [frame_meta["width"], frame_meta["height"]],
            "plate_crop": crop_plate(frame, plate["bbox"]),
        }
        publish(f"{TOPIC_PREFIX}/alerts/lpr", alert)
        st.last_lpr_alert_times[text] = now


def task_due(now, last_ts, fps):
    return fps > 0 and now - last_ts >= 1.0 / fps

def process_camera(camera):
    url = RTSP_TEMPLATE.format(camera)
    st = states[camera]
    session = requests.Session()

    while True:
        print(f"[perception] connect camera={camera} url={url}")
        try:
            grabber = FrameGrabber(url)
        except Exception as exc:
            with metrics_lock:
                st.detect_errors += 1
                st.reconnects += 1
                st.stale = True
            print(f"[perception] connect failed camera={camera}: {exc}")
            time.sleep(RECONNECT_SECONDS)
            continue
        with metrics_lock:
            st.grabber = grabber
            st.stale = False
            st.force_reconnect = False
            st.last_seen = time.time()
        last_seq = 0
        last_task_ts = {"person": 0.0, "fire": 0.0, "lpr": 0.0}

        while True:
            with metrics_lock:
                if st.force_reconnect:
                    break
            seq, frame = grabber.read()
            if seq == 0 or frame is None:
                time.sleep(0.05)
                continue
            now = time.time()
            if seq == last_seq:
                time.sleep(0.01)
                continue
            last_seq = seq
            with metrics_lock:
                st.frames_read += 1
                st.last_seen = now
                st.stale = False
                frame_id = st.frames_read

            tasks = []
            if camera in PERSON_CAMERAS and task_due(now, last_task_ts["person"], FPS):
                tasks.append("person")
            if camera in FIRE_CAMERAS and task_due(now, last_task_ts["fire"], FPS):
                tasks.append("fire")
            if camera in LPR_CAMERAS and task_due(now, last_task_ts["lpr"], LPR_FPS):
                tasks.append("lpr")
            if not tasks:
                continue

            jpeg_bytes = encode_jpeg(frame)
            if jpeg_bytes is None:
                continue
            height, width = frame.shape[:2]
            frame_meta = base_payload(camera, frame_id, utc_now(), time.monotonic(), width, height, url, "unknown")
            ran = False
            for task in tasks:
                last_task_ts[task] = now
                try:
                    if task == "person":
                        process_person_frame(camera, st, session, jpeg_bytes, frame_meta.copy(), now)
                    elif task == "fire":
                        process_fire_frame(camera, st, session, jpeg_bytes, frame_meta.copy(), now)
                    elif task == "lpr":
                        process_lpr_frame(camera, st, session, jpeg_bytes, frame, frame_meta.copy(), now)
                    ran = True
                except Exception as exc:
                    with metrics_lock:
                        st.detect_errors += 1
                    print(f"[perception] frame error camera={camera} task={task}: {exc}")
            if ran:
                with metrics_lock:
                    st.frames_processed += 1
                    st.publish_count += 1

        grabber.release()
        with metrics_lock:
            st.grabber = None
            st.reconnects += 1
        time.sleep(RECONNECT_SECONDS)

def watchdog():
    while True:
        now = time.time()
        stale_grabbers = []
        with metrics_lock:
            for cam, st in states.items():
                if now - st.last_seen > STALE_SECONDS:
                    if not st.stale:
                        print(f"[perception] stale camera={cam} age={now - st.last_seen:.1f}s")
                    st.stale = True
                    st.force_reconnect = True
                    if st.grabber is not None:
                        stale_grabbers.append(st.grabber)
        for grabber in stale_grabbers:
            grabber.release()
        time.sleep(1)


def main():
    start_health_server()
    try:
        mqtt_client.connect(MQTT_HOST, MQTT_PORT, 60)
        mqtt_client.loop_start()
    except Exception as exc:
        print(f"[perception] mqtt connect failed: {exc}")
        sys.exit(1)

    for cam in CAMERAS:
        threading.Thread(target=process_camera, args=(cam,), daemon=True).start()

    threading.Thread(target=watchdog, daemon=True).start()
    while True:
        time.sleep(3600)


if __name__ == "__main__":
    main()
