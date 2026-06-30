#!/usr/bin/env python3
"""
=============================================================================
 Smart_City — CẤU HÌNH TẬP TRUNG (ai_worker)    |    Server: 192.168.3.252
=============================================================================
 SCOPE: REALTIME-STREAM-FIRST. Frigate giữ live/record/go2rtc; AI realtime chạy trong perception_worker đọc RTSP restream.
 Luồng mặc định: go2rtc RTSP (`rtsp://frigate:8554/<cam>`) -> perception_worker -> model_workers -> dashboard.
 stream_core chỉ còn lane phụ/benchmark metadata; không phải nguồn bbox live chính.
 
 Gom các tham số của ai_worker để chỉnh nhanh. File nằm ở thư mục gốc và được
 MOUNT vào container ai_worker (xem docker-compose.yml), nên:
   • Sửa file này        -> docker compose restart ai_worker   (KHÔNG cần build)
   • Hoặc override bằng env trong docker-compose.yml (env ưu tiên cao hơn).

 CÁC CONFIG KHÔNG đặt được trong file này (vì ở container/file khác):
   • Frigate: camera, detect.fps, record, detector, go2rtc   -> config.yml
   • cam1_VIRAT_1, cam_loiter, cam_fire đều single go2rtc exec + RTSP restream.
   • Frigate motion/track theo TỪNG camera (bắt người nhỏ ở cam xa + nhịp re-detect người đứng yên):
       - motion.threshold     (nhạy pixel; mặc định 30; HẠ = nhạy hơn)
       - motion.contour_area  (diện tích motion tối thiểu; mặc định 10; HẠ = bắt người nhỏ/xa hơn — núm chính cho cam xa)
       - detect.stationary.interval (số frame re-detect người đứng yên; mặc định 50 = 10s@fps5; HẠ = box loiterer tươi hơn, giảm gap update)
       -> đặt trong block từng camera ở config.yml. Đổi xong: docker compose restart frigate (mount sẵn, KHÔNG build).
       Hiện cam_loiter: motion.threshold=25, motion.contour_area=5, detect.stationary.interval=5.
   • Ngưỡng phát hiện người: env DETECTION_THRESHOLD trong docker-compose.yml
       (của crowd_gpu / locate_gpu; đổi xong: docker compose up -d <service> — KHÔNG build)
   • Model + cổng 8000 của mỗi GPU service -> model_workers/<model>/server.py
       (crowd_gpu = rf_detr_large, locate_gpu = locate_anything_3b; đổi model phải build lại)
   • Hạ tầng container: mem_limit, cpus, GPU passthrough, port, env override
       -> docker-compose.yml
   • Dashboard: STALE_SECONDS, cổng 8080, topic, BENCHMARK_ENDPOINTS  -> docker-compose.yml (service dashboard)
   • FIRE/SMOKE (realtime RTSP, KHÔNG fetch latest.jpg):
       - Model + cổng 8000 của fire_gpu -> model_workers/fire_smoke/server.py (env FIRE_MODEL_REPO,
         DETECTION_THRESHOLD ngưỡng phía MODEL, FIRE_IMG_SIZE). Đổi model phải build lại fire_gpu.
       - Hạ tầng fire_gpu (mem_limit 6g, GPU passthrough, bind-mount ./model_cache/fire_hf) -> docker-compose.yml.
       - cam_fire dùng cùng single go2rtc exec + RTSP restream như cam1/cam_loiter -> config.yml.
       - perception_worker đọc `rtsp://frigate:8554/{}` cho cam1_VIRAT_1, cam_loiter, cam_fire; xử lý person/crowd/loiter/fire theo frame stream; không gọi `/api/<cam>/latest.jpg`.
       - Env runtime nằm ở docker-compose.yml service perception_worker: PERCEPTION_RTSP_TEMPLATE, PERCEPTION_FPS,
         PERCEPTION_PERSON_CAMERAS, PERCEPTION_FIRE_CAMERAS, FIRE_CONFIDENCE, FIRE_PERSIST_N/M, FIRE_CLEAR_SECONDS.
       - Topic realtime: perception/fire_smoke/<cam> (bbox từng frame), perception/alerts/fire_smoke (debounced alert/clear).
       - Dashboard vẽ bbox từ `realtime_fire_smoke`, chuẩn-hoá theo width/height hoặc inference_resolution.
       - ai_worker fire pump cũ đã tắt; không dùng ai_worker/alerts/fire_smoke trong compose mặc định.
   • Phân cụm ở BENCHMARK (chạy trong container dashboard, KHÔNG đọc file này): tham số CLUSTER_SIZE_RATIO_MIN / CLUSTER_DISTANCE_FACTOR / CLUSTER_CROWD_THRESHOLD nằm ở dashboard/backend/config.py + env service dashboard trong docker-compose.yml. PHẢI giữ ĐỒNG BỘ tay với SIZE_RATIO_MIN / DISTANCE_FACTOR / CROWD_THRESHOLD ở file này thì overlay benchmark mới khớp live. Đổi xong: docker compose up -d dashboard (rebuild nếu sửa app.py).
   • Overlay LOITERING & CROWD trên TILE LIVE ở DASHBOARD (frontend, sửa xong phải build lại dashboard):
       - TỪ 2026-06-24 (D7, ĐẢO lại D3/D5): bbox loitering VÀ crowd vẽ TRỰC TIẾP trên tile live WebRTC bằng SVG
         (cùng pattern overlay fire/smoke). Pop-up CameraInspectPanel ĐÃ XOÁ (+ helper inspectFrameUrl). Backend
         route /inspect/<cam>/frame.jpg GIỮ trong app.py nhưng frontend KHÔNG còn gọi (dead route, bỏ sau cũng được).
       - LOITERING (LoiterOverlay, dashboard/frontend/src/components/CameraTile.tsx): box amber #f59e0b + tag
         "LOITERING {dwell}s" đếm client-side mỗi 1s. bbox = px TUYỆT ĐỐI ở DETECT-RES camera (camera.width/height
         từ /dashboard/config, vd cam_loiter 1280x720) → viewBox=0 0 detectW detectH. Đánh đổi: box ở frame_time
         quá khứ ⇒ người DI CHUYỂN trễ ~nhịp update Frigate (~2.4s); loiterer demo gần đứng yên nên không lộ.
       - CROWD (CrowdOverlay, cam1_VIRAT_1 + cam_loiter): cluster_bbox đỏ #ef4444 nét đứt + box thành viên cụm
         (detections[i], i in cluster_member_indices) xanh #22c55e nét liền — style như overlay BenchmarkPanel.
         bbox = px tuyệt đối ở inference_resolution → viewBox theo inference_resolution. Box trễ người di chuyển
         (đánh đổi đã chọn). Crowd event-driven + cooldown 5s, KHÔNG có message clear ⇒ chỉ dọn bằng TTL.
       - dashboard/frontend/src/components/CameraTile.tsx -> LOITER_OVERLAY_TTL_MS (ms, hiện 10000): cửa sổ VIỀN cam
         màu cam + render box loitering khi active. CROWD_OVERLAY_TTL_MS (ms, hiện 7000): gate render box crowd.
       - dashboard/frontend/src/App.tsx -> LOITER_TTL_MS (ms, hiện 10000): TTL fallback dọn state loiter.
         CROWD_OVERLAY_TTL_MS (ms, hiện 7000): TTL dọn state crowdOverlays (PHẢI khớp giá trị ở CameraTile.tsx).
         TOAST_TTL_MS (ms, hiện 8000): thời gian sống của toast cảnh báo. FIRE_TTL_MS (ms, hiện 10000): TTL state fire.
     (state loiter vẫn được clear ngay bằng message active=false khi object end; TTL chỉ là dự phòng. Crowd KHÔNG có
      clear ⇒ TTL là cách duy nhất tắt box.)
    • EVENTS PANEL (EventsTimeline) — D8 2026-06-24: hằng số dashboard/frontend/src/App.tsx -> SHOW_RAW_FRIGATE_EVENTS
        (bool, hiện = false). false ⇒ timeline CHỈ hiện kết quả bài toán (crowd khi person_count>=threshold, loitering,
        fire/smoke) bắn realtime qua /ws; KHÔNG fetch/trộn lịch sử /api/events và KHÔNG ingest row frigate_event
        per-person/per-frame. Đổi = true để bật lại lịch sử Frigate thô (kèm refresh định kỳ EVENTS_REFRESH_MS).
        Sửa xong: docker compose up -d --build dashboard.
    • STREAM_CORE (Phase 1):
        - Các cấu hình (STREAM_CORE_CAMERAS, STREAM_CORE_RTSP_TEMPLATE, STREAM_CORE_FPS, v.v.) được đặt
          trong môi trường (environment) của service `stream_core` trong `docker-compose.yml`.
        - Sửa xong: `docker compose up -d stream_core` (recreate để nạp env mới).
    • PERCEPTION_WORKER (lane realtime chính):
        - Các cấu hình: PERCEPTION_CAMERAS, PERCEPTION_PERSON_CAMERAS, PERCEPTION_FIRE_CAMERAS,
          PERCEPTION_RTSP_TEMPLATE=rtsp://frigate:8554/{}, PERCEPTION_FPS, PERSON_DETECT_URL,
          FIRE_SMOKE_ENDPOINT_URL, PERSON_DETECT_TIMEOUT, FIRE_DETECT_TIMEOUT, PERCEPTION_JPEG_QUALITY,
          PERCEPTION_MIN_CONFIDENCE, LOITERING_DWELL_SECONDS, CROWD_THRESHOLD, CROWD_PERSIST_SECONDS,
          CLUSTER_SIZE_RATIO_MIN, CLUSTER_DISTANCE_FACTOR, FIRE_CONFIDENCE, FIRE_PERSIST_N/M,
          FIRE_CLEAR_SECONDS, PERCEPTION_TOPIC_PREFIX, PERCEPTION_STALE_SECONDS (30s để RTSP first-frame không bị giết sớm), PERCEPTION_RECONNECT_SECONDS.
        - PERCEPTION_ALLOW_HTTP_FETCH=false mặc định: chặn cấu hình nhầm sang `/api/<cam>/latest.jpg`.
        - Sửa xong: `docker compose up -d --build perception_worker dashboard` nếu sửa code; env-only thì bỏ `--build`.
=============================================================================
"""
import os

