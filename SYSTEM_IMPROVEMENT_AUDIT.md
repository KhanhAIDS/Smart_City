# SYSTEM_IMPROVEMENT_AUDIT.md — Audit cải thiện hệ thống Smart_City (benchmark-first)

> Ngày audit: **2026-07-02**. Phạm vi: benchmark harness AI camera, KHÔNG phải production checklist.
> Bằng chứng lấy từ file/code + snapshot runtime lúc audit (docker stats / nvidia-smi / docker logs).
> Trạng thái đặc biệt: working tree đang có **lane traffic violation dở dang, chưa commit**
> (`PLAN.md`, `model_workers/traffic_violation_gpu/`, sửa `docker-compose.yml`/`config.yml`/`worker.py`/frontend) — audit tính cả phần này.

---

## 1. Baseline đã xác nhận

### 1.1 Kiến trúc & lane (khớp docs ↔ code)
| Hạng mục | Trạng thái | Bằng chứng |
|---|---|---|
| Realtime lane: RTSP → `perception_worker` → `model_workers` → MQTT → dashboard | ✅ chạy | `perception_worker/worker.py:872-959`, `docker-compose.yml:217-291` |
| 4 bài toán cũ (crowd, loiter, fire, LPR) | ✅ chạy, đã verify trước đó | `SYSTEM.md §2`, worker.py:540-697 |
| Lane thứ 5+6 (stopped_vehicle, no_helmet) trên `cam_traffic_violation` | ⚠️ **đang chạy nhưng dở dang, chưa commit, có bug** | worker.py:700-866, git status, mục 2 |
| Clustering 1 nguồn duy nhất | ✅ | `smart_city_common/clustering.py`, import ở worker.py:18 + app.py:17 |
| `ai_worker` inactive, `locate_gpu`/`stream_core` profile-only | ⚠️ **cấu hình đúng nhưng runtime sai** — cả 2 đang Up (mục 3) | compose:65,202 vs `docker ps` |
| Dashboard stateless, benchmark one-shot 2 model crowd | ✅ đúng như docs | `dashboard/backend/app.py:230-304`, compose:194 |

### 1.2 Snapshot runtime lúc audit (2026-07-02 ~06:35 UTC)
| Container | CPU | RAM / limit | Ghi chú |
|---|---|---|---|
| frigate | 254% | **7.83Gi / 8Gi (97.8%)** | detection process **stuck + force-kill 3 lần hôm nay** (log 02:56, 05:59, 06:26), RestartCount=3 |
| perception_worker | 105% / 200% cap | 351Mi / 2Gi | 0 frame error trong 30 phút — lane RTSP khỏe |
| crowd_gpu | 93% | 1.96Gi / 12Gi | phục vụ 2 person-cam @8fps |
| fire_gpu / lpr_gpu / traffic_violation_gpu | 11% / 1.5% / 21% | ~1.2-1.3Gi mỗi cái | ổn |
| **locate_gpu** | 0.1% | 11.2Gi RAM + **~24.8GB GPU unified** (nvidia-smi pid 1728102) | **đang Up 5h, idle, ghim ~25GB dù chỉ dành cho benchmark** |
| stream_core | 14.5% | 347Mi | profile `realtime-benchmark` nhưng đang chạy thường trực |
| Host | — | 58Gi used / 63Gi available / 121Gi | GPU util 31% |

### 1.3 Fact đáng chú ý từ docs
- `SLIDE_NOTES.md §3` + `SLIDE_DECK.md §1D` đã tự nhận đúng các điểm yếu: double-decode, JPEG+HTTP per frame, không batch, Frigate detect CPU "gần như thừa", chưa đo MQTT throughput. Audit này **xác nhận các nhận định đó bằng số đo** và bổ sung phần chưa nói: benchmark validity (không có ground truth/history) và loạt bug của lane traffic mới.
- `ROADMAP.md` M2 (đo continuous vs triggered, ID-switch) và M3 (benchmark history) **vẫn ☐ toàn bộ** — đây chính là phần "làm chưa tới" lớn nhất so với mục tiêu benchmark.

---

## 2. Gap nghiêm trọng theo benchmark (P0)

### R1 — Alert stopped_vehicle / no_helmet bị hiển thị nhầm thành LOITERING (bug tích hợp lane mới)
- **Hạng mục:** Bug / benchmark validity lane traffic.
- **Vấn đề:** Alert của 2 bài toán mới không bao giờ hiện đúng loại trên dashboard; timeline/overlay bị sai hoặc câm.
- **Bằng chứng:** Alert stopped mang cả `object_id` + `dwell_time` + `zone_id` (`perception_worker/worker.py:749-756`); alert no_helmet mang `object_id` (worker.py:860-863). Frontend phân loại `realtime_alert` theo field, nhánh loitering `d.object_id !== undefined || d.dwell_time !== undefined` đứng **trước** nhánh `zone_id`/`no_helmet_bbox` (`dashboard/frontend/src/App.tsx:191` vs `:277,:288`) → mọi alert traffic rơi vào nhánh loitering; nhánh stopped/no_helmet **unreachable**. Message clear của stopped (`{active:false, object_id}` worker.py:764,771,779) còn xóa nhầm overlay loiter của camera.
- **Tác động benchmark:** Không thể đo event precision/recall, trigger delay, clear correctness cho stopped/no_helmet (toàn bộ khối metric trong `PLAN.md §Benchmark`); UI đánh giá trực quan sai loại sự kiện.
- **Ưu tiên:** P0. **Effort:** S (nửa ngày).
- **Rủi ro/tương thích:** Không — sửa thứ tự phân nhánh (check `zone_id`/`no_helmet_bbox` trước) hoặc tốt hơn: backend `map_mqtt_topic` phát type riêng theo topic (`perception/alerts/stopped_vehicle` → type riêng) thay vì field-sniffing; `infer_realtime_alert_type` (app.py:92-105) đã viết sẵn mà không dùng.
- **Đề xuất triển khai:** Thêm field `alert_kind` vào mọi payload alert ở perception_worker (nguồn phát tự khai loại) + frontend switch theo `alert_kind`; giữ field-sniffing làm fallback legacy.
- **Cách đo thành công:** Bơm 1 alert mỗi loại qua MQTT → timeline hiện đúng 6 kind; clear stopped không xóa loiter overlay.
- **File/component:** `App.tsx`, `dashboard/backend/app.py`, `perception_worker/worker.py`.

