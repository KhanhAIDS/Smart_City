import os
import sys
import json
import time
import subprocess
import threading
import re
from datetime import datetime, timezone
import paho.mqtt.client as mqtt
from http.server import HTTPServer, BaseHTTPRequestHandler

# Env vars
CAMERAS = [c.strip() for c in os.getenv("STREAM_CORE_CAMERAS", "cam1_VIRAT_1,cam_loiter,cam_fire").split(",") if c.strip()]
RTSP_TEMPLATE = os.getenv("STREAM_CORE_RTSP_TEMPLATE", "rtsp://frigate:8554/{}")
FPS = float(os.getenv("STREAM_CORE_FPS", "5"))
STALE_SECONDS = float(os.getenv("STREAM_CORE_STALE_SECONDS", "15"))
RECONNECT_SECONDS = float(os.getenv("STREAM_CORE_RECONNECT_SECONDS", "5"))
MQTT_HOST = os.getenv("MQTT_HOST", "mqtt")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))

class CameraMetrics:
    def __init__(self):
        self.frames = 0
        self.reconnects = 0
        self.errors = 0
        self.last_frame_age = 0.0
        self.last_pts_time = 0.0
        self.last_seen = time.time()
        self.fps_ema = 0.0
        self.stale = False
        self.generation = 0
        self.process = None

metrics_dict = {cam: CameraMetrics() for cam in CAMERAS}
metrics_lock = threading.Lock()

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/health':
            with metrics_lock:
                is_degraded = any(m.stale for m in metrics_dict.values())
            
            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"status": "degraded" if is_degraded else "ok"}).encode())
        elif self.path == '/metrics':
            res = {}
            with metrics_lock:
                for cam, m in metrics_dict.items():
                    now = time.time()
                    res[cam] = {
                        "frames": m.frames,
                        "fps_ema": m.fps_ema,
                        "reconnects": m.reconnects,
                        "errors": m.errors,
                        "last_frame_age": now - m.last_seen,
                        "last_pts_time": m.last_pts_time,
                        "stale": m.stale,
                        "generation": m.generation
                    }
            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(res).encode())
        else:
            self.send_response(404)
            self.end_headers()

def start_health_server():
    server = HTTPServer(('0.0.0.0', 8092), HealthHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()

SHOWINFO_REGEX = re.compile(r"pts:\s*(?P<pts>[a-zA-Z0-9/]+)\s+pts_time:\s*(?P<pts_time>[a-zA-Z0-9/\.]+).*?s:\s*(?P<s>\d+x\d+)")

def parser_thread_func(camera: str, client: mqtt.Client):
    url = RTSP_TEMPLATE.format(camera)
    
    while True:
        with metrics_lock:
            metrics_dict[camera].generation += 1
            gen = metrics_dict[camera].generation
            
        cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "info",
            "-i", url, "-fflags", "+genpts",
            "-vf", f"fps=fps={FPS},showinfo", "-f", "null", "-"
        ]
        
        print(f"[stream_core] Starting ffmpeg for {camera}: {' '.join(cmd)}")
        try:
            process = subprocess.Popen(
                cmd,
                stderr=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                text=True,
                bufsize=1
            )
            with metrics_lock:
                metrics_dict[camera].process = process
                metrics_dict[camera].last_seen = time.time()
                
            last_calc_time = time.time()
            
            for line in process.stderr:
                match = SHOWINFO_REGEX.search(line)
                if match:
                    pts_str = match.group("pts")
                    pts = int(pts_str) if pts_str.isdigit() else None
                    pts_time_str = match.group("pts_time")
                    pts_time = float(pts_time_str) if pts_time_str.replace('.', '', 1).isdigit() else None
                    s = match.group("s")
                    w, h = map(int, s.split("x"))
                    
                    now = time.time()
                    with metrics_lock:
                        m = metrics_dict[camera]
                        m.frames += 1
                        frame_id = m.frames
                        if pts_time is not None:
                            m.last_pts_time = pts_time
                        
                        dt = now - last_calc_time
                        if dt > 0:
                            current_fps = 1.0 / dt
                            if m.fps_ema == 0.0:
                                m.fps_ema = current_fps
                            else:
                                m.fps_ema = 0.1 * current_fps + 0.9 * m.fps_ema
                        
                        m.last_seen = now
                        m.stale = False
                        
                    last_calc_time = now
                    
                    payload = {
                        "schema": "frames.v1",
                        "camera": camera,
                        "frame_id": frame_id,
                        "pts": pts,
                        "pts_time": pts_time,
                        "monotonic_ts": time.monotonic(),
                        "wall_ts": datetime.now(timezone.utc).isoformat(),
                        "width": w,
                        "height": h,
                        "source": url,
                        "generation": gen
                    }
                    client.publish(f"stream_core/frames/{camera}", json.dumps(payload))
                    
        except Exception as e:
            print(f"[stream_core] Error parsing ffmpeg for {camera}: {e}")
            with metrics_lock:
                metrics_dict[camera].errors += 1
        finally:
            with metrics_lock:
                if metrics_dict[camera].process:
                    if metrics_dict[camera].process.poll() is None:
                        try:
                            metrics_dict[camera].process.kill()
                            metrics_dict[camera].process.wait()
                        except:
                            pass
                    metrics_dict[camera].process = None
                metrics_dict[camera].reconnects += 1
            time.sleep(RECONNECT_SECONDS)

def watchdog_func():
    while True:
        now = time.time()
        with metrics_lock:
            for cam, m in metrics_dict.items():
                if now - m.last_seen > STALE_SECONDS:
                    if not m.stale:
                        print(f"[stream_core] Watchdog: {cam} is stale (> {STALE_SECONDS}s). Killing process.")
                        m.stale = True
                    if m.process and m.process.poll() is None:
                        try:
                            m.process.kill()
                        except:
                            pass
                        try:
                            if m.process.stderr:
                                m.process.stderr.close()
                        except:
                            pass
        time.sleep(1)

def main():
    start_health_server()
    
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    try:
        client.connect(MQTT_HOST, MQTT_PORT, 60)
        client.loop_start()
    except Exception as e:
        print(f"[stream_core] Could not connect to MQTT: {e}")
        sys.exit(1)
        
    for cam in CAMERAS:
        t = threading.Thread(target=parser_thread_func, args=(cam, client), daemon=True)
        t.start()
        
    watchdog = threading.Thread(target=watchdog_func, daemon=True)
    watchdog.start()
    
    watchdog.join()

if __name__ == "__main__":
    main()
