# SYSTEM.md — Smart_City: Tham chiếu kỹ thuật chi tiết

> **Mục đích:** nơi tra nhanh **logic + công thức + schema** mà 3 file kia chỉ nói lướt qua.
> File này **KHÔNG tự cập nhật** (giống `CLAUDE.md` / `config.py` / `ROADMAP.md`) — sửa tay.
>
> **Cập nhật khi:** (a) đổi công thức/tham số phân cụm trong `ai_worker/worker.py`; (b) đổi schema payload MQTT / WebSocket / `/detect` / `/benchmark/run`; (c) thêm/bớt route dashboard hoặc service compose; (d) đổi port/env/volume wiring; (e) thêm `model_workers/<model>` mới.
> **KHÔNG chép lại** thứ đã có ở `CLAUDE.md` (tổng quan, ràng buộc, file list), `config.py` (tham số ai_worker + comment), `ROADMAP.md` (5 bài toán, deferred) — ở đây **chỉ chi tiết logic & schema**.
> Cập nhật lần cuối: **2026-06-30** (realtime-stream-first: perception_worker RTSP lane default, ai_worker fire pump removed).

---

## Mục lục
1. Luồng dữ liệu end-to-end
2. `ai_worker` — logic chi tiết (⭐ công thức phân cụm ở §2.4)
3. Hợp đồng `/detect` (model_workers)
4. Schema thông điệp (MQTT alert + WebSocket)
5. Dashboard (backend routes + proxy + benchmark + frontend)
6. Frigate `config.yml` — chi tiết
7. Bản đồ cổng & wiring
8. Kế hoạch tiếp theo (gọn)
9. Hướng phát triển (gọn)

---

## 1. Luồng dữ liệu end-to-end

**[DEFAULT] Realtime Stream AI**
```
Camera / MP4 / RTSP
  → Frigate/go2rtc (single RTSP restream per active cam)
  → perception_worker (OpenCV/FFmpeg reads rtsp://frigate:8554/<cam>, PERCEPTION_FPS=8)
      ├─ person cams → crowd_gpu :8000/detect → ByteTrack/crowd/loiter logic
      └─ fire cams   → fire_gpu  :8000/detect → fire/smoke debounce
  → MQTT "perception/#"
  → dashboard backend MQTT bridge
  → WebSocket /ws → SPA live tile overlays/toasts/timeline
```

**[LEGACY] Frigate-Triggered / Event-Driven**
```
Frigate "frigate/events" → ai_worker → ai_worker/alerts/# → dashboard
```
- `ai_worker` hiện inactive trong compose (`ACTIVE_PROBLEM=`). Chỉ giữ fallback crowd/loiter event-driven nếu bật tay.
- Fire/smoke KHÔNG còn chạy trong `ai_worker`; không poll `/api/<cam>/latest.jpg`.
- `stream_core` chỉ là metadata/benchmark helper; bbox live chính lấy từ `perception_worker`.


---

## 2. `ai_worker` — logic chi tiết (`ai_worker/worker.py`)

> `ai_worker` hiện là legacy fallback. Compose mặc định `ACTIVE_PROBLEM=` nên không xử lý live AI. Fire/smoke realtime nằm ở `perception_worker` (§2.7), không còn `FireSmokePump` trong `ai_worker`.

### 2.1 Trình tự xử lý 1 event (`handle_crowd` / `handle_loitering`)

**Với Crowd:**
1. Nhận msg `frigate/events` → parse JSON, lấy `after`.
2. Qua các **cổng lọc** (§2.2) → nếu rớt thì `return` (chưa tốn inference).
3. `fetch_snapshot(event_id)` → GET `{FRIGATE_API}/api/events/{id}/snapshot.jpg` (timeout `FRIGATE_SNAPSHOT_TIMEOUT`).
4. `downscale()` → ép ảnh `< MAX_UPLOAD_BYTES` (§2.3).
5. Đọc `(width,height)` ảnh sau nén → `inference_resolution`.
6. `query_modal()` → **POST raw JPEG** (`Content-Type: application/octet-stream`) tới `MODAL_ENDPOINT_URL` (timeout `MODAL_REQUEST_TIMEOUT`). Nhận `{person_count, detections, model}`.
7. `get_max_crowd_cluster(detections)` → `(max_cluster_size, member_indices)` (§2.4).
8. Tính `cluster_bbox` = bao tất cả box thành viên: `[min x1, min y1, max x2, max y2]`.
9. **Nếu `max_cluster_size >= CROWD_THRESHOLD`** → publish alert lên `ALERT_TOPIC` (§4.1) **và** `set_sub_label(event_id, "CROWD: <n>")` (POST ngược về Frigate để hiện nhãn phụ trong UI).

**Với Loitering (`"loitering" in ACTIVE_PROBLEMS`):** — `handle_loitering(client, after, event_type)`
- **Phạm vi camera:** nếu `LOITERING_CAMERAS` không rỗng và `camera` không thuộc nó → bỏ (mặc định chỉ `cam_loiter`, tránh bắn nhầm `cam1_VIRAT_1`).
- **Dwell = tuổi đời track Frigate:** `dwell_seconds = frame_time - start_time` (lấy `start_time` từ `after`; nếu thiếu → bỏ). Đây là hiện diện LIÊN TỤC do Frigate quản lý, đúng kể cả khi update thưa, và reset tự nhiên khi track kết thúc (`end`) rồi xuất hiện lại với id mới.
- Nếu `dwell_seconds >= LOITERING_DWELL_SECONDS` (vd 40s) → thêm `id` vào `_loiter_active`, publish `active=true` lên `LOITERING_ALERT_TOPIC`. `is_new = (now - last_alert) >= COOLDOWN_SECONDS` (debounce theo object id); chỉ khi `is_new` mới cập nhật mốc alert và gọi `set_sub_label`.
- **Lifecycle bám object Frigate:** vắng mặt phát hiện qua event `end` (KHÔNG qua nhịp MQTT). Khi `end`: pop `_last_loiter_alert`; nếu object đang trong `_loiter_active` → publish **CLEAR** (`active=false`, `bbox` null) để dashboard xoá overlay/toast ngay.
- **Dọn rác:** khi `len(_last_loiter_alert) > 1000` → bỏ entry quá `LOITER_STATE_TTL_SECONDS` (mặc định 600s) và `_loiter_active.discard(id)` tương ứng.
- ⚠️ **Phát hiện thực nghiệm:** nhịp MQTT `update` của Frigate quá thưa để suy ra vắng mặt — quan sát gap inter-update tới ~18s trong khi object vẫn được track liên tục 65s. Vì vậy KHÔNG dùng gap MQTT để reset dwell (cách cũ làm loitering 0 alert); dùng tuổi đời track của Frigate, vắng mặt do event `end`.

