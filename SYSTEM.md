# SYSTEM.md — Smart_City: Tham chiếu kỹ thuật chi tiết

> **Mục đích:** nơi tra nhanh **logic + công thức + schema** mà `AGENTS.md`/`CLAUDE.md` chỉ nói lướt qua.
> File này **KHÔNG tự cập nhật** (giống `CLAUDE.md` / `config.py` / `ROADMAP.md`) — sửa tay.
>
> **Cập nhật khi:** (a) đổi công thức/tham số phân cụm (`smart_city_common/clustering.py`); (b) đổi schema payload MQTT / WebSocket / `/detect` / `/benchmark/run`; (c) thêm/bớt route dashboard hoặc service compose; (d) đổi port/env/volume wiring; (e) thêm `model_workers/<model>` hoặc lane `perception_worker` mới.
> **KHÔNG chép lại** thứ đã có ở `CLAUDE.md`/`AGENTS.md` (tổng quan, ràng buộc, file list), `config.py` (tham số ai_worker + comment), `ROADMAP.md` (milestone) — ở đây **chỉ chi tiết logic & schema**.
> Cập nhật lần cuối: **2026-07-01** (crowd/loiter/fire/LPR đều chạy realtime trong `perception_worker`; clustering gom về `smart_city_common`; bỏ pop-up toast + route `/inspect`).

---

## Mục lục
1. Luồng dữ liệu end-to-end
2. `perception_worker` — logic chi tiết (⭐ lane realtime chính)
3. `ai_worker` — legacy (gọn)
4. Hợp đồng `/detect` (model_workers)
5. Schema thông điệp (MQTT + WebSocket)
6. Dashboard (routes + proxy + benchmark + frontend)
7. Frigate `config.yml`
8. Bản đồ cổng & wiring
9. Kế hoạch & hướng phát triển (gọn)

---

## 1. Luồng dữ liệu end-to-end

**[DEFAULT] Realtime Stream AI**
```
Camera / MP4 / RTSP
  → Frigate/go2rtc (single RTSP restream per active cam)
  → perception_worker (OpenCV/FFmpeg đọc rtsp://frigate:8554/<cam>)
      ├─ person cams (PERCEPTION_FPS=8) → crowd_gpu :8000/detect → ByteTrack + crowd-cluster + loiter
      ├─ fire cams   (PERCEPTION_FPS=8) → fire_gpu  :8000/detect → fire/smoke N-of-M debounce
      └─ lpr cams    (LPR_FPS=3)        → lpr_gpu   :8000/detect → stable/confident plate-read debounce
  → MQTT "perception/#"
  → dashboard backend MQTT bridge → WebSocket /ws → SPA (overlay tile + Live Events panel)
```

**[LEGACY] Frigate-Triggered / Event-Driven**
```
Frigate "frigate/events" → ai_worker → ai_worker/alerts/# → dashboard
```
- `ai_worker` **inactive** trong compose (`ACTIVE_PROBLEM=`, `CROWD_CAMERAS=NONE_CAMERA`). Chỉ giữ fallback crowd/loiter event-driven nếu bật tay.
- Fire/smoke/LPR **không** chạy trong `ai_worker`; không poll `/api/<cam>/latest.jpg`.
- `stream_core` chỉ là metadata/benchmark helper (profile `realtime-benchmark`); bbox live chính lấy từ `perception_worker`.

---

## 2. `perception_worker` — logic chi tiết (`perception_worker/worker.py`)

Mỗi camera 1 thread `process_camera`: `FrameGrabber` (thread riêng, giữ **frame mới nhất**, buffer=1) → theo cadence từng task (`task_due(now,last,fps)`) encode JPEG (`PERCEPTION_JPEG_QUALITY`) → POST raw bytes sang model → publish MQTT. `watchdog` đánh dấu stale + ép reconnect nếu quá `PERCEPTION_STALE_SECONDS` (30s). `PERCEPTION_ALLOW_HTTP_FETCH=false` ⇒ `FrameGrabber` từ chối URL `http` (chặn nhầm `/latest.jpg`).