### R2 — Lane no_helmet chết im lặng: thiếu weights, health vẫn "ok"
- **Hạng mục:** Bug / model availability.
- **Vấn đề:** Helmet model không tồn tại → không có detection no_helmet nào; không surface lỗi.
- **Bằng chứng:** `model_cache/traffic_hf/` **rỗng** (root-owned, tạo 04:59); log `traffic_violation_gpu`: `WARNING: Helmet model not found at /opt/hf/helmet_yolo.pt`; `/health` vẫn trả `status:"ok"` với chỉ 1 flag `helmet_model_loaded:false` (`server.py:90-99`) trong khi `check_detector_health` của perception chỉ xem HTTP 200 (worker.py:286-289). `PLAN.md:231` yêu cầu "fail health clearly if missing".
- **Tác động benchmark:** no_helmet precision/recall = không đo được (không có model); hệ thống báo healthy giả.
- **Ưu tiên:** P0. **Effort:** M (tìm/tải checkpoint + sửa health) — xem R17 cho candidate model.
- **Rủi ro/tương thích:** Checkpoint helmet phải là `.pt` PyTorch (tránh ONNX-GPU); thư mục root-owned cần user `sudo chown` (AI không sudo).
- **Đề xuất triển khai:** (a) health trả `degraded` khi `HELMET_CAMERAS` được cấu hình mà model thiếu; (b) nạp checkpoint qua `TRAFFIC_HELMET_REPO/FILE`; (c) ghi license vào README (đang ghi mơ hồ "Ensure the weights...").
- **Cách đo thành công:** `/system/health` báo degraded khi thiếu weights; có bbox no_helmet trên video `No_helm_accidentally_stopped`.
- **File/component:** `model_workers/traffic_violation_gpu/server.py`, `README.md`, `model_cache/traffic_hf/`.

### R3 — Frigate detect CPU: sát trần RAM + detection stuck lặp lại → rủi ro sập cả lane RTSP
- **Hạng mục:** Ổn định hạ tầng / shared server.
- **Vấn đề:** Frigate là điểm nghẽn đơn (mọi lane đọc RTSP qua nó) nhưng đang chạy sát 97.8% mem_limit và detection process bị watchdog force-kill nhiều lần/ngày sau khi thêm cam thứ 5 (1080p@5fps).
- **Bằng chứng:** docker stats 7.83Gi/8Gi; log frigate 02:56/05:59/06:26 "Detection appears to be stuck... Force killing"; RestartCount=3; cam_traffic_violation thêm detect 1920×1080@5fps (`config.yml:127-137`) trong khi AI thật của cam này nằm ở perception lane — Frigate detect trên cam này **không phục vụ gì** cho benchmark.
- **Tác động benchmark:** Frigate restart ⇒ mọi RTSP restream đứt ⇒ toàn bộ lane AI mất frame ⇒ số đo missed events/latency nhiễm noise hạ tầng; rủi ro OOM trên máy chung (vi phạm ràng buộc §2 CLAUDE.md).
- **Ưu tiên:** P0. **Effort:** S-M.
- **Rủi ro/tương thích:** `latest.jpg` (snapshot fallback + `/benchmark/run`) cần pipeline detect của Frigate còn decode — không nên tắt detect hẳn ngay; go2rtc có endpoint frame JPEG (`/api/frame.jpeg?src=`) có thể thay thế nếu muốn tắt hẳn (cần kiểm chứng trên bản bundled).
- **Đề xuất triển khai:** Bước 1 (ngay): hạ `detect` của `cam_traffic_violation` và `cam_lpr` xuống 640×360@2fps (perception lane không dùng detect-res của Frigate; chỉ LoiterOverlay dùng detect-res làm viewBox — cam đó giữ nguyên); Bước 2: nâng mem_limit frigate 8g→10g HOẶC giảm tải detect như trên rồi quan sát; Bước 3 (thử nghiệm có kiểm soát): `detect.enabled:false` từng cam + chuyển snapshot sang go2rtc, đo lượng CPU/RAM giải phóng (~vài trăm % CPU theo `SLIDE_NOTES.md §2.3`).
- **Cách đo thành công:** 24h không còn log "Detection stuck"; frigate mem < 80% limit; frigate CPU giảm ≥ 30%.
- **File/component:** `config.yml`, `docker-compose.yml` (frigate), dashboard snapshot path.