10. Mọi lỗi trong handler bị **bắt & bỏ qua** (log `[event] handler error`), vòng MQTT không chết.

### 2.2 Cổng lọc (thứ tự, rớt là dừng)
**Dispatch (`on_message`) — chạy crowd + loitering SONG SONG:** `ACTIVE_PROBLEM` nay nhận **danh sách phân tách bằng dấu phẩy** (vd `crowd,loitering`) → `ACTIVE_PROBLEMS` (tập). Mỗi bài toán có nhánh riêng + phạm vi camera riêng:
- **Crowd** chạy khi `"crowd" in ACTIVE_PROBLEMS` **và** `type=="new"` **và** (`CROWD_CAMERAS` rỗng hoặc `camera` thuộc nó). Hiện `CROWD_CAMERAS = cam1_VIRAT_1, cam_loiter` ⇒ crowd chạy trên **cả 2 cam**, song song với loitering (`ACTIVE_PROBLEM=crowd,loitering`).
- **Loitering** chạy khi `"loitering" in ACTIVE_PROBLEMS` **và** `type in (new,update,end)` **và** (`LOITERING_CAMERAS` rỗng hoặc `camera` thuộc nó).
- Guard chung trước cả hai: `after.label == "person"`.

**Cổng lọc CROWD (trong `handle_crowd`, thứ tự, rớt là dừng):**
| # | Điều kiện | Bỏ qua nếu |
|---|-----------|------------|
| 1 | có `id` và `camera` | thiếu → bỏ |
| 2 | zone: nếu `TARGET_ZONES` không rỗng → `after.current_zones` phải giao với nó | không giao → bỏ |
| 3 | cooldown per-camera (§2.5) | còn trong cooldown → bỏ |

> ⇒ Chỉ event **person + type=new** trên `CROWD_CAMERAS` mới kích inference. Lọc zone/cooldown chạy **trước** snapshot → không tốn GPU cho event bị loại. Loitering là logic thuần (không gọi GPU) nên không qua bảng này.

### 2.3 Vòng nén ảnh (`downscale`)
- Nếu ảnh đã `<= MAX_UPLOAD_BYTES` → trả nguyên, bỏ qua.
- Ngược lại lặp tối đa `DOWNSCALE_MAX_ITERATIONS` lần:
  1. Lưu JPEG ở `quality` hiện tại; nếu `<= MAX_UPLOAD_BYTES` → trả.
  2. **Hạ chất lượng trước:** `quality -= DOWNSCALE_QUALITY_STEP` cho tới sàn `DOWNSCALE_MIN_QUALITY`.
  3. **Hết hạ chất lượng → mới resize:** khi `quality <= MIN`, nếu `max(w,h) <= DOWNSCALE_MIN_DIMENSION` thì bỏ cuộc (trả ảnh hiện tại), ngược lại **chia đôi** w,h rồi lặp lại.
- Tóm tắt: **giảm quality (80→40, bước 15) → rồi mới giảm kích thước (÷2)**, dừng khi đạt size hoặc chạm đáy 320px.

### 2.4 ⭐ CÔNG THỨC PHÂN CỤM ĐÁM ĐÔNG (`get_max_crowd_cluster`)
Input: `detections[i].bbox = [x1, y1, x2, y2]` (**toạ độ góc, pixel tuyệt đối** theo độ phân giải inference).

**Bước 1 — điểm chân & chiều cao (mỗi người):**
```
foot_i   = ( (x1 + x2) / 2 , y2 )      # tâm ngang, đáy box = chân người trên ảnh 2D
height_i = max( y2 - y1 , 1.0 )        # cao box, sàn 1.0 chống chia 0
```

**Bước 2 — hai người i,j có "kề nhau" không (`is_neighbor`):**
```
# (a) Cổng phối cảnh / độ sâu: loại 2 người chênh lệch cao nhiều (khác lớp xa-gần)
if  min(h_i,h_j) / max(h_i,h_j)  <  SIZE_RATIO_MIN :   return False

# (b) Khoảng cách Euclid giữa 2 điểm chân (pixel)
dist = sqrt( (foot_i.x - foot_j.x)^2 + (foot_i.y - foot_j.y)^2 )

# (c) Ngưỡng thích ứng theo chiều cao trung bình (người cao/gần ⇒ bán kính lớn hơn)
threshold = ( (h_i + h_j) / 2 ) * DISTANCE_FACTOR

return  dist <= threshold
```
- **Ý nghĩa cổng (a):** 2 người gần nhau trên ảnh nhưng cao lệch nhiều ⇒ thực ra ở **độ sâu khác** (1 xa, 1 gần) ⇒ không tính cùng cụm. `SIZE_RATIO_MIN=0.8` ⇒ cao phải ≥80% của người cao hơn.
- **Ý nghĩa (c):** dùng chiều cao làm thước đo "thực tế" để bù phối cảnh — người ở gần (box to) được phép cách xa hơn (theo pixel) mà vẫn chung cụm. `DISTANCE_FACTOR=1.2`.