`frame_meta` (`base_payload`) chung mọi payload: `{source:"realtime", stream_source:<rtsp url>, camera, frame_id, wall_ts, monotonic_ts, width, height, model}`.

### 2.1 Person → Crowd + Loitering (`process_person_frame`, PERSON_CAMERAS)
1. POST JPEG → `crowd_gpu`; `person_detections()` lọc `class=="person"` + `confidence >= PERCEPTION_MIN_CONFIDENCE` (0.5).
2. **ByteTrack** (`supervision.ByteTrack`) → gán `track_id`; lưu `track_states[raw_id]` (`first_seen`, `last_seen`, `bbox`, `hits`, `loiter_active`). Publish `perception/objects/<cam>` (objects.v1, có `age_seconds`) + `perception/tracks/<cam>` (tracks.v1).
3. **Loitering** (`handle_loiter_alerts`): với mỗi track `age = now - first_seen`; nếu `age >= LOITERING_DWELL_SECONDS` (40s) → `loiter_active=True`, publish `perception/alerts/loitering` `active:true` (debounce theo track bằng `REALTIME_ALERT_REPEAT_SECONDS`=15s). Track **mất >** `PERCEPTION_TRACK_LOST_SECONDS` (2s): nếu đang `loiter_active` → publish `active:false` (clear), rồi xoá state.
4. **Crowd** (`handle_crowd`): gọi `compute_crowd_clusters` (§2.4). Publish `perception/crowd/<cam>` MỖI frame (`person_count = size cụm lớn nhất`, `threshold`, `clusters[]`, `detections[]`). Alert `perception/alerts/crowd` khi `max_cluster_size >= CROWD_THRESHOLD` **và** đã persist `CROWD_PERSIST_SECONDS` (5s); lặp lại mỗi `REALTIME_ALERT_REPEAT_SECONDS`; khi hết đủ ngưỡng → 1 message `active:false`.

### 2.2 Fire/Smoke (`process_fire_frame`, FIRE_CAMERAS)
- POST → `fire_gpu`; `fire_detections()` lọc `class in {fire,smoke}` + `confidence >= FIRE_CONFIDENCE` (0.40). Publish `perception/fire_smoke/<cam>` (fire_smoke.v1) mỗi frame.
- **Debounce N-of-M**: `fire_history` (deque `FIRE_PERSIST_M`=5); active nếu `fire_hits >= FIRE_PERSIST_N` (2) HOẶC `smoke_hits >= N`. Publish `perception/alerts/fire_smoke` `active:true` (lặp mỗi `REALTIME_ALERT_REPEAT_SECONDS`). Sau `FIRE_CLEAR_SECONDS` (4s) không còn → `active:false`.

### 2.3 LPR (`process_lpr_frame`, LPR_CAMERAS)
- Cadence riêng `LPR_FPS` (3) — không làm chậm person/fire (`FPS`=8). POST → `lpr_gpu`; `lpr_plates()` chuẩn hoá text `[A-Z0-9]`. Publish `perception/lpr/<cam>` (lpr.v1) mỗi frame.
- **Gate alert** (`perception/alerts/lpr`, chỉ `active:true`): với mỗi `plate_text` tốt nhất, bắn khi **(stable-read: `stable_hits >= LPR_STABLE_N` trong `lpr_history` `LPR_STABLE_M` frame gần nhất)** HOẶC **(confident-single: `ocr_confidence >= LPR_MIN_OCR_CONF`=0.6 và `det_confidence >= LPR_MIN_DET_CONF`=0.5)**. Suppress lặp theo text bằng `LPR_ALERT_REPEAT_SECONDS` (30s). Nhánh confident cần thiết vì xe chạy nhanh + biển nhỏ ⇒ mỗi biển thường chỉ đọc được 1 frame, stable-read thuần gần như không đạt.
- Alert kèm `plate_crop` (base64 `data:image/jpeg`, `crop_plate()` pad `LPR_CROP_PAD_RATIO`=0.12, tối đa `LPR_CROP_MAX_WIDTH`=320px). **CHỈ** nhét crop vào alert, KHÔNG vào `perception/lpr/<cam>` (tránh phình MQTT).