### R4 — Không có ground truth, không có benchmark session/history ⇒ toàn bộ trục "Accuracy" của Mandatory Metrics chưa đo được
- **Hạng mục:** Benchmark validity (gap lớn nhất toàn hệ thống).
- **Vấn đề:** Harness hiện chỉ đo **tốc độ tức thời** (latency 1 frame, p50/p95 rolling của perception). Không có: ground truth cho bất kỳ task nào; không persist kết quả; không false-alerts/hour; không missed events; không so sánh continuous vs Frigate-triggered; không đánh giá ID-switch. `/benchmark/run` = 1 frame × 2 model crowd, kết quả vứt đi sau khi render.
- **Bằng chứng:** `app.py:230-304` (one-shot, không ghi); `Metrics` backend chỉ có 2 counter (app.py:169-171, 317-322); `ROADMAP.md:20-26` M2/M3 toàn ☐; dashboard "Stateless (không SQLite)" (`SYSTEM.md §6.1`); không file GT nào trong repo; §7 CLAUDE.md liệt kê accuracy metrics là **Mandatory**.
- **Tác động benchmark:** Mọi kết luận "model X tốt hơn Y" hiện chỉ dựa trên latency + quan sát mắt — không đủ để chọn model per task (mục tiêu M4).
- **Ưu tiên:** P0. **Effort:** L (nhưng chia nhỏ được — xem mục 8).
- **Rủi ro/tương thích:** Không đáng kể — ghi JSONL/Parquet + SQLite là CPU-nhẹ, ARM64 ok.
- **Đề xuất triển khai:** (a) service nhỏ `metrics_recorder` (hoặc thread trong dashboard) subscribe `perception/#` ghi JSONL/Parquet xuống `./bench_results/` (mem_limit 256m); (b) tận dụng đặc tính **video loop cố định**: annotate GT 1 lần theo offset trong loop (mỗi video 1 file GT: khoảng thời gian có crowd/fire/biển số đúng/xe dừng); (c) mode replay offline: script đọc thẳng mp4 (không qua RTSP), đẩy từng frame có index xác định vào `/detect` → map GT theo frame index, tính precision/recall/missed/FA-hour; (d) `/benchmark/run` nhận `n_frames`/`duration` + ghi session ra store, UI đọc lại (M3).
- **Cách đo thành công:** Có bảng P/R + false-alerts/hour + missed events cho ≥ 2 task từ dữ liệu ghi tự động; benchmark tab hiển thị lịch sử ≥ 10 session.
- **File/component:** mới `bench/` hoặc `metrics_recorder/`, `dashboard/backend/app.py`, `BenchmarkPanel.tsx`, `ROADMAP.md` M3.

### R5 — locate_gpu (và stream_core) chạy thường trực chung GPU với live lane
- **Hạng mục:** GPU contention / kỷ luật tài nguyên.
- **Vấn đề:** Service chỉ-dành-cho-benchmark đang ghim ~25GB unified memory và sẵn sàng chiếm GPU 18-20s/frame nếu ai đó bấm benchmark, làm méo mọi số đo latency của live lane trong lúc đó.
- **Bằng chứng:** compose khai báo profile `benchmark` + `restart:"no"` (compose:61-78) nhưng `docker ps` cho thấy Up 5h (start 01:31), nvidia-smi 24.8GB; stream_core (profile `realtime-benchmark`) cũng Up 23h.
- **Tác động benchmark:** (a) ~25GB unified memory mất trắng trên máy chung; (b) benchmark compare RF-DETR vs Locate chạy trên GPU đang phục vụ live ⇒ latency 2 phía đều nhiễm nhau ⇒ so sánh không công bằng.
- **Ưu tiên:** P0 (tài nguyên máy chung) . **Effort:** S.
- **Đề xuất triển khai:** `docker compose stop locate_gpu stream_core` khi không benchmark (ghi quy trình vào SYSTEM.md); dài hơn: `/benchmark/run` tự từ chối khi phát hiện live lane đang chạy HOẶC ghi rõ "benchmark window" vào kết quả; cân nhắc chỉ bật locate_gpu trong khung giờ benchmark.
- **Cách đo thành công:** nvidia-smi không còn process ~25GB khi idle; kết quả benchmark ghi kèm trạng thái các service lúc đo.
- **File/component:** vận hành + `SYSTEM.md §6.3`, `app.py /benchmark/run`.

---

## 3. Bottleneck hiệu năng / scale

### R6 — Double decode + decode CPU (không NVDEC): trần scale rõ ràng
- **Vấn đề:** Mỗi luồng bị decode ≥ 2 lần (Frigate detect + perception OpenCV/FFmpeg CPU; thêm lần 3 nếu record transcode). perception_worker đang ăn ~1.05 core cho 5 cam ⇒ ~0.2 core/cam chỉ để decode+encode, cap `cpus:2.0` ⇒ trần thực tế ~9-10 cam trước khi đói CPU (chưa tính Frigate ~700-885% từng đo).
- **Bằng chứng:** docker stats (perception 104.77%/200%); worker.py:176 (`cv2.VideoCapture(CAP_FFMPEG)` — CPU); `SLIDE_NOTES.md §3` tự nhận "DECODE 2 LẦN… không NVDEC".
- **Tác động benchmark:** Metric "Cost" (CPU%) phồng; không mô phỏng được scale "nhiều camera" trong Mandatory Metrics.
- **Ưu tiên:** P1. **Effort:** M-L.
- **Rủi ro/tương thích:** NVDEC trên GB10 ARM64: OpenCV pip wheel không có CUDA; đường khả thi là **PyAV/FFmpeg với `-hwaccel cuda`** (ffmpeg arm64 + nvdec cần build/lấy bản có hỗ trợ) hoặc GStreamer nvv4l2decoder — cần PoC trước khi cam kết; đây đúng loại việc "nghiên cứu/thử nghiệm".
- **Đề xuất triển khai:** PoC 1 container decode NVDEC 1 luồng 1080p đo CPU/GPU; nếu đạt → grabber backend thứ 2 trong perception (env switch). Song song, quick-win không cần NVDEC: **downscale frame trước khi encode JPEG cho các task dùng imgsz=640** (fire/traffic; person tùy model) — hiện gửi full 1920×1080 rồi model tự resize (worker.py:928 encode full-res; fire server imgsz=640).
- **Cách đo thành công:** CPU perception_worker/camera giảm ≥ 40%; hoặc kết luận PoC "NVDEC không khả thi trên stack hiện tại" với số liệu.
- **File/component:** `perception_worker/worker.py` (FrameGrabber, encode_jpeg), Dockerfile perception.

