# 🔬 Kế hoạch 1 tuần — Best-in-Class Implementation

> **Ngày phân tích:** 2026-06-25 | **Triết lý:** Implement tốt nhất ngay từ đầu, không MVP
> **Ràng buộc tôn trọng:** Loop video (không live camera), server chia sẻ aarch64, GB10 sm_121

---

## 0. Phát hiện quan trọng từ research

Trước khi đi vào kế hoạch, có 4 phát hiện thay đổi đáng kể so với kế hoạch cũ:

### ⚡ Phát hiện #1: YOLO26-pose (01/2026) — mới hơn YOLO11

| | YOLO11-pose | **YOLO26-pose** |
|---|---|---|
| Release | 09/2024 | **01/2026** |
| NMS | Cần NMS post-processing | **NMS-free (end-to-end)** |
| DFL | Có | **Không (nhẹ hơn, edge-friendly)** |
| Accuracy | Cao | **Cao hơn (STAL small-target-aware)** |
| Latency (T4) | ~15ms | **~12.2ms** |
| Tasks | 6 tasks | **6 tasks (cùng pipeline)** |

→ Đã có sẵn qua `ultralytics`, auto-download weights. Vì hệ thống đã dùng `ultralytics` (fire_gpu), **YOLO26x-pose là lựa chọn tốt nhất** — SOTA, nhanh nhất, cùng ecosystem.

### ⚡ Phát hiện #2: Fall detection — Rule-based là OBSOLETE

Research 2025-2026 rõ ràng: **GCN-based >> rule-based** cho fall detection.

| | Rule-based (angle+aspect) | **ST-GCN / PoseConv3D** |
|---|---|---|
| Accuracy | ~80-85% (cao FPR) | **>95-99%** |
| FPR | Cao (cúi, ngồi = false positive) | **Rất thấp** |
| Robustness | Kém (nhạy góc camera) | **Tốt (tự học pattern)** |
| Generalization | Cần tune per-camera | **Transfer learning tốt** |
| Cần train? | Không | **Có (fine-tune NTU pretrained)** |

→ Rule-based chỉ là MVP/prototype. Muốn best-in-class → **PHẢI dùng temporal classifier**.

Nhưng đây là thực tế: **chưa có pretrained fall-detection model "lắp vào chạy"**. Phải fine-tune ST-GCN (pretrained NTU RGB+D 60/120 actions) trên dữ liệu fall. Tuy nhiên:
- NTU RGB+D **có sẵn action class "fall down"** (action A043) → có thể extract + binary classify
- Pretrained weights ST-GCN trên NTU → fine-tune nhanh (~vài giờ trên GPU)
- **PYSKL** framework (OpenMMLab) hỗ trợ cả ST-GCN và PoseConv3D

### ⚡ Phát hiện #3: NVDEC hoạt động trên GB10

**NVDEC hardware decode HOẠT ĐỘNG trên GB10** (Blackwell, hỗ trợ H.264/H.265/AV1). `PyNvVideoCodec` compatible với sm_121. Điều này mở ra khả năng:
- Decode video trên GPU (không tốn CPU Frigate)
- Ring buffer frame trên GPU memory
- **Tiền đề cho Lane B (temporal/streaming) không còn là unknown risk**

### ⚡ Phát hiện #4: InsightFace/PaddleOCR — KHÔNG có GPU wheel cho aarch64

| Framework | aarch64 GPU Support | Giải pháp |
|-----------|:---:|-----------|
| InsightFace (ONNX) | ❌ Không có pre-built onnxruntime-gpu | Build from source hoặc **dùng PyTorch + SCRFD/ArcFace trực tiếp** |
| PaddleOCR | ❌ Không có paddlepaddle-gpu aarch64 | Build from source hoặc **dùng EasyOCR/TrOCR (PyTorch-native)** |

→ Đây là ràng buộc cứng. Best practice cho GB10: **bypass ONNX/Paddle, dùng PyTorch thuần** cho mọi model (torch cu128 đã verify hoạt động trên sm_121).

---

## 1. Kiến trúc best-in-class cho Fall Detection