### 2.4 ⭐ CÔNG THỨC PHÂN CỤM ĐÁM ĐÔNG (`smart_city_common/clustering.py::compute_crowd_clusters`)
> **1 bản dùng chung** — import bởi CẢ `perception_worker` LẪN `dashboard/backend/app.py` (benchmark). Không còn bản sao/đồng-bộ-tay; chỉ **tham số** truyền vào khác nhau theo env mỗi service.

Input: `detections[i].bbox = [x1,y1,x2,y2]` (**pixel tuyệt đối** theo độ phân giải inference). Tham số: `size_ratio_min`, `distance_factor`, `min_cluster_size`.

**Bước 1 — điểm chân & chiều cao:** `foot_i=((x1+x2)/2, y2)`, `height_i=max(y2-y1, 1.0)`.

**Bước 2 — `is_neighbor(i,j)`:**
```
if min(h_i,h_j)/max(h_i,h_j) < size_ratio_min:  return False   # (a) cổng phối cảnh/độ sâu
dist = euclid(foot_i, foot_j)                                   # (b) khoảng cách chân (px)
return dist <= (h_i+h_j)/2 * distance_factor                   # (c) ngưỡng thích ứng theo chiều cao
```
- (a) 2 người cao lệch nhiều ⇒ khác lớp xa-gần ⇒ không cùng cụm (`size_ratio_min`=0.8 ⇒ ≥80%).
- (c) người gần (box to) được phép cách xa hơn theo pixel mà vẫn chung cụm (`distance_factor`=1.2).

**Bước 3 — gom cụm:** đồ thị vô hướng (cạnh = `is_neighbor`), **BFS** tìm thành phần liên thông (gom bắc cầu). Mỗi cụm `{size, bbox=[min x1,min y1,max x2,max y2], member_indices}`.

**Bước 4 — lọc & sắp:** giữ cụm `size >= min_cluster_size` (=`CROWD_THRESHOLD`, 3), sort giảm dần theo size. Trả **list** (cụm lớn nhất ở [0]).

> Tham số sống ở env: perception (`CLUSTER_SIZE_RATIO_MIN`/`CLUSTER_DISTANCE_FACTOR`/`CROWD_THRESHOLD`) và dashboard-benchmark (`CLUSTER_*`/`CLUSTER_CROWD_THRESHOLD`). Cùng hàm ⇒ chỉ cần **số** khớp thì overlay benchmark khớp live.

---

## 3. `ai_worker` — legacy (gọn) (`ai_worker/worker.py`)
- Chạy khi bật tay: nghe `frigate/events`, dispatch crowd (`type=new`) + loitering (`new/update/end`) theo `ACTIVE_PROBLEMS`. Crowd: fetch `/api/events/{id}/snapshot.jpg` → downscale (`config.py`) → POST `MODAL_ENDPOINT_URL` → phân cụm → alert `ai_worker/alerts/crowd` + `set_sub_label`. Loitering: dwell = `frame_time - start_time`; alert `ai_worker/alerts/loitering`.
- **Mặc định inactive** (`ACTIVE_PROBLEM=`, `CROWD_CAMERAS=NONE_CAMERA`). Tham số + comment chi tiết ở root `config.py`. Fire/smoke pump đã gỡ; không poll `latest.jpg`.

---

## 4. Hợp đồng `/detect` (mọi `model_workers/<m>`)

Chung: **FastAPI** :8000 (không publish ra host), `GET /health`, `GET /metrics`, `POST /detect` (**body = raw JPEG**, `await request.body()`, không multipart). `bbox` = **[x1,y1,x2,y2] pixel tuyệt đối** (xyxy) theo ảnh gửi lên.