### R7 — JPEG + HTTP đồng bộ mỗi frame/model, không batching, mỗi camera 1 thread tuần tự
- **Vấn đề:** Mỗi task = encode JPEG (~vài ms CPU) + POST + chờ đồng bộ. Task trên cùng camera chạy **tuần tự trong 1 thread** (worker.py:934-944) ⇒ cam nhiều task (vd traffic+lpr) tự giới hạn FPS lẫn nhau; nhiều camera cùng model không được batch (RF-DETR phục vụ 2 cam × 8fps = 16 req/s tuần tự từng request).
- **Bằng chứng:** worker.py:928-944; các server `/detect` nhận 1 ảnh/request; `SLIDE_DECK.md §1D` "❌ CHƯA tối ưu throughput".
- **Tác động benchmark:** p95 e2e bị cộng dồn queueing; throughput/FPS thực tế của model bị đánh giá thấp hơn khả năng (không batch); cost CPU encode tăng theo cam×fps.
- **Ưu tiên:** P1 (chỉ khi mục tiêu scale test; với 5-6 cam hiện tại chưa nghẽn). **Effort:** M (async/batch nhỏ) / L (Triton).
- **Đề xuất triển khai:** Thứ tự tăng dần công sức: (1) tách task cùng camera ra thread/async riêng; (2) server-side micro-batching (gom request trong 10-20ms window ở crowd_gpu — torch batch inference); (3) chỉ khi cần >20 cam: đánh giá Triton/shared-memory bus (đúng Non-Goals hiện tại nên để nghiên cứu).
- **Cách đo thành công:** Với 2 person-cam: throughput crowd_gpu (req/s) tăng ≥ 50% khi batch=2; p95 e2e không tăng.
- **File/component:** worker.py `process_camera`, `model_workers/rf_detr_large/server.py`.

### R8 — MQTT/WS fanout: payload per-frame trùng lặp ×3, chưa stress test, broadcast không lọc
- **Vấn đề:** Mỗi frame person-cam phát 3 topic chồng dữ liệu (`objects` + `tracks` + `crowd` đều chứa bbox); dashboard broadcast **mọi** message tới **mọi** WS client (app.py:58-66) không lọc theo camera/tab; chưa có số đo msg/s, bytes/s, độ trễ broker ở scale cam×fps.
- **Bằng chứng:** worker.py:547-551 (3 publish/frame); app.py `ConnectionManager.broadcast`; `SLIDE_DECK.md 1B#4` "chưa đo throughput".
- **Tác động benchmark:** Mandatory metric "throughput MQTT/WebSocket" chưa có; risk ẩn khi mô phỏng 10-20 cam.
- **Ưu tiên:** P1. **Effort:** S (đo) + M (diet).
- **Đề xuất triển khai:** (1) script publisher giả lập N cam × fps × payload thật, đo CPU mosquitto + latency sub; (2) gộp `objects`+`tracks`+`crowd` thành 1 topic `perception/frame_state/<cam>` (schema v2) hoặc bỏ `tracks` (dashboard không dùng — App.tsx chỉ xử lý `realtime_objects`, không có handler `realtime_tracks`); (3) WS: gửi theo subscription camera đang hiển thị.
- **Cách đo thành công:** Có bảng msg/s-bytes/s theo N cam; giảm ≥ 50% bytes/s per person-cam sau diet.
- **File/component:** worker.py publish paths, app.py, `SYSTEM.md §5`.

### R9 — Overlay ↔ video lệch timestamp, chưa đo end-to-end latency
- **Vấn đề:** Không có timestamp xuyên suốt: `frame_id` là counter cục bộ perception (worker.py:914), không map về PTS nguồn; dashboard không đo `wall_ts` alert → thời điểm render; WebRTC video và overlay MQTT đi 2 đường độc lập ⇒ box trễ so với vật thể — chấp nhận cho demo nhưng **chưa định lượng**.
- **Bằng chứng:** worker.py:356-367 (`wall_ts` = lúc grab, không phải PTS); không code nào đo skew; `SLIDE_DECK.md 1D` thừa nhận "Lệch".
- **Tác động benchmark:** "End-to-end latency" nằm trong Mandatory Metrics nhưng hiện không đo được; đánh giá trực quan (video vs box) không tin được khi so model nhanh/chậm.
- **Ưu tiên:** P1. **Effort:** M.
- **Đề xuất triển khai:** (a) frontend log `Date.now() - wall_ts` khi nhận message (cần NTP-đồng-bộ trong cùng host — ok vì cùng máy) → histogram e2e MQTT→WS→render; (b) đo tổng thể bằng video test có đồng hồ/QR timestamp burn-in: so hình trên tile vs box; (c) thêm `capture_ts` từ PTS RTSP nếu PoC NVDEC/PyAV thành công (PyAV expose PTS, OpenCV không).
- **Cách đo thành công:** Báo cáo p50/p95 e2e (capture→render) per task; số này xuất hiện trong benchmark result.
- **File/component:** worker.py, `useLiveChannel`/App.tsx, bench script.

---

## 4. Gap theo từng bài toán AI

### 4.1 Crowd
| Vấn đề | Bằng chứng | Nhận định |
|---|---|---|
| Heuristic foot-point + height-gate không có calibration/homography — khoảng cách ảnh ≠ khoảng cách thật khi camera nghiêng mạnh | `clustering.py:35-42` | Chấp nhận được cho MVP; sai số tăng ở góc nhìn xiên. GT-đánh-giá (R4) trước, homography sau |
| Không có zone/polygon cho crowd (TARGET_ZONES rỗng) | `SYSTEM.md §7` "Zones: chưa định nghĩa" | Cần khi so với dense scene thật |
| Dense crowd/occlusion nặng: detector + clustering sẽ vỡ, chưa có backend density-map | docs/Tong_hop §2.3 (CSRNet) | Ứng viên benchmark M4 — xem R18 |
| `person_count` = size cụm lớn nhất (không phải tổng người) — dễ hiểu nhầm khi đọc metric | worker.py:496-501, SYSTEM.md §5.1 | Đặt tên lại field hoặc thêm `total_persons` vào topic per-frame (alert đã có) |
| O(n²) neighbor check — 200+ người/frame bắt đầu tốn | clustering.py:44-49 | Chỉ xử lý khi test dense scene |