Thay vì rule-based 1-frame, pipeline tốt nhất là **2-stage với temporal context**:

```
┌─────────────────────────────────────────────────────────────────┐
│                    fall_gpu container (GPU)                      │
│                                                                  │
│  Stage 1: YOLO26x-pose                                          │
│  ─────────────────────                                          │
│  Input: raw JPEG → 17 COCO keypoints + bbox + confidence        │
│  Tốc độ: ~12ms/frame (GB10, ultralytics auto-download)          │
│  Multi-person: ✅ (phát hiện + pose TẤT CẢ người trong frame)   │
│                                                                  │
│  Stage 2: ST-GCN binary classifier                              │
│  ─────────────────────────────                                  │
│  Input: chuỗi N=30 frame skeleton sequences                     │
│  Output: {fall: probability, normal: probability}                │
│  Pretrained: NTU RGB+D 60 → fine-tune binary (fall vs ADL)      │
│  Tốc độ: ~5-10ms/sequence (ST-GCN rất nhẹ)                     │
│                                                                  │
│  Tổng: ~17-22ms/frame = có thể chạy real-time                   │
└─────────────────────────────────────────────────────────────────┘
```

### Vì sao 2-stage thay vì 1-stage?

> [!IMPORTANT]
> **Pose estimation (YOLO26-pose)** = nhận diện chính xác các khớp cơ thể trên MỖI frame.
> **Temporal classifier (ST-GCN)** = phân tích CHUỖI chuyển động qua NHIỀU frame → phân biệt "ngã" vs "ngồi xuống" vs "cúi nhặt đồ".
>
> 1-frame rule-based KHÔNG THỂ phân biệt được "đang ngã" vs "đang ngồi xuống" vì ở 1 thời điểm cả 2 hành động trông GIỐNG NHAU. Chỉ khác nhau ở TỐC ĐỘ + TRAJECTORY theo thời gian.

### So sánh với fire/smoke (tại sao fire/smoke dùng 1-frame OK mà fall thì không)

| Đặc điểm | Fire/Smoke | Fall Detection |
|-----------|-----------|---------------|
| Tín hiệu | Hình dạng/màu sắc ĐẶC TRƯNG | Tư thế thay đổi THEO THỜI GIAN |
| 1-frame đủ? | ✅ (lửa trông khác mọi thứ) | ❌ (nằm = ngã = nghỉ = yoga) |
| Confusion | Ánh nắng ↔ lửa (ít) | Ngồi ↔ ngã ↔ cúi (nhiều) |
| Debounce N/M đủ? | ✅ (persistence = chắc chắn) | ❌ (cần hiểu TRAJECTORY, không chỉ persistence) |

---

## 2. Kế hoạch chi tiết: Fall Detection best-in-class

### Ngày 1 (25/06, Thứ Tư): Research + Spike hardware

**Mục tiêu: Xác nhận YOLO26-pose + ST-GCN chạy được trên GB10**

- [ ] **Spike YOLO26-pose trên GB10:**
  ```python
  from ultralytics import YOLO
  model = YOLO("yolo26x-pose.pt")  # auto-download
  results = model("test_image.jpg", device="cuda")
  # Verify: keypoints trả về, inference time, GPU memory
  ```
  - Đo: inference time, VRAM usage, verify sm_121 JIT OK (nvrtc 12.9.86 đã pin)
  - So sánh kết quả với YOLO11x-pose (cùng ảnh) → chọn cái cho keypoint confidence cao hơn

- [ ] **Spike ST-GCN / PYSKL trên GB10:**
  - Clone PYSKL, verify load pretrained NTU RGB+D weights
  - Hoặc: dùng `mmaction2` (OpenMMLab) có sẵn ST-GCN + PoseConv3D configs
  - Test inference trên dummy skeleton sequence (30 frames × 17 keypoints × 3 coords)
  - Đo: memory footprint, inference time per sequence
  - **Kiểm tra PyTorch compatibility** (mmaction2 có chạy trên torch cu128 + sm_121 không)