# --- MQTT ---
MQTT_HOST: str = os.getenv("MQTT_HOST", "mqtt")
MQTT_PORT: int = int(os.getenv("MQTT_PORT", "1883"))
MQTT_CLIENT_ID: str = os.getenv("MQTT_CLIENT_ID", "ai_worker")
MQTT_KEEPALIVE: int = int(os.getenv("MQTT_KEEPALIVE", "60"))

# --- Topic MQTT: nhận event từ Frigate / phát cảnh báo ra ---
EVENTS_TOPIC: str = os.getenv("EVENTS_TOPIC", "frigate/events")
ALERT_TOPIC: str = os.getenv("ALERT_TOPIC", "ai_worker/alerts/crowd")

# --- API Frigate ---
FRIGATE_API: str = os.getenv("FRIGATE_API", "http://frigate:5000")
FRIGATE_SNAPSHOT_TIMEOUT: int = int(os.getenv("FRIGATE_SNAPSHOT_TIMEOUT", "5"))
FRIGATE_SUBLABEL_TIMEOUT: int = int(os.getenv("FRIGATE_SUBLABEL_TIMEOUT", "5"))

# --- Bài toán AI đang chạy: nhận DANH SÁCH phân tách bằng dấu phẩy (vd "crowd,loitering") ---
#   Giữ lại chuỗi gốc cho log; phần xử lý dùng tập ACTIVE_PROBLEMS bên dưới.
ACTIVE_PROBLEM: str = os.getenv("ACTIVE_PROBLEM", "crowd")