### 4.1 `crowd_gpu` = RF-DETR-Large (`rf_detr_large/server.py`)
- `RFDETRLarge()`, **CUDA bắt buộc**. `model="rfdetr-large"`. Lọc `class_id == person_id`. Ngưỡng env `DETECTION_THRESHOLD` (0.5).
- Response: `{person_count, detections:[{bbox,confidence}], model}`.

### 4.2 `locate_gpu` = LocateAnything-3B (`locate_anything_3b/server.py`, profile `benchmark`)
- VLM tự hồi quy `nvidia/LocateAnything-3B`, **FP16**, CUDA. `model="locate-anything-3b"`. Prompt cố định "…person.". `generate` **bắt buộc** `generation_mode="hybrid"`, `do_sample=True`, `temperature=0.7`, `top_p=0.9`, `repetition_penalty=1.1`, `max_new_tokens=2048`. Parse regex `<box>…</box>` /1000 × `image.size`; `confidence` luôn 1.0. Pin `transformers==4.57.1`. Cache `/opt/hf`. ~18–20s/frame.

### 4.3 `fire_gpu` = YOLOv8 fire+smoke (`fire_smoke/server.py`)
- `ultralytics YOLO(weights).predict(conf=DETECTION_THRESHOLD, imgsz=640, device="cuda")`. `model` = id repo. Tải PRETRAINED lần lượt `FIRE_MODEL_REPOS` → repo đầu tải được + có class fire/smoke (hiện `JJUNHYEOK/yolov8n_wildfire_detection`). Cache `/opt/hf` (`./model_cache/fire_hf`).
- Map class→fire/smoke bằng substring. `DETECTION_THRESHOLD` (0.25) = ngưỡng MODEL; lọc chặt hơn ở perception (`FIRE_CONFIDENCE`).
- **Response schema RIÊNG:** `{detections:[{bbox,confidence,class:"fire"|"smoke"}], fire_count, smoke_count, model}` (không `person_count`).

### 4.4 `lpr_gpu` = YOLO11 plate (GPU) + fast-plate-ocr (CPU) (`lpr_plate_ocr/server.py`)
- **Detector = `ultralytics` YOLO11 trên CUDA** (`model.to("cuda")`, `predict(device="cuda")`). Tải weights lần lượt `CANDIDATES` (default `morsetechlab/yolov11-license-plate-detection`/`license-plate-finetune-v1s.pt`; override `LPR_DETECTOR_REPO`/`LPR_DETECTOR_FILE`). `LPR_DETECTOR_CONFIDENCE` (0.35), `LPR_DETECTOR_IMGSZ` (640). CUDA **bắt buộc** (từ chối CPU fallback).
- **OCR = `fast_plate_ocr.LicensePlateRecognizer(hub_ocr_model="cct-xs-v2-global-model", device="cpu")`** (onnxruntime CPU). Chạy trên crop từng box; `text` uppercase `[A-Z0-9]`, `ocr_confidence` = mean char prob.
- Cache HF `/opt/hf` (`./model_cache/lpr_hf`). `GET /health` → `{status, cuda:true, device:<gpu>, model}`. `model = "<detector-repo-last>+cct-xs-v2-global-model"` (vd `yolov11-license-plate-detection+cct-xs-v2-global-model`).
- **Response schema `lpr.v1`:**
```json
{ "schema":"lpr.v1", "plate_count":1,
  "plates":[{"bbox":[x1,y1,x2,y2],"det_confidence":0.9,"text":"30N73993","raw_text":"30N73993","ocr_confidence":0.99,"confidence":0.9}],
  "model":"yolov11-license-plate-detection+cct-xs-v2-global-model" }
```
> **Vì sao torch/CUDA (không fast-alpr ONNX YOLOv9):** `onnxruntime-gpu` không có wheel aarch64 + không có YOLOv9 plate `.pt` ungated trên HF ⇒ ONNX không lên được GPU. Detector chuyển YOLO11 torch/CUDA; OCR CCT rất nhẹ nên để CPU. Thư mục cũ `model_cache/lpr` (cache fast-alpr ONNX cũ) là RÁC còn sót — có thể xoá (root-owned ⇒ cần sudo, hand cho user).

