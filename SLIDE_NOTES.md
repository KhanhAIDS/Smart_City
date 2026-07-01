# Ràng buộc & Phản biện Hệ thống Smart_City — Ghi chú làm slide

> Đọc nhanh. Mỗi mục = 1 slide. In đậm = từ khoá đưa lên slide.

---

## 0. Một câu tóm tắt (slide mở đầu)
Đây là **MVP / benchmark đa-bài-toán** (crowd, loitering, fire/smoke, LPR) chạy trên **phần cứng mới toanh chưa được hỗ trợ sẵn (NVIDIA GB10, ARM64)**. Thành tựu chính: **vượt được 3 "bức tường nền tảng"** để có AI thời-gian-thực. Điểm cần nói thẳng: kiến trúc đủ tốt cho **quick-win/demo**, **chưa tối ưu throughput** để scale.

---

## 1. Ràng buộc từ MÁY CHỦ (GB10 / ARM64)

### 1.1 Ba "bức tường nền tảng" (điểm nhấn chính)
| # | Bức tường | Vì sao | Hệ quả / cách vượt |
|---|-----------|--------|--------------------|
| 1 | **ARM64 không có AVX/AVX2** | AVX/AVX2 là tập lệnh **x86**; ARM chỉ có NEON/SVE | Frigate **Face Rec / LPR / Semantic Search** (viết cho AVX2) **KHÔNG chạy được** → phải tách thành **GPU worker riêng** |
| 2 | **GB10 sm_121 quá mới** | Frigate prebuilt không có detector GPU cho sm_121; TensorRT detector đã bị bỏ từ Frigate 0.16 | **Frigate detect phải chạy CPU** (OpenVINO). Torch bundle nvrtc 12.8 chỉ tới **sm_120** → JIT crash → **pin `nvidia-cuda-nvrtc-cu12==12.9.86` SAU torch** |
| 3 | **Không có wheel GPU chính thức cho ARM64/GB10 trên PyPI** | Hỗ trợ đầy đủ chỉ đến với CUDA/toolchain mới hơn (13.x); wheel GPU ARM64 chưa sẵn → nhiều người **phải tự build từ source** | Dùng **torch `2.11.0+cu128`** (có wheel) cho model GPU; model ONNX nhẹ để **CPU** |

### 1.2 Vấn đề "bộ 3" khi muốn chạy ONNX trên GPU
Rất nhiều model pretrained ship dạng **ONNX**. Để ONNX chạy **trên GPU** GB10 cần 1 trong 3 backend — **cả 3 đều kẹt**:

| Cách chạy ONNX trên GPU | Vấn đề trên GB10/ARM64 |
|--------------------------|------------------------|
| **onnxruntime-gpu (CUDA EP)** | **Không có wheel aarch64 + CUDA** trên PyPI |
| **TensorRT EP** | Dựng engine cho **sm_121 rất khó**; Frigate cũng bỏ TensorRT từ 0.16 |
| **Tự build ORT + CUDA EP** cho sm_121 | **Tốn công build từ source** + phải tự maintain |

➡️ **Kết luận:** bỏ hẳn "ONNX-trên-GPU" cho phần nặng. **Detector chuyển sang PyTorch/CUDA** (ultralytics YOLO có sẵn wheel cu128). ONNX chỉ giữ cho **tác vụ nhẹ chạy CPU** (OCR biển số CCT). → Đây chính là lý do LPR = **YOLO11 (GPU torch) + fast-plate-ocr (CPU onnx)**.

### 1.3 Ràng buộc phần cứng & vận hành khác
- **Unified memory ~121GB** (CPU+GPU dùng chung, **không có VRAM riêng**): *lợi* = model lớn vẫn nạp được; *rủi ro* = OOM kéo sập cả máy.
- **Máy dùng chung, nhiều người**: RAM dao động → **phải check `free -h`/`nvidia-smi` trước khi tải nặng**, **mỗi container bắt buộc `mem_limit`**, **AI không được sudo**, chỉ env per-user, **không webcam** (headless).
- **1 GPU duy nhất** chia cho nhiều container → có **tranh chấp tài nguyên**.
- **Recipe GB10 tái lập** (nên có 1 slide): `cuda 12.8-runtime` + `torch 2.11.0+cu128` + **pin nvrtc 12.9.86 sau torch** (sm_121 nhị-phân-tương-thích sm_120).