**Bước 3 — gom cụm:** dựng **đồ thị vô hướng** (cạnh = `is_neighbor`), tìm **thành phần liên thông bằng BFS**. Mỗi cụm = 1 thành phần liên thông (gom bắc cầu: A–B, B–C ⇒ A,B,C cùng cụm).

**Bước 4 — chọn cụm:** trả về **cụm lớn nhất** `(size, indices)`. Cảnh báo khi `size >= CROWD_THRESHOLD` (mặc định 3).

> Tham số (`CROWD_THRESHOLD / DISTANCE_FACTOR / SIZE_RATIO_MIN`) sống trong `config.py` — xem ý nghĩa & default ở đó, **không lặp lại số ở đây**.
> Dashboard `/benchmark/run` **tái dùng cùng thuật toán này** (bản sao trong `dashboard/backend/app.py`) để vẽ overlay cụm cho từng model. Bản sao này đọc tham số riêng từ env dashboard (`CLUSTER_SIZE_RATIO_MIN` / `CLUSTER_DISTANCE_FACTOR` / `CLUSTER_CROWD_THRESHOLD`, mặc định khớp live `0.8/1.2/3`) — **phải đồng bộ tay** với `SIZE_RATIO_MIN`/`DISTANCE_FACTOR`/`CROWD_THRESHOLD` của live, nếu lệch overlay benchmark sẽ không khớp live.

### 2.5 Cooldown
- Dict module-level `_last_processed_time[camera] = epoch_utc`.
- Bỏ event nếu `now - last < COOLDOWN_SECONDS`; ngược lại cập nhật `last = now` rồi xử lý.
- **Per-camera** (mỗi cam 1 mốc), kiểm tra trước snapshot.

### 2.6 Hàm phụ
- `set_sub_label(id, text)` → POST `{FRIGATE_API}/api/events/{id}/sub_label` `{"subLabel": text}` (lỗi → nuốt). Đây là **đường duy nhất ai_worker ghi ngược Frigate** (nhãn phụ, không phải điều khiển).
- `query_modal` cảnh báo **một lần** nếu `MODAL_ENDPOINT_URL` rỗng.
- Không còn `fetch_latest()`/`query_fire()` cho fire trong `ai_worker`; realtime fire/smoke đi qua `perception_worker` RTSP.
- Startup in toàn bộ config chính ra log để kiểm tra nhanh.

### 2.7 ⭐ Fire/Smoke realtime (`perception_worker`, RTSP lane)
- `perception_worker` đọc `PERCEPTION_FIRE_CAMERAS` từ `PERCEPTION_RTSP_TEMPLATE=rtsp://frigate:8554/{}`.
- Mỗi frame được encode JPEG rồi POST raw bytes sang `FIRE_SMOKE_ENDPOINT_URL` (`fire_gpu:8000/detect`).
- Raw per-frame output publish `perception/fire_smoke/<cam>` với schema `fire_smoke.v1`, `source=realtime`, `stream_source`, `frame_id`, `width`, `height`, `detections`, `fire_count`, `smoke_count`.
- Debounced alert/clear publish `perception/alerts/fire_smoke`; debounce vẫn N-trên-M theo class (`FIRE_PERSIST_N/M`, `FIRE_CONFIDENCE`, `FIRE_CLEAR_SECONDS`).
- `ai_worker` fire pump cũ đã bị gỡ khỏi runtime; không dùng `/api/<cam>/latest.jpg` cho fire.

---

## 3. Hợp đồng `/detect` (mọi `model_workers/<m>`)

Chung cho mọi service: **FastAPI**, lắng nghe **:8000** (trong container, không publish ra host), 2 route:
- `GET /health` → `{status:"ok", cuda:bool, device:str, model:str}`.
- `POST /detect` → **body = raw JPEG bytes** (đọc bằng `await request.body()`, không multipart).

**Response (cố định, mọi model giống nhau):**
```json
{
  "person_count": 9,
  "detections": [ { "bbox": [x1, y1, x2, y2], "confidence": 0.87 } ],
  "model": "rfdetr-large"
}
```
- `bbox`: **[x1,y1,x2,y2] pixel tuyệt đối** (xyxy), theo độ phân giải ảnh gửi lên.
- Thêm model mới = tạo `model_workers/<m>/server.py` trả đúng schema này → đổi `MODAL_ENDPOINT_URL` (live) hoặc thêm vào `BENCHMARK_ENDPOINTS` (so sánh).
- ⚠️ **Ngoại lệ `fire_gpu`** (bài toán fire/smoke, KHÔNG đếm người): cùng `GET /health` + `POST /detect` (raw JPEG), nhưng response dùng schema RIÊNG: `{detections:[{bbox,confidence,class:"fire"|"smoke"}], fire_count, smoke_count, model}` (không có `person_count`). Vẫn `bbox` xyxy px tuyệt đối. Xem §3.3.

### 3.1 `crowd_gpu` = RF-DETR-Large (`model_workers/rf_detr_large/server.py`)
- Model `RFDETRLarge()`; **bắt buộc CUDA** (chặn CPU). `model` string = `"rfdetr-large"`.
- Lọc người: tìm `person_id` trong COCO_CLASSES lúc khởi động → giữ detection có `class_id == person_id`.
- Ngưỡng tin cậy: `model.predict(img, threshold=DETECTION_THRESHOLD)` — env `DETECTION_THRESHOLD` (mặc định 0.5).
- Detection object có `.xyxy / .confidence / .class_id` (thư viện `supervision`).