---

## 5. Schema thông điệp

### 5.1 MQTT (perception_worker publish)
Tất cả có `frame_meta` (§2). Dưới đây liệt kê field ĐẶC THÙ.

| Topic | Schema/loại | Field chính |
|-------|-------------|-------------|
| `perception/objects/<cam>` | objects.v1 | `objects:[{id,track_id,class,bbox,confidence,age_seconds}]` |
| `perception/tracks/<cam>` | tracks.v1 | `tracks:[{track_id,class,bbox,confidence,first_seen,last_seen,age_seconds,hits,state}]` |
| `perception/crowd/<cam>` | crowd.v1 | `person_count`(=size cụm lớn nhất), `threshold`, `clusters:[{size,bbox,member_indices}]`, `detections:[{bbox,confidence}]` |
| `perception/fire_smoke/<cam>` | fire_smoke.v1 | `detections:[{bbox,confidence,class}]`, `fire_count`, `smoke_count` |
| `perception/lpr/<cam>` | lpr.v1 | `plates:[{bbox,det_confidence,text,raw_text,ocr_confidence,confidence}]`, `plate_count` |

**Alerts (debounced):**
```jsonc
// perception/alerts/crowd (active)
{ ...frame_meta, "timestamp","active":true, "person_count","total_persons","threshold",
  "cluster_bbox":[..], "cluster_member_indices":[..], "clusters":[..], "detections":[..],
  "inference_resolution":[w,h] }              // clear: { ...frame_meta,"active":false,"person_count":0 }

// perception/alerts/loitering (active)
{ ...frame_meta, "timestamp","active":true, "object_id":"<cam>:<track>", "dwell_time":55.4, "bbox":[..] }
                                              // clear: { ...,"active":false, "dwell_time":.., "bbox":null }

// perception/alerts/fire_smoke (active)
{ ...frame_meta, "timestamp","active":true, "fire_count","smoke_count","detections":[..],
  "inference_resolution":[w,h] }              // clear: { ...,"active":false,"fire_count":0,"smoke_count":0,"detections":[] }

// perception/alerts/lpr (chỉ active — dashboard dọn bằng TTL, KHÔNG có clear)
{ ...frame_meta, "timestamp","active":true, "plate_text","bbox":[..], "det_confidence","ocr_confidence",
  "confidence","stable_hits","inference_resolution":[w,h], "plate_crop":"data:image/jpeg;base64,.." }
```
> bbox là px TUYỆT ĐỐI ở `inference_resolution` (= `width`/`height` của frame gửi model). Loitering bbox lấy ở detect-res camera. Overlay chia theo resolution tương ứng.

### 5.2 WebSocket `/ws` (dashboard → trình duyệt)
Backend `map_mqtt_topic()` bọc `{type, data}` rồi fan-out (read-only). Mapping:

| Topic MQTT | `type` |
|------------|--------|
| `perception/objects/*` | `realtime_objects` |
| `perception/tracks/*` | `realtime_tracks` |
| `perception/crowd/*` | `realtime_crowd` |
| `perception/fire_smoke/*` | `realtime_fire_smoke` |
| `perception/lpr/*` | `realtime_lpr` |
| `perception/alerts/*` (crowd/loiter/fire/lpr) | **`realtime_alert`** (frontend phân biệt theo field payload) |
| `stream_core/frames/*` | `realtime_frame` |
| `ai_worker/alerts/fire_smoke` | `fire_smoke_alert` (suppress nếu vừa thấy realtime fire ≤ `REALTIME_SUPPRESS_SECONDS`) |
| `ai_worker/alerts/loitering` | `loitering_alert` (legacy) |
| `ai_worker/alerts/*` | `crowd_alert` (legacy) |
| `frigate/events` | `frigate_event` |