- [ ] **Đánh giá lựa chọn temporal model:**

  | Model | Accuracy NTU | Params | Speed | Dễ deploy | **Chọn?** |
  |-------|:---:|:---:|:---:|:---:|:---:|
  | ST-GCN | ~81.5% | 3.1M | Rất nhanh | Có | ✅ Baseline |
  | 2s-AGCN | ~88.5% | 3.5M | Nhanh | Có | ✅ Nếu ST-GCN OK |
  | PoseConv3D | ~94%+ | Nặng hơn | Trung bình | PYSKL | ⭐ Best accuracy |
  | SkateFormer | SOTA | - | - | Phức tạp | ❌ Quá mới |

  > **Quyết định dự kiến:** PoseConv3D nếu memory cho phép (best accuracy), fallback ST-GCN (nhẹ + proven).

- [ ] **Check `free -h` + `nvidia-smi`:** xác nhận headroom cho thêm 1 container GPU

---

### Ngày 2 (26/06, Thứ Năm): Build `fall_gpu` service

**Mục tiêu: Service `/detect` hoàn chỉnh với 2-stage pipeline**

- [ ] **Tạo `model_workers/fall_detection/`:**

  ```
  model_workers/fall_detection/
  ├── server.py          # FastAPI, 2-stage pipeline
  ├── pose_engine.py     # YOLO26x-pose wrapper
  ├── fall_classifier.py # ST-GCN/PoseConv3D wrapper + sliding window
  ├── Dockerfile         # GB10 recipe (cuda 12.8 + torch cu128 + nvrtc pin)
  └── requirements.txt
  ```

- [ ] **`server.py` — API contract (mở rộng hợp đồng `/detect`):**

  ```
  POST /detect  (raw JPEG body)
  Response:
  {
    "persons": [
      {
        "bbox": [x1, y1, x2, y2],
        "keypoints": [[x,y,conf], ...],  // 17 COCO keypoints
        "fall_probability": 0.92,         // từ temporal classifier
        "is_falling": true,               // fall_probability >= threshold
        "action_class": "fall"            // "fall" | "normal" | "sitting" | ...
      }
    ],
    "fall_detected": true,                // có ít nhất 1 người ngã
    "fall_count": 1,
    "model": "yolo26x-pose+stgcn",
    "inference_ms": 22.3
  }
  ```

  > [!NOTE]
  > **Khác biệt quan trọng vs crowd/fire:** Fall detection cần **state per-person** (sliding window
  > skeleton sequence). Service phải maintain **ring buffer** per tracked person. Đây là điểm
  > phức tạp nhất — person ID từ YOLO26 bbox cần track qua frame.

- [ ] **Skeleton buffer strategy:**
  - YOLO26-pose cho bbox + keypoints per frame
  - Track person across frames bằng **IoU matching** (bbox overlap) — đơn giản, đủ cho ~4fps pump
  - Mỗi tracked person có ring buffer 30 frames skeleton
  - Khi buffer đầy → chạy ST-GCN classify → trả fall_probability
  - **Buffer chưa đầy → trả `fall_probability: null`** (chưa đủ temporal context, ~7.5s warmup ở 4fps)

- [ ] **Dockerfile:** cùng recipe GB10:
  ```dockerfile
  FROM nvidia/cuda:12.8.0-runtime-ubuntu24.04
  # torch cu128 + ultralytics + mmaction2/pyskl
  # Pin nvidia-cuda-nvrtc-cu12==12.9.86 AFTER torch
  ```

- [ ] **Docker-compose:** `fall_gpu` service, mem_limit ~6g, GPU passthrough

---

### Ngày 3 (27/06, Thứ Sáu): Fine-tune ST-GCN trên fall data

**Mục tiêu: Có model fall classifier chất lượng cao**

> [!IMPORTANT]
> **Đây là bước "training" duy nhất.** Nhưng không phải train-from-scratch — là **fine-tune** model đã pretrain trên NTU RGB+D 120 actions, chuyển sang binary classifier (fall vs normal).

