# SLIDE DECK — Smart_City (nội dung slide, dạng bảng)

> Nội dung đã "chín" để đưa lên slide. Mỗi `##` = 1 slide. Fact-check dựa trên `config.yml` / `docker-compose.yml` / code thực tế, KHÔNG theo trí nhớ.

---

## SLIDE 1 — Xây dựng Nền tảng AI Camera

### 1A. Nền tảng là gì (chưa nói bài toán cụ thể)
Ý tưởng cốt lõi: **tách 2 đường dữ liệu** — video nặng đi WebRTC, metadata nhẹ đi MQTT.

| Tầng | Thành phần | Vai trò nền tảng |
|------|-----------|------------------|
| Nguồn | Camera RTSP / mp4 | Đầu vào video |
| Thu nhận & phân phối | **Frigate + go2rtc** | Ingest 1 lần → restream RTSP (`:8554`), WebRTC live (`:8555`/`:1984`), record, snapshot, motion (CPU) |
| Suy luận **realtime** | **perception_worker** | Đọc RTSP liên tục ở FPS cố định → gọi model GPU → sinh detections/tracks/alerts |
| Suy luận **sự kiện** (legacy) | **ai_worker** | Nghe `frigate/events`, xử lý theo sự kiện (hiện **tắt**) |
| Model AI | **model_workers/\*** (GPU) | Mỗi model 1 container, chung hợp đồng `POST /detect` (JPEG thô) |
| Bus thông điệp | **MQTT broker** | Pub/sub nhẹ cho metadata/alert — **KHÔNG tải video** |
| Trình bày | **Dashboard** | Video qua **WebRTC** (trực tiếp go2rtc) + overlay/alert qua **WebSocket** |

**Sơ đồ 1 dòng:** `Camera/mp4 → Frigate/go2rtc (RTSP :8554) → perception_worker → model_workers (GPU) → MQTT → Dashboard (WS + WebRTC)`

### 1B. Các quyết định nền tảng — vì sao / lợi / hại

| # | Quyết định | Tại sao chọn | Lợi ích | Tác hại / rủi ro |
|---|-----------|--------------|---------|------------------|
| 1 | Dùng **luồng go2rtc restream** của Frigate | go2rtc bundled sẵn; mở nguồn **1 lần** rồi fan-out cho detect + record + live + perception; là đường **bắt buộc** để có WebRTC low-latency | Nguồn/cam chỉ bị mở 1 lần; 1 producer chia nhiều consumer; WebRTC mượt cho browser | Chỉ tiết kiệm **kết nối nguồn**, **mỗi consumer vẫn decode riêng**; thêm 1 hop RTSP nội bộ |
| 2 | Xây **lane thứ 2 (perception_worker)** song song Frigate | Frigate detect chỉ **CPU** + **motion-triggered** + nhịp update ~2.4s → không đủ cho AI GPU liên tục; Frigate không chạy được model nặng, **không overlay AI ngoài** | AI thật trên GPU, FPS điều khiển được, tách khỏi motion, dễ thêm model | **Decode 2 lần** cùng luồng; 2 bộ detect song song ⇒ tốn CPU; trùng vai trò |
| 3 | **Giữ Frigate** làm nền | Off-the-shelf: lo sẵn RTSP/restream/WebRTC/record/snapshot/motion/API; **cung cấp chính go2rtc** ta đang phụ thuộc; hữu ích cho bài toán event/forensic sau này | Tiết kiệm rất nhiều công dựng NVR; có sẵn record/snapshot/event cho tác vụ **không-realtime** | Kéo theo detect CPU **~700%+ gần như thừa**; 1 tầng nặng chỉ để dùng vài tính năng |
| 4 | **MQTT** làm bus metadata/alert | Pub/sub nhẹ, decoupled, đúng công cụ cho JSON nhỏ; tách hẳn khỏi đường video | Fan-out nhiều client dễ; độ trễ ms; dashboard chỉ subscribe (read-only) | Payload per-frame (objects/crowd) có thể **phình** khi nhiều cam×fps; **persistence tắt** ⇒ mất message khi đứt; **chưa đo throughput** ở scale |