### R10 — Loitering: dwell reset theo ID-switch, chưa từng đo (M2 ☐)
- **Vấn đề:** Dwell = tuổi track ByteTrack (worker.py:454-473); ID-switch/occlusion ⇒ track mới ⇒ dwell về 0 ⇒ **missed loitering**; `TRACK_LOST_SECONDS=2` rất ngắn với occlusion. Tần suất ID-switch chưa đo (ROADMAP M2 ☐).
- **Tác động benchmark:** Missed events của loitering không định lượng được; so sánh tracker không có cơ sở.
- **Ưu tiên:** P1. **Effort:** M.
- **Đề xuất:** (1) đo trước: script replay `cam_loiter.mp4`/VIRAT (VIRAT có annotation public) qua detector + 2 tracker (ByteTrack vs BoT-SORT qua thư viện `boxmot` — PyTorch, chạy CPU/GPU ARM64 ok) → đếm ID-switch, IDF1/HOTA bằng `TrackEval`; (2) nếu ID-switch là nguồn miss chính: giữ dwell theo "vị trí" (re-attach track mới vào state cũ nếu IoU cao) — rẻ hơn nhiều so với gắn Re-ID.
- **Cách đo thành công:** Bảng ID-switch/phút + loitering recall trước/sau trên cùng video GT.
- **File/component:** worker.py `track_people/handle_loiter_alerts`, bench script, `boxmot`/`TrackEval` (cả hai license nghiên cứu OK, thuần PyTorch/numpy).

### R11 — Fire/smoke: chỉ lọc N-of-M tĩnh, chưa có bộ test false-positive
- **Vấn đề:** YOLO ảnh tĩnh + debounce N-of-M là toàn bộ lớp chống FP (worker.py:592-613); chưa test đèn xe/sương mù/mây/nắng đỏ/đèn đường — các nguồn FP kinh điển; docs/Tong_hop §2.5 khuyến nghị spatio-temporal (optical flow) + xác thực đa tầng.
- **Bằng chứng:** chỉ 1 video demo tinh chỉnh ngưỡng (config.py:208-218 ghi rõ tune trên 1 video); không có FP test set.
- **Tác động benchmark:** false alerts/hour của fire = chưa biết; đây là metric quan trọng nhất của bài fire.
- **Ưu tiên:** P1. **Effort:** M.
- **Đề xuất:** (1) gom bộ clip FP-prone (D-Fire dataset — nghiên cứu, có cả negative; thêm clip đèn xe/sương tự tải) chạy replay → FA/hour theo ngưỡng; (2) nếu FP cao: thêm gate temporal rẻ (flicker/motion trên vùng bbox bằng Farneback optical flow CPU) trước khi nâng cấp model; (3) event-driven VLM confirm (Qwen2-VL) để dành nghiên cứu M4 — chỉ chạy khi alert, không stream liên tục.
- **Cách đo thành công:** FA/hour trên bộ negative < ngưỡng đặt trước (vd < 1/h) mà recall trên positive không giảm.
- **File/component:** bench replay, worker.py fire gate, `model_workers/fire_smoke`.

### R12 — LPR: chưa có plate-level/char-level accuracy, biển nhỏ nhiễu (đã biết nhưng chưa xử lý)
- **Vấn đề:** (a) Không có GT biển số ⇒ không có plate accuracy / char accuracy / CER; (b) detector imgsz=640 trên khung 1080p → biển xa nhỏ; (c) không plate-tracking/best-frame: nhánh "confident-single-read" bắn theo từng text ⇒ cùng 1 xe đọc sai 2 kiểu ("30N7399" vs "30N73993") tạo 2 alert, suppress theo exact text không chặn được (worker.py:675-697); (d) mỗi biến thể OCR sai = 1 false alert.
- **Bằng chứng:** worker.py:662-697; CLAUDE.md §4 "OCR small/far plates noisy"; `SYSTEM.md §9` đã ghi hướng (imgsz/tracking) nhưng chưa làm.
- **Tác động benchmark:** LPR chỉ đo được latency (~21ms), không đo được cái người dùng cần: đọc đúng bao nhiêu %.
- **Ưu tiên:** P1. **Effort:** M.
- **Đề xuất:** (1) annotate GT biển số cho `Ground-level_off-side_road.mp4` (loop cố định — đếm được từng xe); tính plate-exact-match + CER; (2) track plate bằng IoU đơn giản trong perception, gom OCR reads per track, chọn majority/highest-conf khi track kết thúc → 1 alert/xe (giảm cả FP lẫn duplicate); (3) thử `LPR_DETECTOR_IMGSZ=1280` đo lại p95 (GPU 21ms hiện còn headroom lớn); (4) SAHI slicing chỉ khi (3) không đủ (SAHI thuần Python, ARM64 ok).
- **Cách đo thành công:** Bảng plate-accuracy/CER trước-sau; duplicate-alert-rate giảm ≥ 80%.
- **File/component:** worker.py `process_lpr_frame`, compose env lpr_gpu, bench GT.