# --- danh sách bài toán chạy ĐỒNG THỜI (phân tách bằng dấu phẩy), vd "crowd,loitering". loitering là logic thuần (không GPU), crowd cần crowd_gpu ---
ACTIVE_PROBLEMS: set[str] = {p.strip() for p in ACTIVE_PROBLEM.split(",") if p.strip()}

# --- chỉ chạy phát hiện loitering trên các camera này (phân tách bằng dấu phẩy). Rỗng = mọi camera. Mặc định chỉ cam_loiter để KHÔNG bắn nhầm trên cam1_VIRAT_1 ---
LOITERING_CAMERAS: list[str] = [
    c.strip()
    for c in os.getenv("LOITERING_CAMERAS", "cam_loiter").split(",")
    if c.strip()
]

# --- chỉ chạy đếm đám đông trên các camera này. Rỗng = mọi camera ---
CROWD_CAMERAS: list[str] = [
    c.strip()
    for c in os.getenv("CROWD_CAMERAS", "").split(",")
    if c.strip()
]

# --- Endpoint suy luận: mặc định = service GPU local crowd_gpu ---
#   Legacy env alias MODAL_ENDPOINT_URL giữ cho backward compatibility.
#   Mặc định là local endpoint http://crowd_gpu:8000/detect. Không có cloud deployment code.
MODAL_ENDPOINT_URL: str = os.getenv("MODAL_ENDPOINT_URL", "")
CROWD_INFERENCE_URL: str = os.getenv("CROWD_INFERENCE_URL", MODAL_ENDPOINT_URL or "http://crowd_gpu:8000/detect")
MODAL_REQUEST_TIMEOUT: int = int(os.getenv("MODAL_REQUEST_TIMEOUT", "90"))