### 1C. Fact-check (đã kiểm bằng repo, không theo trí nhớ)

| Nhận định | Verdict | Bằng chứng / lưu ý |
|-----------|---------|--------------------|
| go2rtc là thành phần bundled trong Frigate | ✅ ĐÚNG | Khối `go2rtc:` trong `config.yml`; Frigate expose `:8554/:1984/:8555` |
| Hệ thống thực sự dùng luồng go2rtc | ✅ ĐÚNG | Frigate detect **và** perception cùng đọc `rtsp://…:8554/<cam>` (preset `preset-rtsp-restream`) |
| go2rtc giảm decode | ⚠️ SAI/nói lệch | Chỉ giảm **kết nối nguồn**, KHÔNG giảm decode; mỗi consumer tự decode H264 |
| "single-source phase-sync" là lợi ích lớn | ⚠️ CÓ ĐIỀU KIỆN | Chỉ đúng cho **mp4 loop demo**; camera thật (live) thì không còn ý nghĩa |
| WebRTC low-latency cần go2rtc | ✅ ĐÚNG | Đường video browser đi thẳng go2rtc (`/api/webrtc` → `frigate:1984`) |

### 1D. "Streaming + MQTT đã tối ưu perf/throughput/latency chưa?" — trả lời thẳng

| Thành phần | Đánh giá | Lý do |
|-----------|----------|-------|
| **MQTT bus** | ✅ Gần tối ưu cho MVP | Nhẹ, đúng việc, tách video khỏi metadata; chỉ lo ở scale lớn / persistence |
| **Đường video (WebRTC)** | ✅ Tốt | Trực tiếp go2rtc, low-latency, không qua backend |
| **Đường frame → inference** | ❌ CHƯA tối ưu throughput | Decode 2 lần + **JPEG encode + HTTP POST mỗi frame/model** + **không batch** + round-trip đồng bộ |
| **Đồng bộ overlay ↔ video** | ⚠️ Lệch | Overlay (MQTT→WS + inference delay + TTL) **trễ hơn** video ⇒ box bám trễ vật di chuyển (chấp nhận cho demo) |

### 1E. Câu hỏi vặn / phản biện (để thảo luận hoặc ghi chú nói)
1. perception_worker đã làm **toàn bộ** AI → **giữ Frigate detect (CPU ~700%) để làm gì?** Tắt/hạ fps thì mất gì? (Đáp: chỉ mất trigger record + event motion.)
2. Ta phụ thuộc go2rtc — nhưng go2rtc **chạy standalone được**. Có cần cả Frigate không, hay chỉ cần **go2rtc + 1 script record**? Frigate thêm giá trị gì ta **đang** dùng? (record/snapshot/motion/API/config).
3. go2rtc restream **không** cứu double-decode → với **camera thật**, lợi ích thực còn lại là gì (giảm kết nối cam) so với chi phí thêm 1 hop?
4. **Đã đo throughput MQTT chưa?** Bao nhiêu camera × fps thì JSON per-frame + broker thành nút thắt?
5. Mọi service **cùng 1 host** → có cần **JPEG+HTTP** không, hay **shared-memory / gRPC** để bỏ encode/parse mỗi frame?
6. "Phase-sync single-source" là lợi ích **demo (mp4 loop)** — còn giá trị khi lắp **camera thật** không?
7. Video real-time nhưng overlay trễ → cần **đồng bộ theo timestamp** để box khớp khung, hay chấp nhận trailing?

> **Chốt slide 1:** Nền tảng tách đúng **video (WebRTC) ↔ metadata (MQTT)** và tái dùng Frigate/go2rtc để đi nhanh (quick-win). Đánh đổi lớn nhất: **giữ Frigate ⇒ decode 2 lần + detect CPU thừa**, và **đường frame→inference chưa tối ưu throughput**.