### 3.2 `locate_gpu` = LocateAnything-3B (`model_workers/locate_anything_3b/server.py`)
- VLM tự hồi quy `nvidia/LocateAnything-3B`, **FP16**, CUDA bắt buộc. `model` string = `"locate-anything-3b"`.
- Prompt cố định: *"Locate all the instances that matches the following description: person."*
- `generate(...)` **bắt buộc**: `generation_mode="hybrid"`, `do_sample=True`, `temperature=0.7`, `top_p=0.9`, `repetition_penalty=1.1`, `max_new_tokens=2048`. (greedy → lặp tới max → rác.)
- Parse output bằng regex `<box><x1><y1><x2><y2></box>`, toạ độ **chuẩn hoá /1000** → nhân lại theo `image.size`. **`confidence` luôn = 1.0** (VLM không cho điểm).
- **Không** đọc `DETECTION_THRESHOLD`. Pin **`transformers==4.57.1`** (mới hơn → vỡ custom code). HF cache `/opt/hf` (bind-mount host).

### 3.3 `fire_gpu` = YOLOv8 fire+smoke (`model_workers/fire_smoke/server.py`)
- Chạy qua package `ultralytics` (`YOLO(weights).predict(img, conf=DETECTION_THRESHOLD, imgsz=640, device="cuda")`). **CUDA bắt buộc.** `model` string = id repo (vd `yolov8n_wildfire_detection`).
- **Model**: tải PRETRAINED từ HF Hub, thử lần lượt danh sách `DEFAULT_REPOS` (env `FIRE_MODEL_REPOS`) → repo ĐẦU TIÊN tải được + có class fire/smoke. Hiện dùng **`JJUNHYEOK/yolov8n_wildfire_detection`** (classes `{0:smoke,1:fire}`) — đã verify tải+load không cần auth. HF cache `/opt/hf` (bind-mount `./model_cache/fire_hf`) để rebuild không tải lại.
  - ⚠️ Các repo mà brief đề xuất (`AlimTleuliyev/wildfire-detection` MIT, `TommyNgx/YOLOv10-...` Apache) đều **gated/404** lúc làm (2026-06-24) → đã thay bằng repo ungated tương đương qua `HfApi.list_models(search=...)`. Đổi model: set env `FIRE_MODEL_REPOS` + rebuild fire_gpu.
- Map class→`fire`/`smoke` bằng substring tên class (chứa "smoke"→smoke, "fire"/"flame"→fire, còn lại "other" bị bỏ). `DETECTION_THRESHOLD` (env, mặc định 0.25) = ngưỡng phía MODEL; lọc CHẶT hơn nằm ở `ai_worker` (`FIRE_CONFIDENCE`).
- GB10 sm_121: cùng recipe — base cuda 12.8 + torch cu128 + pin `nvidia-cuda-nvrtc-cu12==12.9.86` SAU torch.

---

## 4. Schema thông điệp

### 4.1 MQTT alerts

**Crowd (`ai_worker/alerts/crowd`):**
```json
{
  "camera": "cam1_VIRAT_1",
**Loitering (`ai_worker/alerts/loitering`):** — 2 dạng message theo lifecycle object.

Dạng 1 — ĐANG lảng vảng (`active:true`, khi dwell ≥ ngưỡng, mỗi event new/update):
```json
{
  "camera": "cam_loiter",
  "object_id": "<frigate event id>",
  "label": "person",
  "dwell_seconds": 55.4,             // hiện diện LIÊN TỤC = frame_time - start_time (tuổi đời track Frigate)
  "bbox": [1102, 417, 1137, 502],
  "score": 0.93,
  "active": true,
  "is_new": true,                    // true = lần cảnh báo mới (qua cooldown); false = cập nhật lặp lại
  "frame_time": 1700000055.4,
  "timestamp": "2026-06-23T..Z"
}
```

Dạng 2 — CLEAR (`active:false`, khi Frigate object `end`, object đang hiển thị):
```json
{
  "camera": "cam_loiter",
  "object_id": "<frigate event id>",
  "active": false,                   // dashboard xoá overlay/toast ngay; bbox bị BỎ (null)
  "timestamp": "2026-06-23T..Z"
}
```
> Lifecycle bám object Frigate: overlay chủ yếu được clear ngay bằng `active:false` lúc `end`; TTL frontend chỉ là dự phòng. Phạm vi camera: chỉ chạy trên `LOITERING_CAMERAS` (mặc định `cam_loiter`).

**Fire/Smoke (`perception/fire_smoke/<cam>` + `perception/alerts/fire_smoke`):** — lane realtime RTSP trong `perception_worker`. Không poll `latest.jpg`. Raw frame messages phát liên tục; alert/clear đã debounce N-trên-M theo từng class.

Dạng 1 — ĐANG có lửa/khói (`active:true`, khi >= `FIRE_PERSIST_N` trong `FIRE_PERSIST_M` khung pump gần nhất có class đó vượt `FIRE_CONFIDENCE`):
```json
{
  "camera": "cam_fire",
  "detections": [ { "bbox": [x1,y1,x2,y2], "confidence": 0.72, "class": "fire" } ],
  "fire_count": 1,
  "smoke_count": 0,
  "active": true,
  "model": "yolov8n_wildfire_detection",
  "inference_resolution": [w, h],    // kích thước ảnh ĐÃ NÉN gửi cho model = hệ toạ độ của bbox (xyxy px tuyệt đối)
  "timestamp": "2026-06-24T..Z"
}
```
> bbox là px TUYỆT ĐỐI ở `inference_resolution` (KHÔNG chuẩn-hoá). Overlay live-tile chia bbox cho `inference_resolution` để vẽ trên video (SVG `viewBox=0 0 w h` + `preserveAspectRatio=slice` khớp `object-cover`). Nếu khung hiện tại không có detection nhưng class vẫn persist → mang theo bbox khung gần nhất (`_last_dets`) để box không nhấp nháy.

Dạng 2 — CLEAR (`active:false`, sau `FIRE_CLEAR_SECONDS` không còn detection nào):
```json
{ "camera": "cam_fire", "active": false, "timestamp": "2026-06-24T..Z" }
```
> Vì sao là lane riêng: lửa & khói KHÔNG phải object COCO ⇒ Frigate không phát event. Lane mới lấy frame trực tiếp từ RTSP qua `perception_worker`, không qua `ACTIVE_PROBLEMS` của `ai_worker`.

**Stream Core (`stream_core/frames/<cam>`):**
```json
{
  "schema": "frames.v1",
  "camera": "cam_loiter",
  "frame_id": 150,
  "pts": 270000,
  "pts_time": 3.0,
  "monotonic_ts": 12345.67,
  "wall_ts": "2026-06-29T...Z",
  "width": 1280,
  "height": 720,
  "source": "rtsp://...",
  "generation": 1
}
```

**Perception Worker - Objects (`perception/objects/<cam>`):**
```json
{
  "schema": "objects.v1",
  "camera": "cam_loiter",
  "frame_id": 150,
  "pts": null,
  "monotonic_ts": 12345.89,
  "wall_ts": "2026-06-29T...Z",
  "width": 1280,
  "height": 720,
  "source": "rtsp://...",
  "model": "rfdetr-large",
  "objects": [
    {
      "id": "cam_loiter:150:0",
      "track_id": "cam_loiter:1",
      "class": "person",
      "bbox": [100, 200, 300, 400],
      "confidence": 0.85,
      "velocity": null
    }
  ]
}
```

**Perception Worker - Tracks (`perception/tracks/<cam>`):**
```json
{
  "schema": "tracks.v1",
  "camera": "cam_loiter",
  "wall_ts": "2026-06-29T...Z",
  "tracks": [
    {
      "track_id": "cam_loiter:1",
      "class": "person",
      "bbox": [100, 200, 300, 400],
      "first_seen": "2026-06-29T...Z",
      "last_seen": "2026-06-29T...Z",
      "age_seconds": 15.2,
      "hits": 30,
      "misses": 0,
      "state": "active"
    }
  ]
}
```

### 4.2 WebSocket `/ws` (dashboard → trình duyệt)
Mỗi frame là JSON `{type, data}`; backend **bọc** message MQTT rồi fan-out cho mọi client (read-only, không publish ngược).
```jsonc
// type 1: từ ai_worker/alerts/crowd
{ "type": "crowd_alert",  "data": { ...payload §4.1 (crowd)... } }