# --- Circuit Breaker cho gọi Model ---
MODEL_FAILURE_THRESHOLD: int = int(os.getenv("MODEL_FAILURE_THRESHOLD", "3"))
MODEL_BREAKER_SECONDS: int = int(os.getenv("MODEL_BREAKER_SECONDS", "30"))

# --- Giảm dung lượng ảnh trước khi POST sang crowd_gpu ---
#   MAX_UPLOAD_BYTES: trần dung lượng. DOWNSCALE_*: vòng lặp hạ chất lượng JPEG rồi resize.
MAX_UPLOAD_BYTES: int = int(os.getenv("MAX_UPLOAD_BYTES", "2000000"))
DOWNSCALE_INITIAL_QUALITY: int = int(os.getenv("DOWNSCALE_INITIAL_QUALITY", "80"))
DOWNSCALE_QUALITY_STEP: int = int(os.getenv("DOWNSCALE_QUALITY_STEP", "15"))
DOWNSCALE_MIN_QUALITY: int = int(os.getenv("DOWNSCALE_MIN_QUALITY", "40"))
DOWNSCALE_MAX_ITERATIONS: int = int(os.getenv("DOWNSCALE_MAX_ITERATIONS", "12"))
DOWNSCALE_MIN_DIMENSION: int = int(os.getenv("DOWNSCALE_MIN_DIMENSION", "320"))

# --- Phân cụm đám đông ---
#   CROWD_THRESHOLD: số người tối thiểu trong 1 cụm để cảnh báo.
#   DISTANCE_FACTOR: hệ số khoảng cách gộp nhóm. SIZE_RATIO_MIN: tỷ lệ chiều cao tối thiểu giữa 2 người.
CROWD_THRESHOLD: int = int(os.getenv("CROWD_THRESHOLD", "3"))
DISTANCE_FACTOR: float = float(os.getenv("DISTANCE_FACTOR", "1.2"))
SIZE_RATIO_MIN: float = float(os.getenv("SIZE_RATIO_MIN", "0.8"))
CROWD_PERSIST_SECONDS: int = int(os.getenv("CROWD_PERSIST_SECONDS", "5"))
CROWD_ALERT_REPEAT_SECONDS: int = int(os.getenv("CROWD_ALERT_REPEAT_SECONDS", "15"))

# --- Lọc theo zone Frigate (cách nhau bằng dấu phẩy; rỗng = mọi zone). VD: entrance,parking_lot ---
TARGET_ZONES: list[str] = [
    z.strip()
    for z in os.getenv("TARGET_ZONES", "").split(",")
    if z.strip()
]

# --- Thời gian chờ tối thiểu (giây) giữa 2 lần xử lý CÙNG 1 camera ---
#   Ở bài toán loitering, dùng làm debounce theo từng object id (mỗi người chỉ
#   cảnh báo lại sau COOLDOWN_SECONDS).
COOLDOWN_SECONDS: int = int(os.getenv("COOLDOWN_SECONDS", "5"))

# --- Phát hiện lảng vảng (loitering) — THUẦN LOGIC, không gọi model GPU ---
#   Khi 1 người (object Frigate) hiện diện LIÊN TỤC >= LOITERING_DWELL_SECONDS giây
#   thì ai_worker bắn cảnh báo lên LOITERING_ALERT_TOPIC.
#   dwell = frame_time - start_time (tuổi đời track liên tục của Frigate), reset
#   tự nhiên khi object kết thúc (event `end`) và xuất hiện lại với id mới.
#   KHÔNG suy ra vắng mặt từ nhịp MQTT update: Frigate phát update rất thưa cho
#   người đứng yên (gap quan sát tới ~18s), dễ gây reset sai. Vắng mặt do event `end`.
#   Debounce theo object id dùng lại COOLDOWN_SECONDS ở trên.
LOITERING_DWELL_SECONDS: float = float(os.getenv("LOITERING_DWELL_SECONDS", "40"))
LOITERING_ALERT_TOPIC: str = os.getenv("LOITERING_ALERT_TOPIC", "ai_worker/alerts/loitering")