> **Mọi alert perception → 1 type `realtime_alert`.** Frontend `handleMessage` phân loại theo field: `person_count`→crowd, `object_id`/`dwell_time`→loitering, `fire_count`/`smoke_count`→fire, `plate_text`→lpr. (Có hàm `infer_realtime_alert_type` trong app.py nhưng **không** dùng cho map — để tham khảo.)

---

## 6. Dashboard (`dashboard/`)

### 6.1 Routes backend (FastAPI, `dashboard/backend/app.py`, `uvicorn :8080`)
| Route | Method | Vai trò |
|-------|--------|---------|
| `/dashboard/config` | GET | `{stale_seconds, alert_topic, cameras:[{name,enabled,width,height}]}` (chỉ cam `enabled`, đọc từ Frigate `/api/config`) |
| `/benchmark/run` | POST | so sánh đa model (§6.3) |
| `/health` · `/metrics` | GET | health đơn giản + đếm mqtt/ws |
| `/system/health` | GET | tổng hợp: frigate, ai_worker(:8091), perception_worker(:8093), crowd_gpu, fire_gpu, lpr_gpu → `{status, details}` |
| `/ws` | WS | đẩy các type §5.2 |
| `/api/webrtc` | POST | signaling go2rtc → `http://frigate:1984/api/webrtc` |
| `/api/{path}` | mọi method | proxy Frigate; **`proxy_frigate_api` chỉ cho GET/HEAD** (+ POST /api/webrtc), method ghi khác → **405** |
| `/live/{path}` | WS | relay 2 chiều video WS (webrtc/mse/jsmpeg) sang Frigate |
| `/{full_path}` | GET | phục vụ SPA build (`frontend/dist`, mount `/assets`), fallback `index.html` |

- MQTT subscribe: `ai_worker/alerts/#`, `frigate/events`, `stream_core/frames/#`, `perception/{objects,tracks,crowd,fire_smoke,lpr,alerts}/#`. **Không publish.**
- **Không có route `/inspect`** (đã bỏ cùng `CameraInspectPanel`). Stateless (không SQLite).

### 6.2 Reverse-proxy Frigate (`frigate_proxy.py`)
- **Read-only:** `proxy_frigate_api` chặn mọi method ≠ GET/HEAD → 405 (ngoại lệ `POST /api/webrtc` → `:1984`). Lọc hop-by-hop headers, timeout 30s. `proxy_frigate_ws`: nâng cấp WS, relay 2 chiều, tự dọn.

### 6.3 `/benchmark/run`
- Body `{camera?}` (mặc định cam enabled đầu). Lấy 1 frame `GET /api/{cam}/latest.jpg` (5s). Đọc env `BENCHMARK_ENDPOINTS` (`[{name,url}]`) → POST raw JPEG **song song** (`asyncio.gather`, 90s/req). Mỗi model: `latency_ms`, `person_count=len(detections)`, chạy `compute_crowd_clusters` (§2.4, tham số `CLUSTER_*` env dashboard) → `max_cluster_size`, `cluster_bbox`, `cluster_member_indices`.
- ⚠️ `locate_gpu` mặc định Exited (profile `benchmark`) ⇒ lỗi name-resolution nếu chưa `docker compose up -d locate_gpu` (chờ load ~30–60s).
- Response: `{frame_b64, frame_width, frame_height, results:[{model,latency_ms,person_count,max_cluster_size,cluster_bbox,cluster_member_indices,detections,error}]}`.

