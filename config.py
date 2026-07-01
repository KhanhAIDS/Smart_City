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
   • cam1_VIRAT_1, cam_loiter, cam_fire, cam_lpr đều single go2rtc exec + RTSP restream.
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
   • LPR / NHẬN DIỆN BIỂN SỐ (realtime RTSP, KHÔNG fetch latest.jpg):
       - Camera cam_lpr -> config.yml, nguồn videos/Ground-level_off-side_road.mp4, detect 1920x1080@2fps.
       - Model worker -> model_workers/lpr_plate_ocr, service lpr_gpu (GPU GB10). DETECTOR chạy PyTorch/CUDA (ultralytics YOLO), OCR fast-plate-ocr chạy CPU (onnxruntime). Dockerfile theo recipe GB10: cuda12.8-runtime + torch2.11.0+cu128 + pin nvidia-cuda-nvrtc-cu12==12.9.86 SAU torch. Weights cache host: ./model_cache/lpr_hf:/opt/hf.
       - Model mặc định: detector YOLO11 morsetechlab/yolov11-license-plate-detection (file license-plate-finetune-v1s.pt, 1 class License_Plate) + OCR cct-xs-v2-global-model (CPU). Env service lpr_gpu: LPR_OCR_MODEL, LPR_DETECTOR_CONFIDENCE, LPR_DETECTOR_IMGSZ; override detector qua LPR_DETECTOR_REPO/LPR_DETECTOR_FILE (server.py có fallback list). LƯU Ý: không có YOLOv9 plate .pt ungated trên HF + onnxruntime-gpu không có wheel aarch64 → bỏ fast-alpr[onnx] CPU, chuyển detector sang torch/CUDA (YOLO11) để lên GPU; OCR CCT rất nhẹ nên để CPU.
       - Env runtime nằm ở docker-compose.yml service perception_worker: PERCEPTION_LPR_CAMERAS, LPR_ENDPOINT_URL, LPR_FPS (hiện 3 — GPU ~21ms; hạ từ 5 để giảm tải, vẫn đủ frame cho gate alert), LPR_DETECT_TIMEOUT, LPR_STABLE_N/M, LPR_ALERT_REPEAT_SECONDS, LPR_MIN_OCR_CONF, LPR_MIN_DET_CONF.
       - GATE ALERT: bắn khi (cùng plate_text đạt LPR_STABLE_N/M) HOẶC (1 lần đọc tự tin: ocr_confidence>=LPR_MIN_OCR_CONF=0.6 và det_confidence>=LPR_MIN_DET_CONF=0.5), repeat-suppress theo text 30s. Lý do có nhánh confident: cảnh giao thông xe chạy nhanh + biển nhỏ → mỗi biển thường chỉ đọc được 1 frame với text khác nhau, stable-read thuần gần như không bao giờ đạt (đo 2026-07-01). Muốn ít nhiễu hơn: nâng LPR_MIN_OCR_CONF hoặc dựng plate-tracking (future).
       - Ảnh biển số CROP nhúng base64 vào alert perception/alerts/lpr (field plate_crop, data:image/jpeg) — CHỈ alert stable-read, KHÔNG nhét vào perception/lpr/<cam> để tránh phình MQTT. Env (mặc định ở perception_worker/worker.py, override ở docker-compose service perception_worker): LPR_CROP_PAD_RATIO=0.12, LPR_CROP_MAX_WIDTH=320, LPR_CROP_JPEG_QUALITY=80. Sửa xong: docker compose up -d --build perception_worker.
       - Topic realtime: perception/lpr/<cam> (bbox+OCR từng frame), perception/alerts/lpr (stable-read alert + plate_crop, không active=false; dashboard dọn bằng TTL).
   • Phân cụm ở BENCHMARK (chạy trong container dashboard): tham số CLUSTER_SIZE_RATIO_MIN / CLUSTER_DISTANCE_FACTOR /
     CLUSTER_CROWD_THRESHOLD đọc TỪ ENV service dashboard trong docker-compose.yml (KHÔNG còn file dashboard/backend/config.py).
     CÔNG THỨC phân cụm là 1 bản DÙNG CHUNG: smart_city_common/clustering.py::compute_crowd_clusters — import bởi CẢ
     perception_worker LẪN dashboard/backend/app.py (không fork lại). Chỉ THAM SỐ cần khớp tay với perception_worker
     (CLUSTER_SIZE_RATIO_MIN / CLUSTER_DISTANCE_FACTOR / CROWD_THRESHOLD) để overlay benchmark khớp live. Đổi: docker compose up -d --build dashboard.
   • OVERLAY trên TILE LIVE (frontend, sửa xong build lại dashboard) — 4 overlay SVG trong dashboard/frontend/src/components/CameraTile.tsx,
     cùng pattern viewBox + preserveAspectRatio="xMidYMid slice" (khớp <video> object-cover). KHÔNG còn pop-up: component
     AlertToast + toast state đã XOÁ (2026-07-01: chồng lên panel event, không có nút tắt). Route /inspect + CameraInspectPanel cũng đã bỏ.
       - LOITERING (LoiterOverlay): box amber #f59e0b + tag "LOITERING {dwell}s" đếm client-side. bbox = px TUYỆT ĐỐI ở
         DETECT-RES camera (camera.width/height từ /dashboard/config) → viewBox=0 0 detectW detectH. Nguồn: realtime_objects
         (bbox track age>=40s) + alert loitering (perception/alerts/loitering, field dwell_time). Người di chuyển ⇒ box trễ ~nhịp update.
       - CROWD (CrowdOverlay, cam1_VIRAT_1 + cam_loiter): cluster_bbox đỏ #ef4444 nét đứt + box thành viên cụm xanh #22c55e
         nét liền. bbox px tuyệt đối ở inference_resolution → viewBox theo inference_resolution. Nguồn: realtime_crowd
         (perception/crowd/<cam>, clusters[] mỗi frame) + alert crowd. Chỉ vẽ khi person_count>=threshold.
       - FIRE/SMOKE (FireOverlay, cam_fire): fire đỏ #ef4444, smoke lam #3b82f6 + nhãn class%; viewBox theo inference_resolution.
       - LPR (LprOverlay, cam_lpr): box cyan #06b6d4 + text OCR + conf%; viewBox theo inference_resolution. Nguồn: realtime_lpr
         (perception/lpr/<cam>) + alert lpr (perception/alerts/lpr, kèm plate_crop base64).
       - Hằng số TTL trong dashboard/frontend/src/App.tsx: OVERLAY_TTL_MS (hiện 1200) dọn state crowd/loiter/fire;
         LPR_OVERLAY_TTL_MS (hiện 2000) dọn state lpr; MAX_TIMELINE (hiện 80) số dòng event tối đa. Env backend/perception
         REALTIME_OVERLAY_TTL_MS=1200 + REALTIME_ALERT_REPEAT_SECONDS=15 (docker-compose.yml). Sửa TS xong: docker compose up -d --build dashboard.
    • EVENTS PANEL (EventsTimeline; App.tsx đẩy vào timeline khi nhận alert /ws) — CHỈ hiện kết quả bài toán realtime
        (crowd khi person_count>=threshold, loitering, fire/smoke, lpr). KHÔNG fetch/trộn lịch sử /api/events và KHÔNG render
        row frigate_event per-person/per-frame. LPR có card riêng (LprEventRow): ảnh crop biển số (plate_crop), text OCR
        (mono) + det/ocr/conf %. Đây là bề mặt cảnh báo DUY NHẤT (đã bỏ toast). Sửa xong: docker compose up -d --build dashboard.
    • STREAM_CORE (Phase 1):
        - Các cấu hình (STREAM_CORE_CAMERAS, STREAM_CORE_RTSP_TEMPLATE, STREAM_CORE_FPS, v.v.) được đặt
          trong môi trường (environment) của service `stream_core` trong `docker-compose.yml`.
        - Sửa xong: `docker compose up -d stream_core` (recreate để nạp env mới).
    • PERCEPTION_WORKER (lane realtime chính):
        - Các cấu hình: PERCEPTION_CAMERAS, PERCEPTION_PERSON_CAMERAS, PERCEPTION_FIRE_CAMERAS, PERCEPTION_LPR_CAMERAS,
          PERCEPTION_RTSP_TEMPLATE=rtsp://frigate:8554/{}, PERCEPTION_FPS, LPR_FPS, PERSON_DETECT_URL,
          FIRE_SMOKE_ENDPOINT_URL, LPR_ENDPOINT_URL, PERSON_DETECT_TIMEOUT, FIRE_DETECT_TIMEOUT, LPR_DETECT_TIMEOUT,
          PERCEPTION_JPEG_QUALITY, PERCEPTION_MIN_CONFIDENCE, LOITERING_DWELL_SECONDS, CROWD_THRESHOLD,
          CROWD_PERSIST_SECONDS, CLUSTER_SIZE_RATIO_MIN, CLUSTER_DISTANCE_FACTOR, FIRE_CONFIDENCE, FIRE_PERSIST_N/M,
          FIRE_CLEAR_SECONDS, LPR_STABLE_N/M, LPR_ALERT_REPEAT_SECONDS, DETECTOR_HEALTH_TIMEOUT, PERCEPTION_TOPIC_PREFIX,
          PERCEPTION_STALE_SECONDS (30s để RTSP first-frame không bị giết sớm), PERCEPTION_RECONNECT_SECONDS.
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