- [ ] **Dataset strategy (không cần thu thập footage thật):**

  | Dataset | Loại | Dùng cho |
  |---------|------|----------|
  | **NTU RGB+D 60** | Skeleton sequences, Action A043 = "falling down" | Fine-tune chính |
  | **UR Fall Detection** | 30 falls + 40 ADL sequences | Validation cross-domain |
  | **Le2i** | Fall sequences from 4 camera angles | Test robustness |

  - NTU RGB+D có sẵn skeleton annotation (25 joints, 3D coords) → không cần chạy pose estimation trên raw video
  - Nhưng **hệ thống dùng YOLO26-pose (17 COCO joints, 2D)** → cần mapping:
    - COCO 17 joints ⊂ NTU 25 joints (ánh xạ 1:1 cho các joint chung)
    - Hoặc: chạy YOLO26-pose trên NTU RGB videos → extract skeleton cùng format hệ thống dùng

- [ ] **Fine-tune workflow:**
  ```
  1. Load pretrained ST-GCN/PoseConv3D (NTU RGB+D 60, 60 action classes)
  2. Replace FC head: 60 classes → 2 classes (fall, normal)
  3. Freeze backbone (GCN layers), chỉ train head
  4. Dataset: NTU A043 (fall) + subset ADL actions (walk, sit, stand, pick up, ...)
  5. Train ~50-100 epochs (nhẹ vì chỉ train head)
  6. Unfreeze backbone, train thêm ~20 epochs (learning rate thấp)
  7. Evaluate trên UR Fall Detection (cross-domain)
  ```

- [ ] **Target metric:** 
  - Accuracy ≥ 95% trên NTU test set
  - **FPR < 1% trên ADL sequences** (quan trọng nhất — false alarm rate)
  - Recall ≥ 90% trên fall sequences

- [ ] **Nếu không kịp fine-tune ngày 3:** Backup plan = rule-based enhanced (kết hợp nhiều signal: velocity + angle + head-position + bbox-aspect-ratio change-rate). Không tốt bằng ST-GCN nhưng hơn rule-based đơn giản.

---

### Ngày 4 (28/06, Thứ Bảy): Tích hợp ai_worker + Dashboard

**Mục tiêu: End-to-end fall detection trên dashboard**

- [ ] **`ai_worker/worker.py` — `FallDetectionPump`:**
  - Clone pattern `FireSmokePump` (daemon thread per camera)
  - Poll `latest.jpg` ở `FALL_PUMP_FPS=4` (mỗi 250ms)
  - POST raw JPEG → `fall_gpu:8000/detect`
  - **Debounce:** fall_detected liên tục N=3/M=5 frames → publish alert
  - **CLEAR:** không fall_detected trong `FALL_CLEAR_SECONDS=5` → `active:false`
  - Publish `ai_worker/alerts/fall`

- [ ] **`config.py` — tham số fall detection:**
  ```python
  # === FALL DETECTION ===
  # FALL_CAMERAS: cameras to monitor (config.yml cam names)
  # FALL_PUMP_FPS: frame polling rate (default: 4)
  # FALL_CONFIDENCE: minimum fall_probability to consider (default: 0.7)
  # FALL_PERSIST_N / FALL_PERSIST_M: debounce window (default: 3/5)
  # FALL_CLEAR_SECONDS: seconds without detection before CLEAR (default: 5)
  ```

- [ ] **MQTT alert schema (`ai_worker/alerts/fall`):**
  ```json
  {
    "camera": "cam_fall",
    "persons": [
      {
        "bbox": [x1, y1, x2, y2],
        "keypoints": [[x,y,conf], ...],
        "fall_probability": 0.92,
        "action_class": "fall"
      }
    ],
    "fall_count": 1,
    "active": true,
    "model": "yolo26x-pose+stgcn",
    "inference_resolution": [w, h],
    "timestamp": "2026-06-28T..Z"
  }
  ```