### 6.4 Frontend (React 19 + TS + Vite + Tailwind, `dashboard/frontend/src/`)
- **Tabs:** `live` (mặc định) | `benchmark`. State ở `App.tsx`: `crowdOverlays`, `loiters`, `fires`, `lprs`, `timeline`. **Không còn `toasts`/pop-up.**
- **Prune TTL** (interval 500ms): crowd/loiter/fire theo `OVERLAY_TTL_MS`=1200; lpr theo `LPR_OVERLAY_TTL_MS`=2000. `MAX_TIMELINE`=80.
- **`handleMessage`:** `realtime_objects` (cập nhật bbox loiter từ object `age_seconds>=40`), `realtime_crowd`, `realtime_fire_smoke`, `realtime_lpr`, `realtime_alert` (phân theo field → crowd/loiter/fire/lpr; đẩy timeline), legacy `crowd_alert`.
- **Player live phân tầng** (`lib/liveStream.ts`): **webrtc** thật (`RTCPeerConnection` → `POST /api/webrtc?src=`) → fallback **snapshot** (poll `/api/{cam}/latest.jpg?h=360` ~800ms). Nút **Snap/Live** ép snapshot; nút 📷 tải frame sạch.
- **Overlay SVG trên tile (`CameraTile.tsx`)** — cùng pattern `viewBox` + `preserveAspectRatio="xMidYMid slice"` (khớp `<video> object-cover`):
  - **FireOverlay** (`cam_fire`): fire đỏ `#ef4444`, smoke lam `#3b82f6` + nhãn class%; viewBox = `inference_resolution`.
  - **LoiterOverlay**: box amber `#f59e0b` + tag `LOITERING {dwell}s` (giây tick client-side); viewBox = **detect-res** (`detectWidth/Height` từ `/dashboard/config`).
  - **CrowdOverlay** (`cam1_VIRAT_1`+`cam_loiter`): `cluster_bbox` đỏ `#ef4444` nét đứt + box thành viên xanh `#22c55e`; render từ `clusters[]` (realtime) hoặc `clusterBbox`+`memberIndices` (legacy); chỉ khi `personCount >= threshold`; viewBox = `inference_resolution`.
  - **LprOverlay** (`cam_lpr`): box cyan `#06b6d4` + text + conf%; viewBox = `inference_resolution`.
  - Viền tile theo ưu tiên fire > crowd > loiter > lpr; badge dưới tile (CROWD n + số nhóm, FIRE/SMOKE, LOITERING, LPR n).
- **`EventsTimeline`** (panel phải, bề mặt cảnh báo DUY NHẤT): chỉ hiện kết quả bài toán realtime; **`LprEventRow`** = card riêng cho `kind:"lpr"` (ảnh `plate_crop`, text mono, det/ocr/conf%). Không fetch `/api/events`, không render `frigate_event` thô.
- `useLiveChannel`: WS `/ws` + reconnect backoff 1→15s.

---

## 7. Frigate `config.yml`
- **Detector:** `openvino` CPU; model `/openvino-model/ssdlite_mobilenet_v2.xml` 300×300 `nhwc`/`bgr`, labelmap `coco_91cl_bkgr.txt`.
- **Objects track:** `person, bicycle, car, motorcycle, bus, truck`.
- **go2rtc streams:** `cam1_VIRAT_1`, `cam_loiter`, `cam_fire` (`cam_fire_baseline.mp4`), `cam_lpr` (`Ground-level_off-side_road.mp4`) = **exec ffmpeg `-stream_loop -1 … -c copy … {{output}}`** (nguồn đơn always-on, cần `GO2RTC_ALLOW_ARBITRARY_EXEC=true`, escape `{{output}}`). `cam4_entry_area`/`cam6_warehouse_door` = stream `ffmpeg:` thường, **không** có block `cameras.*` ⇒ không detect.
- **Cameras (`ffmpeg.inputs` đọc `rtsp://127.0.0.1:8554/<cam>`, `preset-rtsp-restream`, roles detect+record):**
  - `cam1_VIRAT_1`: 1920×1080, `detect.fps=5`.
  - `cam_loiter`: 1280×720, `detect.fps=8`, `stationary.interval=5`/`threshold=50`, `motion.threshold=25`/`contour_area=5` (bắt người nhỏ/xa + freshen box người đứng yên).
  - `cam_fire`: 1280×720, `detect.fps=5`.
  - `cam_lpr`: 1920×1080, `detect.fps=2`.