# --- TTL (giây) dọn dict trạng thái loitering (_last_loiter_alert) để tránh rò bộ nhớ. Giảm từ 3600 xuống 300 cho demo ---
LOITER_STATE_TTL_SECONDS: int = int(os.getenv("LOITER_STATE_TTL_SECONDS", "300"))

# =============================================================================
#  PHÁT HIỆN LỬA/KHÓI (fire_smoke) — RUNTIME MỚI NẰM Ở perception_worker
# =============================================================================
#   Compose mặc định KHÔNG còn cho ai_worker poll `{FRIGATE_API}/api/<cam>/latest.jpg`.
#   Fire/smoke realtime đọc RTSP restream trong perception_worker, gọi fire_gpu theo frame stream,
#   phát `perception/fire_smoke/<cam>` + `perception/alerts/fire_smoke` cho dashboard.
#   Các biến FIRE_* bên dưới chỉ còn legacy/backward-compatible nếu ai_worker cũ được bật tay;
#   chỉnh runtime thật trong docker-compose.yml service perception_worker.

# --- LEGACY ai_worker pump: danh sách camera nếu bật tay code cũ. Runtime mới dùng PERCEPTION_FIRE_CAMERAS ---
FIRE_CAMERAS: list[str] = [
    c.strip()
    for c in os.getenv("FIRE_CAMERAS", "cam_fire").split(",")
    if c.strip()
]

# --- Endpoint suy luận lửa/khói: service GPU local fire_gpu (cùng hợp đồng /detect, body = JPEG thô) ---
FIRE_SMOKE_ENDPOINT_URL: str = os.getenv("FIRE_SMOKE_ENDPOINT_URL", "http://fire_gpu:8000/detect")

# --- Topic MQTT phát cảnh báo lửa/khói ---
FIRE_SMOKE_ALERT_TOPIC: str = os.getenv("FIRE_SMOKE_ALERT_TOPIC", "ai_worker/alerts/fire_smoke")

# --- LEGACY ai_worker pump: không dùng trong compose mặc định; realtime dùng PERCEPTION_FPS ở perception_worker ---
FIRE_PUMP_FPS: float = float(os.getenv("FIRE_PUMP_FPS", "4"))

# --- Ngưỡng tin cậy (phía ai_worker) để 1 detection được TÍNH là hợp lệ. Cao hơn ngưỡng MODEL (DETECTION_THRESHOLD của fire_gpu) để chống dương-tính-giả (nắng≈lửa, hơi nước/mây≈khói) ---
#   TINH CHỈNH (eval video demo, model JJUNHYEOK/yolov8n_wildfire_detection): 0.40 bắt được
#   lửa bền (tín hiệu lửa >=0.54 từ ~17.6s tới hết) + khói (đỉnh ~0.42 quanh 16.6-16.8s) mà
#   KHÔNG có dương-tính-giả nào trước mốc lửa/khói. Hạ thấp hơn -> nhiễu lửa 0.06-0.19; cao hơn -> mất khói.
FIRE_CONFIDENCE: float = float(os.getenv("FIRE_CONFIDENCE", "0.40"))

# --- Bền vững N-trên-M (chống dương-tính-giả 1 khung): chỉ bắn alert khi >= FIRE_PERSIST_N trong FIRE_PERSIST_M khung pump gần nhất có detection (theo từng class fire/smoke) vượt FIRE_CONFIDENCE ---
#   TINH CHỈNH: N=2/M=5 — khói trên video demo thưa (chỉ ~2 khung >=0.40 trong cửa sổ) nên N=3 sẽ MẤT khói;
#   N=2/M=5 bắt khói@~16.8s + lửa@~17.6s, 0 dương-tính-giả. Tăng N -> chắc hơn nhưng dễ bỏ lỡ khói thưa.
FIRE_PERSIST_N: int = int(os.getenv("FIRE_PERSIST_N", "2"))
FIRE_PERSIST_M: int = int(os.getenv("FIRE_PERSIST_M", "5"))

# --- TTL clear (giây): sau FIRE_CLEAR_SECONDS không còn detection nào thì bắn message active=false để dashboard xoá overlay ---
FIRE_CLEAR_SECONDS: float = float(os.getenv("FIRE_CLEAR_SECONDS", "4"))

# --- Timeout (giây) request POST sang fire_gpu ---
FIRE_REQUEST_TIMEOUT: int = int(os.getenv("FIRE_REQUEST_TIMEOUT", "30"))