// type 2: từ ai_worker/alerts/loitering
{ "type": "loitering_alert", "data": { ...payload §4.1 (loitering)... } }

// type 3: từ perception/fire_smoke/<cam>
{ "type": "realtime_fire_smoke", "data": { ...payload §4.1 (fire/smoke raw frame)... } }

// type 4: từ perception/alerts/fire_smoke
{ "type": "realtime_alert", "data": { ...payload §4.1 (fire/smoke alert/clear)... } }

// type 4: từ frigate/events (chuyển thẳng)
{ "type": "frigate_event", "data": { "type":"new|update|end",
    "after": { "id", "camera", "label", "current_zones": [...] } } }
```
> `mqtt_bridge` map topic → type: `perception/fire_smoke/*`→`realtime_fire_smoke`, `perception/alerts/*`→`realtime_alert`, legacy `ai_worker/alerts/fire_smoke` chỉ là fallback/suppressed nếu realtime fire vừa seen.

---

## 5. Dashboard (`dashboard/`)

### 5.1 Routes backend (FastAPI, `dashboard/backend/app.py`, chạy `uvicorn :8080`)
| Route | Method | Vai trò |
|-------|--------|---------|
| `/dashboard/config` | GET | `{stale_seconds, alert_topic, cameras:[{name,enabled,width,height}]}` (chỉ cam `enabled:true`) |
| `/benchmark/run` | POST | so sánh đa model (§5.3) |
| `/inspect/{cam}/frame.jpg` | GET | **composite (D5):** backend fetch frame SẠCH `latest.jpg` (KHÔNG `bbox`) rồi vẽ CHỈ 1 box loiterer màu cố định bằng PIL. Query: `box=nx1,ny1,nx2,ny2` (chuẩn-hoá 0..1), `h` (chiều cao, backend map → Frigate param `height`). KHÔNG box → trả frame sạch. KHÔNG đi qua reverse-proxy `/api/*`. |
| `/ws` | WS | đẩy `crowd_alert` + `frigate_event` (§4.2) |
| `/api/{path}` | GET/HEAD | **proxy Frigate, chỉ đọc** (§5.2) |
| `/api/webrtc` | POST | **ngoại lệ** — signaling go2rtc → `http://frigate:1984` |
| `/live/{path}` | WS | proxy WebSocket video (webrtc/mse/jsmpeg) sang Frigate |
| `/api/{path}` | POST/PUT/PATCH/DELETE/OPTIONS | **chặn → 405** |
| `/` | GET | phục vụ SPA build (Vite `dist/` → mount `static/`), fallback `index.html` |

- MQTT: subscribe `ai_worker/alerts/#` + `EVENTS_TOPIC`, đẩy qua `/ws`. **Không publish.**
- `fetch_cameras()`: GET `/api/config` của Frigate → **lọc bỏ `enabled:false`**.
- ⚠️ **Lưu ý:** Dashboard hoàn toàn stateless, không lưu trữ database SQLite (đã gỡ bỏ thư mục `data/` và `aiosqlite`).

### 5.2 Reverse-proxy Frigate (`frigate_proxy.py`)
- **Read-only:** chỉ `GET/HEAD` qua `/api/*`; mọi method ghi → **405**. Ngoại lệ duy nhất: `POST /api/webrtc`.
- Lọc hop-by-hop headers, stream body, timeout `FRIGATE_PROXY_TIMEOUT` (30s).
- `/live/{path}`: nâng cấp WS, đổi http→ws, relay 2 chiều client↔Frigate, tự dọn khi đứt.

### 5.3 `/benchmark/run`
- Body: `{ "camera": "<name>" }` (mặc định cam enabled đầu tiên).
- Lấy **1 frame**: GET `{FRIGATE_API}/api/{camera}/latest.jpg` (timeout 5s).
- Đọc env `BENCHMARK_ENDPOINTS` (mảng JSON `[{name,url}]`) → POST raw JPEG **song song** (`asyncio.gather`) tới mọi url, timeout **90s/req** (chịu VLM chậm).
- Mỗi model: đo `latency_ms`, parse response, chạy phân cụm (§2.4) với tham số **`CLUSTER_*`** (env dashboard) — **phải sync tay** với tham số live (§2.4).
- ⚠️ Nếu service đích đang **stopped** (vd `locate_gpu` mặc định Exited 0) → lỗi `[Errno -3] Temporary failure in name resolution`; bật `docker compose up -d locate_gpu`, chờ load ~30–60s (xem `DEBUG_GUIDE.md §6f`).
- **Response:**
```json
{
  "frame_b64": "<JPEG base64>", "frame_width": 1920, "frame_height": 1080,
  "results": [
    { "model":"rfdetr-large", "latency_ms":58, "person_count":9,
      "max_cluster_size":5, "cluster_bbox":[..]|null,
      "detections":[{bbox,confidence}], "error":null }
  ]
}
```

### 5.4 Frontend (React 19 + TS + Vite + Tailwind, `dashboard/frontend/src/`)
- **Tabs:** `live` (mặc định) | `benchmark`. State: alert theo cam, timeline event, toast.
- **Player live phân tầng** (`lib/liveStream.ts`): **webrtc** (chạy thật, `RTCPeerConnection` → POST `/api/webrtc?src=`) → **mse** (stub, `onError` ngay) → **jsmpeg** (stub) → **snapshot** (poll `/api/{cam}/latest.jpg?h=360` mỗi ~800ms). ⇒ thực tế: **webrtc → snapshot**.
- Components: `CameraGrid`/`CameraTile` (tile + badge số người + viền đỏ khi vượt ngưỡng, mờ khi quá `stale_seconds`; **viền cam khi loitering active**) — **D7 (2026-06-24): bbox loitering ĐƯỢC ĐƯA LẠI lên tile live** (đảo quyết định D3/D5: box hơi trễ trên video WebRTC mượt > pop-up giật). **`CameraInspectPanel` đã XOÁ** (không còn pop-up loitering; helper `inspectFrameUrl()` ở `lib/api.ts` cũng xoá). Backend route `/inspect/<cam>/frame.jpg` GIỮ NGUYÊN trong `app.py` nhưng **frontend không còn gọi** (dead route, có thể bỏ sau). 3 overlay live-tile nay DÙNG CHUNG 1 pattern SVG (`viewBox` + `preserveAspectRatio=xMidYMid slice` khớp `object-cover`): `BenchmarkPanel` (lưới so sánh + overlay SVG: box xanh = detection, đỏ nét đứt = cụm), `EventsTimeline` (**CHỈ kết quả bài toán** — D8 2026-06-24: crowd alert khi `person_count >= threshold` + loitering + fire/smoke. Hằng số `SHOW_RAW_FRIGATE_EVENTS=false` (`App.tsx`) ⇒ **KHÔNG** fetch/trộn lịch sử `/api/events` và **KHÔNG** ingest row `frigate_event` per-person/per-frame nữa; timeline chỉ còn live problem-alert, tối đa 80. Đổi `true` để bật lại lịch sử thô), `Header` (trạng thái WS, đếm cam), `AlertToast` (nổi khi `person_count >= threshold`, tự tắt 8s, tối đa 4).
- `useLiveChannel`: WS `/ws` + reconnect backoff 1→15s. Nhận thêm type `fire_smoke_alert` → `onFireSmokeAlert`.
- **3 overlay SVG trên tile live `CameraTile.tsx` (cùng pattern, vẽ giữa `CameraSurface` và lớp gradient):**
  - **Fire/Smoke (`FireOverlay`):** state `fires` theo cam ở `App.tsx` (TTL `FIRE_TTL_MS=10s`, clear ngay bằng `active=false`). Box **đỏ `#ef4444`** = fire, **lam `#60a5fa`** = smoke; `viewBox=0 0 w h` theo `inference_resolution`. Badge dưới tile đổi sang `FireBadge`, viền đỏ nhấp nháy. + toast/timeline `kind:"fire"`.
  - **Loitering (`LoiterOverlay`, D7):** state `loiters` theo cam ở `App.tsx` (clear ngay bằng `active=false`; TTL dự phòng `LOITER_TTL_MS=10s`). Box **amber `#f59e0b`** (= viền `warn`) + name-tag `LOITERING {dwell}s` với **giây đếm client-side tick mỗi 1s** (dwell = `dwellSeconds` + thời gian trôi từ `receivedAt`). bbox loitering là **px tuyệt đối ở DETECT-RES của camera** (= `camera.width/height` từ `/dashboard/config`, vd cam_loiter 1280×720) ⇒ `viewBox=0 0 detectWidth detectHeight`. `detectWidth/Height` được nhét vào `CameraLoiterState` lúc nhận alert (tra cứu qua `cameraDimsRef` ← config). Đánh đổi: bbox ở `frame_time` quá khứ ⇒ người DI CHUYỂN box trễ ~nhịp update; loiterer demo gần đứng yên nên không lộ.
  - **Crowd (`CrowdOverlay`, D7) — cam1_VIRAT_1 + cam_loiter:** state `crowdOverlays` theo cam ở `App.tsx` (TTL `CROWD_OVERLAY_TTL_MS=7s` vì crowd event-driven + cooldown 5s — không có message clear nên CHỈ dọn bằng TTL). Mỗi `crowd_alert`: vẽ `cluster_bbox` **đỏ `#ef4444` nét ĐỨT** + các box thành viên cụm (`detections[i]` với `i ∈ cluster_member_indices`) **xanh `#22c55e` nét liền** — cùng style overlay `BenchmarkPanel`. bbox là px tuyệt đối ở `inference_resolution` ⇒ `viewBox=0 0 w h` theo `inference_resolution`. Badge số người + viền đỏ khi vượt ngưỡng giữ nguyên. Chấp nhận box trễ người di chuyển (đánh đổi đã chọn). **D8 (2026-06-24) — kiểm chứng mapping trên `cam_loiter`:** nguồn crowd = event snapshot `/api/events/<id>/snapshot.jpg` đo được **1280×720** = `latest.jpg` 1280×720 = video gốc 1280×720 = detect-res, **tất cả 16:9** (không crop/letterbox/stretch) ⇒ `inference_resolution` 16:9 + `viewBox` slice khớp HỆT `<video> object-cover` trong tile 16:9 ⇒ **KHÔNG có bug toạ độ/tỉ lệ**. Lệch box quan sát thấy là **trễ thời gian cố hữu** (box từ snapshot quá khứ, đứng im trên video real-time tới khi TTL 7s xoá; crowd event-driven + cooldown 5s nên không update giữa chừng) — KHÔNG sửa (đúng giới hạn "box nhịp thấp trên video real-time").

---

## 6. Frigate `config.yml` — chi tiết

- **Detector:** `openvino` trên **CPU**; model `/openvino-model/ssdlite_mobilenet_v2.xml`, **300×300**, `nhwc`/`bgr`, labelmap `coco_91cl_bkgr.txt`.
- **Objects track:** `person, bicycle, car, motorcycle, bus, truck` (nhưng ai_worker chỉ xử lý `person`).
- **Camera enabled:** `cam1_VIRAT_1` (đám đông, 1080p, `detect.fps=5`), `cam_loiter` (loitering demo, loop Better_loitering.mp4, 720p). **`cam_loiter` nâng `detect.fps = 8`** (D7 2026-06-24, từ 5; chọn 8 thay vì 10 — 8 đã được đo an toàn trước đây ~909% < trần 1400%, còn 10 không kịp đo `docker stats` trong sandbox này) + `detect.stationary.interval = 5` (re-detect người đứng yên mỗi ~1s thay vì ~2s) để **làm tươi box loitering**. `detect.fps` chỉ đổi tần suất AI "thấy" frame — **độc lập** framerate live (go2rtc full-fps); hạ fps làm bbox của mục tiêu **di chuyển** trễ hơn giữa các update (xem `DEBUG_GUIDE §7b`). ⚠️ **Caveat:** fps cao làm tươi box cho mục tiêu **ĐỨNG YÊN** (loiterer demo), NHƯNG Frigate vẫn giới hạn nhịp message `update` cho mục tiêu **DI CHUYỂN** (~2.4s, không có knob) ⇒ box người đi vẫn bước ~2.4s trên video real-time. Đây là cố hữu, không phải bug. **D1 (2026-06-24, `cam_loiter`):** `motion.contour_area`=5 + `motion.threshold`=25 (bắt người nhỏ/xa) — đặt per-camera trong `config.yml`, `docker compose restart frigate` để áp.
- **`cam_fire` (fire/smoke demo):** đọc `cam_fire.mp4` qua NGUỒN ĐƠN exec-go2rtc như cam1/cam_loiter. `cameras.cam_fire.ffmpeg.inputs` đọc `rtsp://127.0.0.1:8554/cam_fire`; `perception_worker` đọc `rtsp://frigate:8554/cam_fire` cho fire/smoke realtime. `detect.fps=5` chỉ phục vụ Frigate record/snapshot; fire/smoke bbox không phụ thuộc `latest.jpg`.
- **go2rtc:** WebRTC candidate `192.168.3.252:8555`. **`cam_loiter` (D5) + `cam1_VIRAT_1` & `cam_fire` (D8 2026-06-24) = NGUỒN ĐƠN:** mỗi stream là `exec:/usr/lib/ffmpeg/rpi/bin/ffmpeg -hide_banner -loglevel warning -re -stream_loop -1 -fflags +genpts -i ...<video>.mp4 -c copy -rtsp_transport tcp -f rtsp {{output}}` (escape `{{output}}` vì Frigate chạy `str.format`); cần env `GO2RTC_ALLOW_ARBITRARY_EXEC=true` ở service frigate (nguồn `exec/echo/expr` bị chặn mặc định). `cameras.<cam>.ffmpeg.inputs` đọc `rtsp://127.0.0.1:8554/<cam>` (`input_args: preset-rtsp-restream`, roles detect+record) ⇒ detect + live WebRTC dùng CHUNG 1 producer loop always-on → **đồng pha; refresh trang join giữa vòng lặp thay vì restart về 0** (xem §1 / `DEBUG_GUIDE §6g`). Mỗi nguồn-đơn = 1 ffmpeg `-c copy` always-on (không transcode → rẻ, ~5% CPU/cam; verify: đúng 1 reader file/cam, `1984/api/streams` mỗi cam 1 producer `-stream_loop -1`). Đo D8 sau khi bật cả 3: frigate CPU ~885%/1400% (còn headroom), KHÔNG drop frame.
- **Record (0.17 tiered):** `continuous` + `alerts.retain` + `detections.retain`, tất cả ≈ **0.0007 ngày (~60s)** mode `all` — **giữ rất ngắn để thư mục storage liên tục tự dọn dẹp, không phình to**. Snapshots bật (timestamp + bbox), retain ~60s.
- **Zones:** hiện **chưa định nghĩa zone** trên cam1 ⇒ để `TARGET_ZONES` rỗng (xử lý mọi vị trí). Muốn lọc zone phải thêm `zones:` vào cam rồi set `TARGET_ZONES`.

---

## 7. Bản đồ cổng & wiring

| Service | Host→Container | Giao thức | Vai trò |
|---------|---------------|-----------|---------|
| mqtt | 1883→1883 | TCP | broker (persistence disabled) |
| frigate | 5000→5000 | TCP | UI + HTTP API |
| frigate | 8556→8554 | TCP | RTSP |
| frigate | 8555→8555 | TCP/UDP | WebRTC |
| frigate | *(nội bộ)* 1984 | TCP | go2rtc API (đích của `/api/webrtc`) |
| dashboard | 8080→8080 | TCP | UI control-room |
| stream_core | 8092→8092 | TCP | metrics & health |
| perception_worker | 8093→8093 | TCP | metrics & health |
| crowd_gpu | *(không publish)* | TCP | `:8000/detect` (gọi nội bộ) |
| locate_gpu | *(không publish)* | TCP | `:8000/detect` (chỉ benchmark) |
| fire_gpu | *(không publish)* | TCP | `:8000/detect` (fire/smoke, gọi nội bộ; mem_limit 6g) |
| ai_worker | *(không publish)* | — | client MQTT + pump HTTP fire/smoke |

**Điểm nối (compose env):**
- `ai_worker.MODAL_ENDPOINT_URL = http://crowd_gpu:8000/detect` (legacy config name, luôn trỏ về local).
- `perception_worker`: `PERCEPTION_RTSP_TEMPLATE=rtsp://frigate:8554/{}`, `PERCEPTION_CAMERAS=cam1_VIRAT_1,cam_loiter,cam_fire`, `PERCEPTION_PERSON_CAMERAS=cam1_VIRAT_1,cam_loiter`, `PERCEPTION_FIRE_CAMERAS=cam_fire`, `PERCEPTION_FPS=8`, `PERCEPTION_STALE_SECONDS=30`. `ai_worker.ACTIVE_PROBLEM=` mặc định inactive. `locate_gpu` chỉ bật khi benchmark.
- `dashboard.BENCHMARK_ENDPOINTS = [{rfdetr-large→crowd_gpu}, {locate-anything-3b→locate_gpu}]`.
- `depends_on`: `ai_worker → mqtt,frigate,crowd_gpu`; `dashboard → mqtt,frigate`.
- GPU 1 chiếc chia 4 container (`frigate`, `crowd_gpu`, `locate_gpu`, `fire_gpu`) cùng `device_requests gpu count:1`; `locate_gpu` mặc định chỉ bật khi benchmark; mem_limit xem `docker-compose.yml` (không lặp ở đây).
- **`frigate.cpus = 14`** (history 6→12→14) — detect CPU-bound; trần CPU = **1400%**. Đo lại 2026-06-24 (fps=5/cpus=14): **~700–800%** (đỉnh ~800%, không bỏ frame, còn ~600% headroom). Chỉ phần detect giảm theo fps (decode full-fps độc lập) nên CPU giảm ít hơn tỉ lệ so với ~909% cũ ở fps=8. Nút thắt scale là CPU detect, không phải GPU/RAM (xem `DEBUG_GUIDE.md §8`).

---

## 8. Kế hoạch tiếp theo (gọn — chi tiết ở `ROADMAP.md` và `DEVELOPMENT_PLAN.md`)
- ☐ **Phase 0 hardening:** shared clustering module, `clusters[]`, temporal persistence, health/metrics, circuit breaker, alert history.
- ☐ **`stream_core` spike:** đọc RTSP trực tiếp từ go2rtc/MediaMTX, gắn `frame_id`/`pts`, đo FPS/latency/resource, chưa cắt Frigate.
- ☐ **Detector/tracker backbone:** xuất `objects[]`/`tracks[]`, A/B ByteTrack vs BoT-SORT, giữ RF-DETR/fire YOLO legacy làm baseline.
- ◻ mse/jsmpeg live tier vẫn là stub — chỉ làm khi WebRTC không đủ hoặc khi MediaMTX playback/live thay Frigate.

## 9. Hướng phát triển (gọn — chi tiết ở `ROADMAP.md §2` và `DEVELOPMENT_PLAN.md`)
- **Mẫu legacy:** mỗi bài toán Frigate-triggered = 1 `model_workers/<m>/` + nhánh `ACTIVE_PROBLEM` trong `ai_worker`; chỉ dùng khi task không cần realtime.
- **Mẫu mới:** `perception_worker` đọc RTSP liên tục → detector/tracker chung → task engines đọc `objects[]`/`tracks[]`/raw detections → MQTT `perception/#` → dashboard. Không thêm task Frigate-triggered mới nếu task cần realtime/state liên tục.
- **4 bài toán còn lại:** Re-ID đa camera · Nhận diện khuôn mặt (SCRFD→ArcFace→FAISS) · LPR/giao thông (YOLO+SAHI→OCR) · Trật tự đô thị/khói-lửa (seg + point-in-polygon).
- **Crowd nâng cao:** pose → ST-GCN (bạo lực/ngã), PAR, VLM theo sự kiện; homography/BEV để đo mật độ thực.
- **Hạ tầng:** detect trên substream 360/720p + record mainstream; NVDEC HWAccel; MQTT store-and-forward; GPU batching đa luồng.
