#!/usr/bin/env python3
import io
import json
import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime, timezone

import paho.mqtt.client as mqtt
import requests
from PIL import Image
from smart_city_common.clustering import compute_crowd_clusters
from smart_city_common.circuit_breaker import CircuitBreaker

from config import (
    MQTT_HOST, MQTT_PORT, MQTT_CLIENT_ID, MQTT_KEEPALIVE,
    EVENTS_TOPIC, ALERT_TOPIC,
    FRIGATE_API, FRIGATE_SNAPSHOT_TIMEOUT, FRIGATE_SUBLABEL_TIMEOUT,
    ACTIVE_PROBLEM, ACTIVE_PROBLEMS, CROWD_INFERENCE_URL, MODAL_ENDPOINT_URL, MODAL_REQUEST_TIMEOUT,
    MAX_UPLOAD_BYTES,
    DOWNSCALE_INITIAL_QUALITY, DOWNSCALE_QUALITY_STEP,
    DOWNSCALE_MIN_QUALITY, DOWNSCALE_MAX_ITERATIONS, DOWNSCALE_MIN_DIMENSION,
    CROWD_THRESHOLD, DISTANCE_FACTOR, SIZE_RATIO_MIN,
    TARGET_ZONES, COOLDOWN_SECONDS,
    LOITERING_DWELL_SECONDS, LOITERING_ALERT_TOPIC,
    LOITERING_CAMERAS, CROWD_CAMERAS,
    LOITER_STATE_TTL_SECONDS,
    CROWD_PERSIST_SECONDS, CROWD_ALERT_REPEAT_SECONDS,
    MODEL_FAILURE_THRESHOLD, MODEL_BREAKER_SECONDS
)

_warned_no_url = False
_last_processed_time = {}
_last_loiter_alert = {}
_loiter_active = set()



class Metrics:
    requests = 0
    failures = 0
    alerts = 0
    last_error = ""
    last_success = ""

crowd_breaker = CircuitBreaker(MODEL_FAILURE_THRESHOLD, MODEL_BREAKER_SECONDS)

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/health':
            status = "ok" if crowd_breaker.state == "closed" else "degraded"
            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({
                "status": status,
                "crowd": crowd_breaker.state
            }).encode())
        elif self.path == '/metrics':
            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({
                "requests": Metrics.requests,
                "failures": Metrics.failures,
                "alerts": Metrics.alerts,
                "last_error": Metrics.last_error,
                "last_success": Metrics.last_success
            }).encode())
        else:
            self.send_response(404)
            self.end_headers()