---

## 2. Ràng buộc từ KIẾN TRÚC / mô hình / tools

### 2.1 Vì sao chọn Frigate — và Frigate ràng buộc gì
- **Chọn Frigate** vì: NVR chín, lo sẵn phần khó (**RTSP, go2rtc/WebRTC, record, snapshot, motion CPU**) → **tiết kiệm công** cho MVP.
- **Nhưng Frigate ràng buộc:**
  - Trên máy này **detect chỉ chạy CPU** (không có đường GPU).
  - **UI Frigate không overlay được AI ngoài** → buộc phải làm **dashboard riêng**.
  - Frigate là **motion/event-triggered**, không phải AI liên tục. **Nhịp update MQTT cho vật thể DI CHUYỂN ~2.4s là cố hữu, không có núm chỉnh** → không đủ "mượt" cho overlay real-time.

### 2.2 Lời giải kiến trúc hiện tại
- **Realtime-stream-first:** `perception_worker` **đọc RTSP liên tục ở FPS cố định**, độc lập motion Frigate → **đây mới là lane AI chính**. Frigate lùi về vai trò **hạ tầng stream/record**.
- **Mỗi model = 1 container GPU**, chung hợp đồng **`POST /detect` (JPEG thô)**; **clustering gom về 1 module dùng chung**; **MQTT → WebSocket** bridge; **dashboard stateless** (không DB).

### 2.3 Số liệu (nên có 1 slide bảng)
| Thành phần | Latency / tải | Ghi chú |
|-----------|----------------|---------|
| RF-DETR-Large (crowd) | **~35–60 ms**/frame | backend crowd live |
| Fire/smoke YOLOv8 | ~vài chục ms | debounce N-of-M |
| **LPR (YOLO11+OCR)** | **p50 ~21 ms / p95 ~40 ms** | detect GPU + OCR CPU |
| LocateAnything-3B (VLM) | **~18–20 GIÂY**/frame | **chỉ benchmark**, ~300× chậm hơn RF-DETR |
| Frigate detect (CPU) | **~700–885% / trần 1400%** | nút thắt là CPU detect |
| perception_worker | person/fire **8 fps**, LPR **3 fps** | — |

---

## 3. Phản biện (mạnh / yếu / khắc phục) — slide "đánh giá thẳng thắn"