- [ ] **Dashboard integration:**
  - `mqtt_bridge`: `fall` → `fall_alert`
  - `types.ts`: `FallAlert` interface
  - `CameraTile.tsx` → `FallOverlay`:
    - **Skeleton visualization** (vẽ cả skeleton trên tile, không chỉ bbox!)
    - Skeleton bình thường = xanh lá mờ
    - Skeleton ngã = **đỏ sáng + nhấp nháy** + bbox đỏ dày
    - Tag: `⚠️ FALL DETECTED` + probability%
    - **Keypoint connections** (lines giữa các joint) = trực quan hơn bbox đơn thuần
  - Toast: "⚠️ Fall detected on {camera}" (priority cao, không tự tắt)
  - Timeline: fall entry với icon riêng
  - Viền tile: **đỏ nhấp nháy** (khẩn cấp hơn crowd/loiter)

- [ ] **`ACTIVE_PROBLEM`:** thêm `fall` → `ACTIVE_PROBLEM=crowd,loitering,fire_smoke,fall`

---

### Ngày 5 (29/06, Chủ Nhật): Video demo + End-to-end testing

**Mục tiêu: Fall detection hoạt động hoàn chỉnh trên demo video**

- [ ] **Tạo/tìm video demo ngã:**

  **Option A (tốt nhất):** Tìm video fall detection trên internet → download → loop
  - UR Fall Detection dataset có video (30 falls, 4 angles)
  - Hoặc: tìm trên YouTube compilation "fall detection test video"
  - Cần video có: người đi bình thường → ngã → nằm → đứng dậy (đủ cycle)

  **Option B:** Tự dàn dựng (nếu có quyền quay trên server area)

- [ ] **Thiết lập `cam_fall` trong `config.yml`:**
  - Cùng pattern: go2rtc `exec:` ffmpeg `-stream_loop -1` → RTSP restream
  - `detect.fps=5` (cho Frigate track person → bbox)
  - `detect.stationary.interval=5`

- [ ] **Test matrix (checklist):**

  | Test case | Kỳ vọng | Kết quả |
  |-----------|---------|---------|
  | Người đi bình thường | NO alert | |
  | Người ngồi xuống ghế | NO alert | |
  | Người cúi nhặt đồ | NO alert | |
  | Người ngã (nhanh) | Alert trong ≤ 2s | |
  | Người ngã (chậm, trượt) | Alert trong ≤ 3s | |
  | Người nằm nghỉ (đã nằm sẵn) | NO alert (không có transition) | |
  | Người ngã → đứng dậy | Alert → CLEAR trong ≤ 5s | |
  | 2 người, 1 ngã | Alert chỉ cho người ngã | |
  | Loop seam (video lặp) | Không false positive ở seam | |

- [ ] **Đo performance:**
  - Inference latency: YOLO26-pose + ST-GCN trên GB10
  - Total pipeline latency: ngã → alert trên dashboard
  - GPU memory: fall_gpu + crowd_gpu + fire_gpu cùng lúc
  - CPU impact: Frigate vẫn < 1400% ceiling
  - FPR per hour trên video bình thường

---

### Ngày 6 (30/06, Thứ Hai): Face Recognition spike + technical debt

**Mục tiêu: Xác minh Face Recognition path trên GB10 và dọn dẹp**

- [ ] **Face Recognition — PyTorch path (bypass ONNX):**

  Vì `onnxruntime-gpu` không có aarch64 wheel, best-in-class approach trên GB10:

  | Component | Model | Framework | License |
  |-----------|-------|-----------|---------|
  | Detection | **SCRFD** hoặc **RetinaFace** | PyTorch (InsightFace source) | MIT |
  | Alignment | 5-point landmark affine | OpenCV | BSD |
  | Embedding | **ArcFace (IResNet-100)** | PyTorch (InsightFace source) | MIT |
  | Search | **FAISS-GPU** | PyTorch/C++ | MIT |

  ```python
  # Spike: verify InsightFace PyTorch models on GB10
  import torch
  from insightface.model_zoo import get_model
  # Hoặc load trực tiếp PyTorch checkpoint thay vì ONNX
  ```

  > **Nếu InsightFace PyTorch path không work:** Fallback = dùng `facenet-pytorch` (MIT, PyTorch-native, MTCNN + InceptionResnetV1, pretrained VGGFace2) — đơn giản hơn, verified PyTorch.