- **Record (0.17 tiered):** `continuous` + `alerts.retain` + `detections.retain` ≈ 0.0007 ngày (~60s) mode `all` — giữ rất ngắn để storage tự dọn. Snapshots on (timestamp+bbox), retain ~60s.
- **Zones:** chưa định nghĩa ⇒ `TARGET_ZONES` rỗng.

---

## 8. Bản đồ cổng & wiring

| Service | Host→Container | Vai trò |
|---------|---------------|---------|
| mqtt | 1883→1883 | broker (persistence disabled) |
| frigate | 5000→5000 | UI + HTTP API |
| frigate | 8556→8554 | RTSP |
| frigate | 8555→8555 tcp/udp | WebRTC |
| frigate | *(nội bộ)* 1984 | go2rtc API (đích `/api/webrtc`) |
| dashboard | 8080→8080 | UI control-room |
| stream_core | 8092→8092 | metrics/health (profile `realtime-benchmark`) |
| perception_worker | 8093→8093 | metrics/health |
| crowd_gpu / fire_gpu / lpr_gpu | *(không publish)* | `:8000/detect` (gọi nội bộ) |
| locate_gpu | *(không publish)* | `:8000/detect` (profile `benchmark`) |
| ai_worker | *(không publish)* | client MQTT legacy (inactive) |

**Điểm nối (compose env):**
- `perception_worker`: `PERCEPTION_RTSP_TEMPLATE=rtsp://frigate:8554/{}`, `PERCEPTION_CAMERAS=cam1_VIRAT_1,cam_loiter,cam_fire,cam_lpr`, `PERCEPTION_PERSON_CAMERAS=cam1_VIRAT_1,cam_loiter`, `PERCEPTION_FIRE_CAMERAS=cam_fire`, `PERCEPTION_LPR_CAMERAS=cam_lpr`, `PERCEPTION_FPS=8`, `LPR_FPS=3`, `PERSON_DETECT_URL=crowd_gpu`, `FIRE_SMOKE_ENDPOINT_URL=fire_gpu`, `LPR_ENDPOINT_URL=lpr_gpu`.
- `ai_worker`: `ACTIVE_PROBLEM=` (inactive), `CROWD_CAMERAS=NONE_CAMERA`, `MODAL_ENDPOINT_URL=http://crowd_gpu:8000/detect`.
- `dashboard`: `BENCHMARK_ENDPOINTS=[{rfdetr-large→crowd_gpu},{locate-anything-3b→locate_gpu}]`, `CLUSTER_*` (khớp perception), `REALTIME_OVERLAY_TTL_MS=1200`.
- GPU 1 chiếc chia nhiều container (`frigate`, `crowd_gpu`, `fire_gpu`, `lpr_gpu`, + `locate_gpu` khi benchmark) cùng `device_requests gpu count:1`; `mem_limit` xem `docker-compose.yml`.
- **`frigate.cpus=14`** (trần 1400%) — detect CPU-bound (OpenVINO SSDLite); nút thắt scale là CPU detect, không phải GPU/RAM.

---

## 9. Kế hoạch & hướng phát triển (gọn — chi tiết ở `ROADMAP.md`)
- **Mẫu mới (ưu tiên):** `perception_worker` đọc RTSP liên tục → detector/tracker chung → task engines → MQTT `perception/#` → dashboard. Thêm bài toán mới = `model_workers/<m>/` + lane trong `perception_worker` (không thêm task Frigate-triggered mới nếu cần realtime/state).
- **Còn lại:** Re-ID đa camera · Nhận diện khuôn mặt (SCRFD→ArcFace→FAISS). Crowd nâng cao: pose→ST-GCN, PAR, homography/BEV. Hạ tầng: detect substream + record mainstream, NVDEC HWAccel, MQTT store-and-forward, GPU batching.
- **LPR cải thiện:** nâng `LPR_DETECTOR_IMGSZ` hoặc plate-tracking để bớt nhiễu OCR biển nhỏ/xa.