| Quyết định | Điểm mạnh | Điểm yếu | Hướng khắc phục |
|-----------|-----------|----------|-----------------|
| **Dùng Frigate làm nền** | Chín, có sẵn WebRTC/record/RTSP; tiết kiệm công | Detect CPU **gần như thừa** (AI thật nằm ở perception_worker); nhịp update chậm; thêm 1 tầng | **Hạ/tắt detect Frigate**; hoặc thay bằng **MediaMTX** thuần cho stream, giữ Frigate chỉ để record |
| **perception_worker đọc RTSP riêng** | Real-time, tách khỏi motion, code đơn giản | **DECODE 2 LẦN** cùng 1 luồng (Frigate + perception); decode bằng **CPU ffmpeg**, không NVDEC | **Bật NVDEC**; hoặc **1 pipeline decode chung** cấp frame cho mọi model |
| **Mỗi model 1 container + POST JPEG** | Module hoá, dễ thêm/thay model, hợp đồng rõ | **Encode JPEG + HTTP mỗi frame/model**; **không batch**; nhiều CUDA context; overhead serialize + network | **Triton Inference Server** (dynamic batching, share GPU); **gRPC/shared-memory** thay JPEG+HTTP; batch nhiều cam |
| **Detector torch/CUDA, OCR CPU** | Vòng qua rào ONNX-GPU, chạy được ngay | Nếu sau cần **nhiều model ONNX GPU** thì vẫn kẹt | **Dựng 1 lần onnxruntime-gpu / TensorRT cho sm_121** → mở khoá cả hệ sinh thái ONNX |
| **1 GPU chia nhiều container** | Đơn giản; unified memory đủ chứa | **Tranh chấp GPU**; LocateAnything-3B (18–20s) làm nghẽn nếu bật chung; **OOM rủi ro cả máy** | Triton/MPS; **tách benchmark khỏi live**; hàng đợi ưu tiên |
| **Clustering module chung** | 1 nguồn sự thật, benchmark khớp live | Tham số vẫn phải **khớp tay qua env** | Inject 1 config chung cho cả 2 service |
| **Dashboard stateless, không DB** | Nhẹ, dễ suy luận | **Không có lịch sử/metrics theo thời gian** → khó đo accuracy dài hạn | Thêm store nhẹ (SQLite/parquet) cho benchmark history |
| **Loitering theo tuổi track (ByteTrack)** | Đơn giản, không cần model thêm | **ID-switch reset dwell**; không Re-ID xuyên camera | BoT-SORT/Re-ID; chọn best-frame cho LPR |
| **Nguồn cam = mp4 loop qua go2rtc exec** | Demo phase-sync khéo | Là **hack demo**, không phải camera thật | Cắm **RTSP camera thật** khi lên production |

### 3.1 Ý tưởng out-of-the-box (slide "nếu làm lại / khi scale")
- ❓ **Có thật sự cần Frigate detect không?** perception_worker đã làm AI → **tắt detect Frigate giải phóng ~700%+ CPU**.
- 🔁 **Gom decode:** 1 pipeline **NVDEC** giải mã 1 lần, nuôi mọi model → hết decode 2 lần.
- 📦 **Gom inference:** **Triton** thay N container FastAPI → **batching + share VRAM + đo p50/p95 sẵn**.
- 🚚 **Bỏ JPEG+HTTP:** dùng **shared-memory / gRPC frame bus** (giảm encode/decode/network mỗi frame).
- 🔓 **Biến rào thành tài sản:** đầu tư **build ORT-GPU/TensorRT cho sm_121 một lần** → dùng được kho model ONNX khổng lồ.
- 🧱 **Câu hỏi lớn:** nếu chỉ cần stream + record, **stack mỏng hơn Frigate** (MediaMTX + record) có thể gọn hơn cho benchmark.

---

## 4. Những gì nên đưa lên slide (chốt)
- **Câu chuyện "3 bức tường nền tảng"** (ARM64 không AVX / GB10 sm_121 quá mới / thiếu wheel GPU) + cách vượt — **điểm nhấn kỹ thuật ấn tượng nhất**.
- **Sơ đồ luồng 1 dòng:** `Camera/MP4 → Frigate/go2rtc RTSP → perception_worker → model_workers (GPU) → MQTT → Dashboard`.
- **Recipe GB10 tái lập** (cuda12.8 + torch cu128 + pin nvrtc 12.9.86).
- **Bảng số liệu** latency + tài nguyên (mục 2.3).
- **4 bài toán chạy được:** crowd, loitering, fire/smoke, LPR.
- **Ranh giới rõ ràng — đây là MVP/benchmark, KHÔNG phải production.** Non-goals: Auth/TLS, HA, NATS/Redis backbone, thay thế recorder hoàn chỉnh, Triton/DeepStream (trừ khi cần scale).
- **Định vị & lộ trình:** quick-win đã đạt; hướng scale rõ = **NVDEC + Triton + batching + frame bus**.

> **Chốt 1 câu:** *Kiến trúc hiện tại là một quick-win vững trên phần cứng chưa được hỗ trợ; điểm yếu lớn nhất về hiệu năng là **decode 2 lần + không batch + JPEG/HTTP** — muốn scale phải **gom decode và gom inference**.*