- [ ] **LPR — PyTorch path (bypass PaddleOCR):**

  | Component | Model | Framework |
  |-----------|-------|-----------|
  | Plate detection | YOLO26 (fine-tune hoặc pretrained) | ultralytics (PyTorch) |
  | Text recognition | **TrOCR** (Microsoft, Transformer-based) hoặc **EasyOCR** (PyTorch) | PyTorch |
  | Vietnamese support | EasyOCR có Vietnamese, TrOCR cần fine-tune | |

  > EasyOCR = PyTorch-native, hỗ trợ Vietnamese out-of-box, **dễ deploy nhất trên GB10**.

- [ ] **Technical debt — dọn dẹp:**
  - Xóa dead route `/inspect/<cam>/frame.jpg` ở backend
  - Multi-cluster D4: đánh giá scope, tạo issue/task
  - Review: fall detection code quality, error handling

---

### Ngày 7 (01/07, Thứ Ba): Polish + Documentation + Plan tuần 2

- [ ] **Polish fall detection:**
  - Dashboard UI: skeleton animation smooth, color transitions
  - Error handling: fall_gpu down → ai_worker graceful degrade
  - Logging: structured logs cho debug
  - Eval script: `model_workers/fall_detection/eval_fall.py` (tương tự `eval_fire.py`)

- [ ] **Cập nhật documentation:**
  - `CLAUDE.md §4`: thêm fall_gpu status, YOLO26-pose + ST-GCN
  - `SYSTEM.md`: 
    - §2.8 Fall Detection logic (2-stage pipeline, skeleton buffer, IoU tracking)
    - §3.4 `fall_gpu` contract (extended response schema)
    - §4.1 fall alert schema
  - `DEBUG_GUIDE.md`:
    - Mục kiểm thử fall bằng mắt (skeleton có bám người không, fall alert timing)
    - "Bình thường vs lỗi thật" cho fall detection
  - `ROADMAP.md`: cập nhật §2.3 Behavior → ◑ (fall done, violence/ẩu đả next)
  - `config.py`: comment-declare fall detection parameters

- [ ] **Plan tuần 2 — quyết định dựa trên spike ngày 6:**

  | Nếu... | Thì tuần 2... |
  |--------|--------------|
  | Face Rec PyTorch OK trên GB10 | Build `face_gpu` service (SCRFD→ArcFace→FAISS) |
  | Face Rec FAIL, LPR EasyOCR OK | Build `lpr_gpu` service (YOLO26→EasyOCR) |
  | Cả 2 cần work | Focus LPR (ít phức tạp hơn, EasyOCR PyTorch-native) |
  | NVDEC spike | Prototype Lane B (decode stream → ring buffer → temporal model) |

---

## 3. Phân tích tài nguyên

### Memory budget (GB10 unified memory ~121GB, available ~93GB)

| Service | mem_limit | GPU VRAM (approx) | Status |
|---------|-----------|-------------------|--------|
| frigate | 8g | ~0 (CPU OpenVINO) | ✅ Running |
| crowd_gpu (RF-DETR-Large) | 12g | ~1.7 GiB | ✅ Running |
| fire_gpu (YOLOv8) | 6g | ~1 GiB | ✅ Running |
| **fall_gpu (YOLO26x-pose + ST-GCN)** | **6g** | **~2-3 GiB** | 🆕 |
| locate_gpu | 16g | ~7 GiB | ⏹️ Stopped |
| ai_worker | 512m | 0 | ✅ Running |
| dashboard | 512m | 0 | ✅ Running |
| **Tổng (running)** | **~33g** | **~5-7 GiB** | ✅ OK |

→ Headroom: ~60g mem, ~114 GiB VRAM. **Rất thoải mái.**

### CPU budget

| Component | CPU% | Ceiling |
|-----------|------|---------|
| frigate (detect fps=5/8, cpus=14) | ~700-885% | 1400% |
| fall_gpu pump (4fps poll latest.jpg) | ~5% | negligible |
| ai_worker (threads) | ~1-2% | minimal |

→ **Không vấn đề gì.** Thêm 1 pump 4fps = thêm ~5% CPU.

---

## 4. Câu hỏi cần bạn quyết định