### R13 — Lane traffic (stopped + no_helmet): hoàn thiện phần "làm chưa tới" ngoài R1/R2
- **Vấn đề (liệt kê theo bằng chứng):**
  1. `TRAFFIC_ATTACH_LPR` parse xong **không dùng**; `traffic_lpr_cache` chỉ được đọc, không bao giờ ghi (worker.py:81,123,748) ⇒ plate-attach của PLAN.md:109-112 là dead code, "plate attach success rate" = 0 vĩnh viễn.
  2. Topic `perception/stopped_vehicle/<cam>` publish **mọi vehicle đang track** (worker.py:798-813), frontend coi `vehicles.length>0` = "STOPPED" (CameraTile.tsx:338,417-421) ⇒ xe đang chạy cũng bị viền amber + badge STOPPED — sai ngữ nghĩa, phá đánh giá trực quan.
  3. Metrics service thiếu `latency_ms_p50/p95` mà PLAN.md:41-42 yêu cầu (server.py:80-88 chỉ có counters).
  4. `helmet_track_states` không bao giờ dọn (worker.py:847 setdefault, không có xóa) ⇒ leak chậm theo track id tăng vô hạn.
  5. Logic clear: `moving_since` không reset khi xe dừng lại giữa chừng (worker.py:758-767) ⇒ 2 lần "nhúc nhích" cách nhau >3s có thể clear oan; history ≤ 5 điểm ⇒ `speed_ratio=0.0` = coi như dừng ngay khi track mới xuất hiện trong zone (worker.py:735-736).
  6. `server.py` traffic vi phạm rule "No comments" của repo, còn nguyên comment tự-thoại ("Wait, the plan schema allows...", server.py:186-191).
  7. Overlay stopped/no_helmet hardcode `viewBox="0 0 1920 1080"` (CameraTile.tsx:154,171) thay vì `inference_resolution` như 4 overlay cũ.
  8. no_helmet không có rider và không có motorcycle ⇒ không alert dù model báo no_helmet (worker.py:819-835) — chấp nhận nếu là thiết kế, cần ghi vào docs.
- **Ưu tiên:** P1 (1,2,3) / P2 (4,5,7,8) / P1 (6 — vì là rule repo). **Effort:** tổng M.
- **Cách đo thành công:** Chạy đủ 9 test case trong PLAN.md:217-226 và pass; badge STOPPED chỉ hiện khi có track thỏa dwell.
- **File/component:** worker.py:700-866, `traffic_violation_gpu/server.py`, CameraTile.tsx, EventsTimeline.tsx.

---

## 5. Gap so với scope cuối trong docs (`docs/Tong_hop_tai_lieu.md`)

| Nhóm nghiệp vụ (docs §2) | Trạng thái repo | Ghi chú benchmark |
|---|---|---|
| MTMC Re-ID (FastReID, topology, HOTA) | ☐ chưa bắt đầu (M4) | Bước 0 đúng đắn: đo HOTA/IDF1 đơn-camera trước (R10) rồi mới multi-cam |
| Face Recognition (SCRFD→ArcFace→FAISS) | ☐ chưa bắt đầu (M4) | InsightFace PyTorch chạy ARM64/CUDA ok; SCRFD ONNX → cần detector .pt thay thế (cùng bài học LPR) |
| Violence/Fall (pose→ST-GCN) | ☐ — nhưng `videos/fight_0012.mpeg` đã được thả vào (02:20 hôm nay) → có vẻ là task kế tiếp | YOLOv8-pose (.pt, GPU ok) + phân loại skeleton; xem R19 |
| PAR (attribute) | ☐ | 2-stage YOLO→classifier, dễ Docker hóa |
| Wrong-way / lane violation (vector cross product) | ☐ (stopped vehicle mới làm 1 phần "đỗ sai") | Tận dụng ngay tracker + zone đã có trong traffic lane — effort thấp nhất trong nhóm |
| Illegal dumping (PFSM) | ☐ | Cần chuỗi trạng thái — làm sau khi tracking ổn |
| Sidewalk encroachment (segmentation) | ☐ | SegFormer/U-Net torch ok trên GB10 |
| Event-driven VLM confirm | ☐ | Qwen2-VL-2B/7B FP16 chạy được trên unified memory; chỉ chạy khi alert (đúng docs §2.3) |
| Store-and-forward MQTT, edge-server phân tán | ☐ — **cố ý** (Non-Goals) | Giữ nguyên |

Kết luận mục này: hệ thống mới phủ **4.5/≈10** bài toán của scope cuối; hạ tầng thêm-bài-toán (pattern model_worker + lane) đã chứng minh hoạt động, nên gap còn lại chủ yếu là **công sức model + GT**, không phải kiến trúc.

---

## 6. Đề xuất kiến trúc

### R14 — Hạ vai trò Frigate detect xuống mức tối thiểu phục vụ record/snapshot
- Nội dung chính đã ở R3. Bổ sung quyết định kiến trúc: **giữ Frigate** (go2rtc/WebRTC/record đáng giá, thay bằng MediaMTX không đáng công cho benchmark), nhưng coi detect CPU là "tiện ích snapshot", không phải AI: mọi cam mới mặc định detect 640×360@2fps hoặc `detect.enabled:false` nếu không cần `latest.jpg`.
- **Ưu tiên:** P1 · **Effort:** S · **Đo:** CPU frigate < 300% ổn định với 5-6 cam.

### R15 — Nguồn config chung cho tham số trùng lặp (CLUSTER_*, threshold)
- **Vấn đề:** `CLUSTER_SIZE_RATIO_MIN/DISTANCE_FACTOR/CROWD_THRESHOLD` phải khớp tay giữa 2 service (compose:189-191 vs 259-262); các env traffic mới (compose:276-291) **chưa được comment-declare trong header `config.py`** như rule §6 CLAUDE.md yêu cầu (config.py sửa lần cuối 07-01, trước khi thêm traffic); `SYSTEM.md`/`AGENTS.md`/`ROADMAP.md` chưa nhắc lane traffic ⇒ docs lệch code (PLAN.md:169-174 tự yêu cầu update mà chưa làm).
- **Đề xuất:** (a) ngắn hạn: 1 block YAML anchor trong compose (`x-cluster-env: &cluster ...`) dùng chung cho 2 service — hết lệch tay, zero code; (b) cập nhật header config.py + SYSTEM.md §2/§5 (schema stopped_vehicle.v1, helmet.v1) + ROADMAP M4 + AGENTS/CLAUDE khi lane traffic được nghiệm thu; (c) contract test nhỏ: pytest validate sample payload mỗi schema (tests/ hiện chỉ có clustering + breaker).
- **Ưu tiên:** P1 · **Effort:** S · **Đo:** grep 1 nguồn duy nhất cho CLUSTER_*; CI/pytest chạy contract test pass.