def start_health_server():
    server = HTTPServer(('0.0.0.0', 8091), HealthHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()

_crowd_state = {}

def fetch_snapshot(event_id: str, session: requests.Session = None) -> bytes | None:
    url = f"{FRIGATE_API}/api/events/{event_id}/snapshot.jpg"
    try:
        r = session.get(url, timeout=FRIGATE_SNAPSHOT_TIMEOUT) if session else requests.get(url, timeout=FRIGATE_SNAPSHOT_TIMEOUT)
        if r.ok:
            return r.content
        print(f"[api] snapshot {event_id} -> HTTP {r.status_code}")
    except Exception as exc:  # noqa: BLE001
        print("[api] snapshot fetch error:", exc)
    return None


def downscale(jpeg_bytes: bytes) -> bytes | None:
    if len(jpeg_bytes) <= MAX_UPLOAD_BYTES:
        return jpeg_bytes
    try:
        img = Image.open(io.BytesIO(jpeg_bytes)).convert("RGB")
    except Exception as exc:  # noqa: BLE001
        print("[img] decode failed, skipping:", exc)
        return None

    quality = DOWNSCALE_INITIAL_QUALITY
    for _ in range(DOWNSCALE_MAX_ITERATIONS):
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=quality)
        data = buf.getvalue()
        if len(data) <= MAX_UPLOAD_BYTES:
            return data
        if quality > DOWNSCALE_MIN_QUALITY:
            quality -= DOWNSCALE_QUALITY_STEP
        else:
            w, h = img.size
            if max(w, h) <= DOWNSCALE_MIN_DIMENSION:
                return data
            img = img.resize((max(w // 2, 1), max(h // 2, 1)))
    return data


def get_image_size(jpeg_bytes: bytes) -> tuple[int, int] | None:
    try:
        with Image.open(io.BytesIO(jpeg_bytes)) as img:
            return img.size
    except Exception as exc:  # noqa: BLE001
        print("[img] size read failed:", exc)
        return None


def query_crowd(jpeg_bytes: bytes, session: requests.Session = None) -> dict | None:
    global _warned_no_url
    
    url = CROWD_INFERENCE_URL or MODAL_ENDPOINT_URL
    if not url:
        if not _warned_no_url:
            print("[modal] CROWD_INFERENCE_URL and MODAL_ENDPOINT_URL are empty")
            _warned_no_url = True
        return None
    if not crowd_breaker.can_request():
        return None
    try:
        Metrics.requests += 1
        req_func = session.post if session else requests.post
        r = req_func(
            url,
            data=jpeg_bytes,
            headers={"Content-Type": "application/octet-stream"},
            timeout=MODAL_REQUEST_TIMEOUT,
        )
        if not r.ok:
            Metrics.failures += 1
            Metrics.last_error = f"HTTP {r.status_code}"
            crowd_breaker.record_failure()
            print(f"[modal] HTTP {r.status_code}: {r.text[:200]}")
            return None
        crowd_breaker.record_success()
        Metrics.last_success = datetime.now(timezone.utc).isoformat()
        return r.json()
    except Exception as exc:  # noqa: BLE001
        Metrics.failures += 1
        Metrics.last_error = str(exc)
        crowd_breaker.record_failure()
        print("[modal] request/parse error:", exc)
        return None


def set_sub_label(event_id: str, label_text: str) -> None:
    url = f"{FRIGATE_API}/api/events/{event_id}/sub_label"
    payload = {"subLabel": label_text}
    try:
        r = requests.post(url, json=payload, timeout=FRIGATE_SUBLABEL_TIMEOUT)
        if not r.ok:
            print(f"[api] set_sub_label {event_id} -> HTTP {r.status_code}")
    except Exception as exc:  # noqa: BLE001
        print("[api] set_sub_label error:", exc)





def handle_crowd(client, after: dict, session: requests.Session = None) -> None:
    event_id = after.get("id")
    camera = after.get("camera")
    if not event_id or not camera:
        return

    if TARGET_ZONES:
        current = after.get("current_zones") or []
        if not any(z in TARGET_ZONES for z in current):
            return

    now = datetime.now(timezone.utc).timestamp()
    last_time = _last_processed_time.get(camera, 0)
    if now - last_time < COOLDOWN_SECONDS:
        return
    _last_processed_time[camera] = now

    t0 = time.perf_counter()
    snapshot = fetch_snapshot(event_id, session)
    if not snapshot:
        return
    t1 = time.perf_counter()

    payload = downscale(snapshot)
    if not payload:
        return
    t2 = time.perf_counter()

    inference_resolution = get_image_size(payload)
    if not inference_resolution:
        return

    result = query_crowd(payload, session)
    if not result:
        return
    t3 = time.perf_counter()

    print(f"[timing] {camera} fetch={(t1 - t0) * 1000:.0f}ms "
          f"downscale={(t2 - t1) * 1000:.0f}ms "
          f"infer={(t3 - t2) * 1000:.0f}ms "
          f"total={(t3 - t0) * 1000:.0f}ms")

    detections = result.get("detections", [])
    total_persons = int(result.get("person_count", 0))
    clusters = compute_crowd_clusters(detections, SIZE_RATIO_MIN, DISTANCE_FACTOR)
    
    max_cluster_size = clusters[0]["size"] if clusters else 0
    cluster_bbox = clusters[0]["bbox"] if clusters else None
    cluster_member_indices = clusters[0]["member_indices"] if clusters else []
    
    print(f"[crowd] {camera} id={event_id} total_persons={total_persons} "
          f"max_cluster={max_cluster_size} (threshold={CROWD_THRESHOLD})")

    cam_state = _crowd_state.setdefault(camera, {"active": False, "last_alert_time": 0, "first_detect_time": 0})

    if max_cluster_size >= CROWD_THRESHOLD:
        if cam_state["first_detect_time"] == 0:
            cam_state["first_detect_time"] = now
            
        if now - cam_state["first_detect_time"] >= CROWD_PERSIST_SECONDS:
            if not cam_state["active"] or (now - cam_state["last_alert_time"] >= CROWD_ALERT_REPEAT_SECONDS):
                cam_state["active"] = True
                cam_state["last_alert_time"] = now
                
                alert = {
                    "camera": camera,
                    "person_count": max_cluster_size,
                    "total_persons": total_persons,
                    "threshold": CROWD_THRESHOLD,
                    "event_id": event_id,
                    "model": result.get("model", "rfdetr-large"),
                    "detections": detections,
                    "cluster_bbox": cluster_bbox,
                    "cluster_member_indices": cluster_member_indices,
                    "clusters": clusters,
                    "active": True,
                    "inference_resolution": list(inference_resolution),
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
                client.publish(ALERT_TOPIC, json.dumps(alert))
                Metrics.alerts += 1
                print(f"[alert] published {ALERT_TOPIC} -> {camera} "
                      f"{max_cluster_size} persons in cluster")
                
                # Set sub_label in Frigate Review tab
                set_sub_label(event_id, f"CROWD: {max_cluster_size}")
    else:
        if cam_state["active"]:
            clear_alert = {
                "camera": camera,
                "person_count": 0,
                "active": False,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            client.publish(ALERT_TOPIC, json.dumps(clear_alert))
            print(f"[alert] cleared {ALERT_TOPIC} -> {camera}")
        cam_state["active"] = False
        cam_state["first_detect_time"] = 0



def handle_loitering(client, after: dict, event_type: str) -> None:
    event_id = after.get("id")
    camera = after.get("camera")
    if not event_id or not camera:
        return

    if LOITERING_CAMERAS and camera not in LOITERING_CAMERAS:
        return

    frame_time = after.get("frame_time")
    if frame_time is None:
        return

    now = datetime.now(timezone.utc).timestamp()

    if len(_last_loiter_alert) > 1000:
        for k in [k for k, v in _last_loiter_alert.items()
                  if now - v > LOITER_STATE_TTL_SECONDS]:
            del _last_loiter_alert[k]
            _loiter_active.discard(k)

    if event_type == "end":
        _last_loiter_alert.pop(event_id, None)
        if event_id in _loiter_active:
            _loiter_active.discard(event_id)
            clear = {
                "camera": camera,
                "object_id": event_id,
                "active": False,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            client.publish(LOITERING_ALERT_TOPIC, json.dumps(clear))
            print(f"[alert] cleared {LOITERING_ALERT_TOPIC} -> {camera} "
                  f"id={event_id} (object end)")
        return

    start_time = after.get("start_time")
    if start_time is None:
        return
    dwell_seconds = float(frame_time) - float(start_time)
    if dwell_seconds < LOITERING_DWELL_SECONDS:
        return

    box = after.get("box")
    last_alert = _last_loiter_alert.get(event_id, 0)
    is_new = (now - last_alert) >= COOLDOWN_SECONDS
    if is_new:
        _last_loiter_alert[event_id] = now
    _loiter_active.add(event_id)

    alert = {
        "camera": camera,
        "object_id": event_id,
        "label": after.get("label", "person"),
        "dwell_seconds": round(dwell_seconds, 1),
        "bbox": box,
        "score": after.get("score"),
        "active": True,
        "is_new": is_new,
        "frame_time": frame_time,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    client.publish(LOITERING_ALERT_TOPIC, json.dumps(alert))
    print(f"[alert] published {LOITERING_ALERT_TOPIC} -> {camera} "
          f"id={event_id} dwell={dwell_seconds:.1f}s is_new={is_new}")

    if is_new:
        set_sub_label(event_id, f"LOITER: {int(dwell_seconds)}s")


def on_connect(client, userdata, flags, reason_code, properties=None):
    print(f"[mqtt] connected rc={reason_code}; subscribing '{EVENTS_TOPIC}'")
    client.subscribe(EVENTS_TOPIC)


_mqtt_session = requests.Session()

def on_message(client, userdata, msg):
    try:
        payload = json.loads(msg.payload.decode())
        after = payload.get("after") or {}
        if after.get("label") != "person":
            return
        camera = after.get("camera")
        evt_type = payload.get("type")
        if ("crowd" in ACTIVE_PROBLEMS and evt_type in ("new", "update")
                and (not CROWD_CAMERAS or camera in CROWD_CAMERAS)):
            handle_crowd(client, after, _mqtt_session)
        if ("loitering" in ACTIVE_PROBLEMS and evt_type in ("new", "update", "end")
                and (not LOITERING_CAMERAS or camera in LOITERING_CAMERAS)):
            handle_loitering(client, after, evt_type)
    except Exception as exc:  # noqa: BLE001
        print("[event] handler error (ignored):", exc)


def main() -> None:
    start_health_server()
    print("=== AI Worker thin bridge starting ===")
    print(f"[cfg] ACTIVE_PROBLEM={ACTIVE_PROBLEM} "
          f"ACTIVE_PROBLEMS={sorted(ACTIVE_PROBLEMS)} "
          f"CROWD_THRESHOLD={CROWD_THRESHOLD} "
          f"MAX_UPLOAD_BYTES={MAX_UPLOAD_BYTES}")
    print(f"[cfg] DISTANCE_FACTOR={DISTANCE_FACTOR} "
          f"SIZE_RATIO_MIN={SIZE_RATIO_MIN} "
          f"TARGET_ZONES={TARGET_ZONES} "
          f"CROWD_CAMERAS={CROWD_CAMERAS}")
    print(f"[cfg] FRIGATE_API={FRIGATE_API} "
          f"MODAL_ENDPOINT_URL={'(set)' if MODAL_ENDPOINT_URL else '(empty)'}")
    print(f"[cfg] LOITERING_DWELL_SECONDS={LOITERING_DWELL_SECONDS} "
          f"LOITERING_ALERT_TOPIC={LOITERING_ALERT_TOPIC} "
          f"COOLDOWN_SECONDS={COOLDOWN_SECONDS}")
    print(f"[cfg] LOITERING_CAMERAS={LOITERING_CAMERAS} "
          f"LOITER_STATE_TTL_SECONDS={LOITER_STATE_TTL_SECONDS}")
    if "fire_smoke" in ACTIVE_PROBLEMS:
        print("[cfg] fire_smoke is handled by perception_worker RTSP lane; ai_worker latest.jpg pump is disabled")
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=MQTT_CLIENT_ID)
    client.on_connect = on_connect
    client.on_message = on_message
    print(f"[mqtt] connecting to {MQTT_HOST}:{MQTT_PORT} ...")
    client.connect(MQTT_HOST, MQTT_PORT, keepalive=MQTT_KEEPALIVE)
    client.loop_forever()


if __name__ == "__main__":
    main()