> [!IMPORTANT]
> **3 quyết định kỹ thuật:**

### Q1: Temporal classifier nào?

| Option | Pro | Con |
|--------|-----|-----|
| **A. ST-GCN** (đề xuất cho tuần 1) | Nhẹ, nhanh, proven, dễ fine-tune | Accuracy thấp hơn PoseConv3D |
| **B. PoseConv3D** (PYSKL) | SOTA accuracy trên NTU | Nặng hơn, phức tạp deploy hơn |
| **C. Cả 2** (ST-GCN trước, PoseConv3D sau) | Best of both | Mất thêm thời gian |

→ **Đề xuất: C** — ST-GCN trong tuần 1 (proven, nhanh), nâng cấp PoseConv3D tuần 2 nếu accuracy chưa đủ.

### Q2: Video demo ngã lấy từ đâu?

| Option | Pro | Con |
|--------|-----|-----|
| **A. Download UR Fall Detection dataset** | Có ground truth, multi-angle | Cần download (~2GB), scenes khác server |
| **B. Tìm video ngã trên YouTube/web** | Nhanh, đa dạng | Không có ground truth |
| **C. Tự quay** | Khớp môi trường thật | Cần người, thời gian |

→ **Đề xuất: A** — UR Fall Detection có sẵn GT annotations, dùng cho cả eval.

### Q3: NTU RGB+D dataset — download ở đâu?

NTU RGB+D cần đăng ký request từ NTU (research purpose). Nếu không kịp:
- **Alternative 1:** Dùng skeleton data đã trích sẵn (nhiều repo GitHub share)
- **Alternative 2:** Tự tạo skeleton data từ UR Fall Detection video + YOLO26-pose
- **Alternative 3:** Dùng rule-based enhanced làm v1 (velocity+angle+trajectory), fine-tune ST-GCN tuần sau khi có data

---

## 5. Deliverables cuối tuần — Best-in-class

| # | Deliverable | Tiêu chuẩn "best-in-class" |
|---|-------------|---------------------------|
| 1 | `fall_gpu` service | 2-stage pipeline (YOLO26x-pose + ST-GCN), sliding window skeleton buffer, IoU person tracking |
| 2 | Fall classifier | Fine-tuned trên fall data, FPR < 1%, accuracy > 95% |
| 3 | Dashboard: fall overlay | **Skeleton visualization** (vẽ xương, không chỉ bbox), animation, severity coloring |
| 4 | Eval pipeline | `eval_fall.py` với precision/recall/FPR trên test video |
| 5 | Demo video | Full cycle: walk → fall → lie → stand up, verified end-to-end |
| 6 | Spike results | Face Rec + LPR viability trên GB10 (PyTorch path) |
| 7 | Documentation | CLAUDE/SYSTEM/DEBUG_GUIDE/ROADMAP cập nhật đầy đủ |

> [!TIP]
> **Metric thành công best-in-class:**
> - Fall detection hoạt động end-to-end với **temporal classifier** (không phải rule-based)
> - **Skeleton overlay trên live tile** (trực quan hơn bbox đơn thuần)
> - FPR = 0 trên video bình thường (đi lại, ngồi, cúi)
> - Latency ngã → alert ≤ 3s (bao gồm warmup buffer)
> - **Phân biệt được ngã vs ngồi xuống** (test case quan trọng nhất)

---

## 6. Bức tranh lớn — 4 tuần tới

| Tuần | Focus | Bài toán |
|------|-------|---------|
| **Tuần 1** (hiện tại) | **Fall Detection** best-in-class + spike Face/LPR | ◑ → ✅ |
| **Tuần 2** | **Face Recognition** (PyTorch SCRFD→ArcFace→FAISS) hoặc **LPR** (YOLO26→EasyOCR) | ☐ → ◑ |
| **Tuần 3** | Bài còn lại (Face hoặc LPR) + **NVDEC Lane B spike** | ☐ → ◑ |
| **Tuần 4** | **Violence/ẩu đả** (VideoMAE fine-tune RWF-2000, cần Lane B) + **Early Smoke Phase-2** (D-Fire fine-tune) | ☐ → ◑ |