### R16 — Tách benchmark khỏi live thành quy trình có kiểm soát
- Gộp R5 + `/benchmark/run` mở rộng: benchmark session chạy khi live lane pause (hoặc ghi rõ trạng thái GPU lúc đo vào kết quả); locate_gpu chỉ sống trong session. Lý do: unified memory + 1 GPU ⇒ không bao giờ có số đo sạch nếu trộn.
- **Ưu tiên:** P1 · **Effort:** M · **Đo:** report benchmark có field `environment` (danh sách container active + GPU util lúc đo).

### Việc kiến trúc cân nhắc rồi XẾP SAU (có lý do)
| Ý tưởng | Quyết định | Lý do |
|---|---|---|
| Triton Inference Server | Nghiên cứu, chưa làm | Non-Goals §7; ≤ 6 cam chưa nghẽn GPU; công build image ARM64+sm_121 lớn |
| gRPC/shared-memory frame bus | Sau khi có số đo R8 | Đừng tối ưu đường chưa đo |
| Tự build onnxruntime-gpu/TensorRT sm_121 | Chỉ khi ≥ 2 model tương lai bắt buộc ONNX | Bài học LPR cho thấy luôn có đường .pt; build ORT là công trình maintain dài |
| MediaMTX thay Frigate | Không | Mất record/snapshot/WebRTC tích hợp; lợi ích chính (bỏ detect CPU) đạt được bằng R14 rẻ hơn |

---

## 7. Đề xuất model / tool / dataset (đã lọc ARM64/GB10; license nghiên cứu OK)

### R17 — Helmet model cho no_helmet (chặn R2)
| Phương án | Ghi chú |
|---|---|
| Fine-tune **YOLO11s/m** trên dataset helmet giao thông: Kaggle "Rider Helmet Detection"/"Traffic violation dataset", Roboflow Universe "motorcycle helmet" (nhiều bộ CC BY 4.0) | Train ngay trên GB10 (torch cu128 đã có recipe); classes chuẩn `helmet/no_helmet/rider/motorcycle` khớp `_normalize_helmet_class` |
| HF checkpoint có sẵn (search "helmet yolov8 rider") — chất lượng không đảm bảo, phải eval | Nhanh nhất để un-block lane; ghi license vào README |
- **Ưu tiên:** P0 (gắn R2) · **Effort:** M · **Đo:** no_helmet P/R trên đoạn GT của `No_helm_accidentally_stopped.MOV`.

### R18 — Crowd backend thứ 3: density map cho dense crowd
- **Candidate:** **DM-Count** hoặc **P2PNet** (PyTorch thuần, pretrained ShanghaiTech; license nghiên cứu) làm `model_workers/crowd_density/`; trả `person_count` + point map (bbox giả từ point để tương thích schema).
- **Giá trị benchmark:** so RF-DETR (detect+cluster) vs density-map trên scene đặc — đúng tinh thần M4; LocateAnything đã chứng minh pipeline compare nhiều backend chạy được.
- **Ưu tiên:** P2 · **Effort:** M · **Đo:** MAE đếm người trên clip dense (Harder_grouping.mp4 đã có sẵn trong videos/).

### R19 — Violence/fall (task kế tiếp theo bằng chứng `fight_0012.mpeg`)
- **Stack đề xuất:** YOLOv8-pose (.pt, ultralytics, GPU ok) → cửa sổ skeleton 1-2s → **ST-GCN/MS-G3D** pretrained (PyTorch, research license) hoặc baseline rẻ hơn: rule-based fall (tỷ lệ khung xương) + classifier nhỏ.
- **Lưu ý:** thêm đúng pattern `model_workers/<m>` + lane mới; KHÔNG nhét vào traffic lane.
- **Ưu tiên:** P2 · **Effort:** L · **Đo:** P/R trên bộ clip fight/normal (RWF-2000 — research license).

### R20 — Tool đo lường (không model)
| Tool | Dùng cho | Tương thích |
|---|---|---|
| `TrackEval` / `motmetrics` | HOTA/IDF1/ID-switch (R10) | Python thuần, ARM64 ok |
| `pycocotools` mAP | detector accuracy vs GT | ok |
| `sahi` | LPR/biển nhỏ khi cần (R12) | Python thuần |
| `boxmot` | BoT-SORT/OC-SORT so với ByteTrack | PyTorch, ok |
| Datasets GT: VIRAT annotations (crowd/loiter), D-Fire (fire/negative), UFPR-ALPR hoặc tự annotate video loop (LPR), AI City Track 5 (helmet — cần đăng ký) | R4 | tải về `datasets/` ngoài image |

---

## 8. Kế hoạch đo đạc (thứ tự thực thi)

1. **Tuần 1 — Instrument (không GT):**
   - `metrics_recorder` ghi `perception/#` → JSONL/Parquet (R4a).
   - e2e latency: log `Date.now()-wall_ts` tại frontend + p50/p95 per topic (R9a).
   - Baseline tài nguyên: cron `docker stats`/`nvidia-smi` 1 phút/lần vào cùng store trong 24h.
   - MQTT stress script offline (R8).
   *Deliverable: bảng "hiện trạng đo được" — FPS thực, e2e p50/p95, msg/s, CPU/GPU per service.*
2. **Tuần 2 — GT + replay:**
   - Annotate GT theo loop-offset cho 5 video active (crowd interval, loiter interval, fire onset, danh sách biển số, khoảng xe dừng/no-helmet).
   - Script replay mp4 → `/detect` trực tiếp theo frame-index → P/R, missed, FA/hour, trigger delay per task (R4c).
   *Deliverable: bảng accuracy per task per model — lần đầu tiên trục Accuracy có số.*
3. **Tuần 3 — So sánh có kiểm soát:**
   - Continuous (perception) vs Frigate-triggered (bật lại ai_worker tạm) trên cùng video + GT (đóng M2).
   - ByteTrack vs BoT-SORT ID-switch/IDF1 (R10).
   - LPR imgsz 640 vs 1280; có/không plate-tracking (R12).
   - Benchmark session persist + UI history (đóng M3).
4. **Định kỳ:** mỗi lần thêm model/lane ⇒ bắt buộc chạy lại replay suite + ghi session.

---

## 9. Roadmap ưu tiên (theo benchmark, không theo production)

| Đợt | Việc | ID |
|---|---|---|
| **Ngay (P0)** | Fix alert misrouting traffic; helmet weights + health trung thực; hạ tải/ổn định Frigate detect (mem 97.8% + stuck); stop locate_gpu/stream_core ngoài benchmark; khởi động metrics_recorder | R1, R2, R3, R5, R4a |
| **Sau khi có số đo (P1)** | GT + replay suite; e2e latency; benchmark session history (M3); LPR plate-tracking + imgsz; loitering ID-switch (M2); fire FP set; MQTT diet + stress; hoàn thiện lane traffic (R13.1-3); config/docs đồng bộ (R15); Frigate detect policy (R14); benchmark tách live (R16) | R4b-d, R9-R13, R14-R16 |
| **Nghiên cứu/thử nghiệm (P2)** | NVDEC PoC; micro-batching; density-map backend; violence/pose lane; wrong-way (rẻ, tái dùng tracker+zone); Re-ID/Face khởi động theo M4; event-driven VLM confirm | R6, R7, R18, R19, mục 5 |
| **Không làm đợt này (P3)** | Xem mục 10 | — |

---

## 10. Việc cố ý không làm (và lý do)

| Hạng mục | Lý do bỏ qua |
|---|---|
| Auth / Authorization / TLS | Non-Goals §7; không ảnh hưởng số đo benchmark (mạng nội bộ) |
| HA, backup, SRE, audit log pháp lý | Production-only |
| NATS/Redis backbone, store-and-forward | Non-Goals; MQTT local đủ cho scale benchmark sau khi đo R8 |
| Triton/DeepStream ngay bây giờ | Chưa nghẽn ở 6 cam; chi phí ARM64+sm_121 lớn; chỉ mở lại nếu scale test thất bại |
| Thay Frigate bằng MediaMTX | Lợi ích chính đạt được bằng R14 với effort nhỏ hơn nhiều |
| Tự build onnxruntime-gpu / TensorRT sm_121 | Đường PyTorch .pt vẫn thông cho mọi model đã cần; build ORT là cam kết maintain dài hạn |
| Face-blur / ẩn danh / privacy compliance | Production/legal; dữ liệu benchmark là video công khai/mock |
| Operator workflow, acknowledgment UI | Non-Goals — UI hiện tại đủ cho demo/benchmark |
| QR biển số QCVN 08:2024 | Ngoài phạm vi harness hiện tại |

---

## Phụ lục A — Tóm tắt lỗi cụ thể phát hiện khi audit (để fix nhanh)

| # | File:dòng | Lỗi |
|---|---|---|
| 1 | `App.tsx:191` vs `worker.py:749-756,860-863` | Alert stopped/no_helmet rơi vào nhánh loitering (unreachable branch tại App.tsx:277,288); clear stopped xóa nhầm loiter overlay |
| 2 | `traffic_violation_gpu/server.py:90-99` + `model_cache/traffic_hf/` rỗng | Helmet model thiếu nhưng health="ok"; lane no_helmet chết im lặng |
| 3 | `worker.py:81,123,748` | `TRAFFIC_ATTACH_LPR`/`traffic_lpr_cache` dead code — plate attach chưa hiện thực |
| 4 | `worker.py:798-813` + `CameraTile.tsx:338,417` | Topic stopped_vehicle publish mọi vehicle đang track → badge/viền "STOPPED" cho cả xe đang chạy |
| 5 | `traffic_violation_gpu/server.py:80-88` | `/metrics` thiếu `latency_ms_p50/p95` (PLAN.md:41-42 yêu cầu) |
| 6 | `worker.py:847` | `helmet_track_states` không bao giờ dọn — leak chậm |
| 7 | `worker.py:735-736,758-767` | speed_ratio=0 khi history≤5 (dừng "ngay lập tức"); `moving_since` không reset → clear oan |
| 8 | `traffic_violation_gpu/server.py:25,136,159,186-191` | Vi phạm rule no-comments; còn comment tự-thoại của LLM chưa dọn |
| 9 | `CameraTile.tsx:154,171` | Overlay stopped/no_helmet hardcode viewBox 1920×1080 thay vì `inference_resolution` |
| 10 | `config.py` header; `SYSTEM.md`; `ROADMAP.md`; `AGENTS.md` | Chưa khai báo/ghi nhận lane traffic + env mới (rule §6 CLAUDE.md; PLAN.md:169-174) |
| 11 | compose:65,202 vs runtime | locate_gpu + stream_core đang chạy ngoài profile — ghim ~25GB unified memory |
| 12 | frigate logs + docker stats | Detection stuck ×3/ngày, mem 7.83/8Gi — cần R3 trước khi thêm bất kỳ cam nào |
