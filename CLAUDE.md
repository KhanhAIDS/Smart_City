# AGENTS.md — Smart_City (Frigate NVR)

Auto-loaded each session. STABLE facts only; put volatile/task-specific details in the
task prompt, not here. This file does NOT auto-update — edit it manually when core
structure/logic changes. `CLAUDE.md` is a verbatim copy of this file.

## 1. Project & pipeline
Smart-city CCTV / object-detection **benchmark harness** (see §7).
**ARCHITECTURE VERDICT (2026-06-30):** **realtime-stream-first** for live AI overlays. Frigate remains the live/record/go2rtc base; AI inference reads Frigate/go2rtc RTSP streams directly.
Realtime AI Flow: Camera/MP4/RTSP → Frigate/go2rtc RTSP → `perception_worker` → `model_workers` → dashboard.
Legacy Frigate Event Flow: Frigate `frigate/events` → `ai_worker` (inactive by default; crowd/loiter fallback only).
A custom **dashboard** (`:8080`) gives a control-room view.
Heavy AI runs in separate GPU services on the GB10 (`model_workers/`).
Active cameras: `cam1_VIRAT_1` (crowd), `cam_loiter` (loitering), `cam_fire` (fire/smoke), `cam_lpr` (license-plate).

## 2. Environment — READ BEFORE RUNNING ANYTHING
SHARED, MULTI-USER company server.
- Arch: Linux **aarch64 (ARM64)** — images/binaries must be arm64.
- GPU: 1× **NVIDIA GB10 (Grace-Blackwell, sm_121)**, **unified memory** (CPU+GPU share ~121GB, no separate VRAM).
- 20 CPUs, /dev/shm 61GB.

### Hard constraints (safety)
- `tts` has sudo (password) but **the AI must never run sudo** — hand sudo commands to the user.
- Docker + nvidia-container-toolkit installed & running (GPU passthrough works).
- **Stay inside** the workspace (`.../Luyen_Minh_Khanh/Smart_City`).
- **Shared RAM fluctuates.** Always check `free -h` (the `available` column) + `nvidia-smi` before any heavy load; never risk OOM-ing neighbours. Every container needs a `mem_limit`.
- Per-user envs only (Docker / local venv in workspace) — no global pip/apt.
- Headless: no webcam.

### Model licensing policy
- This is an **experimental benchmark harness, not production** ⇒ **non-commercial / research-only model licenses are ACCEPTABLE.** Prefer permissive (Apache/MIT) when equivalent, but a research-only or non-commercial checkpoint is fine here. Just **record each model's license** where it is documented (`model_workers/<m>` note + §3 below). Re-evaluate licensing before any real deployment.

## 3. Key files
- `config.yml` — Frigate config (hand-authored, 0.17). OpenVINO CPU detector + SSDLite MobileNet. **4 enabled cameras** + go2rtc streams (WebRTC candidates on `:8555`). **Every active cam (`cam1_VIRAT_1`, `cam_loiter`, `cam_fire`, `cam_lpr`) uses a SINGLE go2rtc source:** each `go2rtc.streams.<cam>` is an `exec:` ffmpeg with `-stream_loop -1 ... -c copy ... {{output}}` (always-on looping producer); `cameras.<cam>` detect/record read it via `rtsp://127.0.0.1:8554/<cam>` (`preset-rtsp-restream`) → detect + live WebRTC share ONE looping producer ⇒ phase-synced (page refresh joins mid-loop instead of restarting at 0). The exec source needs `GO2RTC_ALLOW_ARBITRARY_EXEC=true` (frigate env in compose) + `{{output}}` escaped (Frigate runs `str.format` on stream strings). `cam4_entry_area`/`cam6_warehouse_door` are go2rtc streams only (no camera block ⇒ not detected).
- `config.py` (**root**) — central human-edited config hub for the legacy `ai_worker`. **Intentionally commented (Vietnamese) — do NOT strip comments.** Mounted into the `ai_worker` container (`/app/config.py`); edit → `docker compose restart ai_worker` (no rebuild). docker-compose env overrides it. Its header block also **comment-declares** the params that physically live in other files (perception_worker env, frontend TS constants, config.yml, model servers) — keep those pointers accurate.
- `smart_city_common/` — **shared Python package** imported by both `perception_worker` and the dashboard backend. `clustering.py` = `compute_crowd_clusters()` (the ONE crowd-clustering implementation: foot-point + height gate + adaptive distance + BFS connected components). `circuit_breaker.py` = reusable breaker. The formula is now single-source; only its **parameters** (env, per service) must be kept in sync.
- `ai_worker/` — legacy thin CPU bridge (no GPU, no models): `worker.py` still contains the old `frigate/events` crowd/loiter fallback, but compose sets `ACTIVE_PROBLEM=` (empty) + `CROWD_CAMERAS=NONE_CAMERA` ⇒ **fully inactive by default.** Fire/smoke pump was removed here; do not reintroduce `latest.jpg` polling.
- `perception_worker/` — primary realtime AI lane (`worker.py`): reads every active camera from `PERCEPTION_RTSP_TEMPLATE=rtsp://frigate:8554/{}` via OpenCV/FFmpeg. Per camera role: **person cams** → POST JPEG to `crowd_gpu`, ByteTrack tracking + crowd clustering (`smart_city_common`) + age-based loitering; **fire cams** → `fire_gpu`; **lpr cams** → `lpr_gpu`. Person/fire run at `PERCEPTION_FPS=8`, LPR at its own `LPR_FPS=3`. Publishes `perception/objects/*`, `perception/tracks/*`, `perception/crowd/*`, `perception/fire_smoke/*`, `perception/lpr/*`, and debounced `perception/alerts/*` (crowd/loitering/fire_smoke/lpr). `PERCEPTION_ALLOW_HTTP_FETCH=false` blocks accidental `/latest.jpg` polling.
- `model_workers/` — one GPU inference service per model, shared contract: `POST /detect` (raw JPEG → detections) + `/health` + `/metrics`. GB10 recipe: base `nvidia/cuda:12.8.0-runtime-ubuntu24.04` + torch `2.11.0+cu128` + pin `nvidia-cuda-nvrtc-cu12==12.9.86` AFTER torch (sm_121 JIT fix).
  - `rf_detr_large/` → `crowd_gpu` container: **RF-DETR-Large (Apache-2.0)**, the live crowd/person backend (~1.6 GiB GPU, ~35–60ms warm). Response `{person_count, detections[{bbox,confidence}], model}`. **Verified working.**
  - `locate_anything_3b/` → `locate_gpu` container: **NVIDIA LocateAnything-3B** (FP16), second crowd backend for the benchmark only (`benchmark` profile, ~18–20s/frame — a 3B autoregressive VLM). Needs `transformers==4.57.1` + `generation_mode="hybrid"`/`do_sample=True`. HF weights bind-mounted (`./model_cache/locate_hf:/opt/hf`).
  - `fire_smoke/` → `fire_gpu` container: **pretrained YOLOv8 fire+smoke via `ultralytics`** (`JJUNHYEOK/yolov8n_wildfire_detection`, classes `{smoke,fire}`). **Response schema differs:** `{detections[{bbox,confidence,class:"fire"|"smoke"}], fire_count, smoke_count, model}` (no `person_count`). `FIRE_MODEL_REPOS` is an ordered candidate list (brief's named repos were 404/gated → ungated equivalent). Weights `./model_cache/fire_hf`.
  - `lpr_plate_ocr/` → `lpr_gpu` container: **LPR = split detector(GPU) + OCR(CPU)**. Detector = **YOLO11 plate via `ultralytics` on CUDA** (default `morsetechlab/yolov11-license-plate-detection` → `license-plate-finetune-v1s.pt`; ordered candidate list in `server.py`, override via `LPR_DETECTOR_REPO`/`LPR_DETECTOR_FILE`). OCR = **`fast-plate-ocr` `cct-xs-v2-global-model` on CPU** (onnxruntime). **Why torch/CUDA not the old fast-alpr ONNX YOLOv9:** onnxruntime-gpu has no aarch64 wheel + no ungated YOLOv9 plate `.pt` on HF, so ONNX detection couldn't reach the GPU. Weights `./model_cache/lpr_hf:/opt/hf`. **Response schema `lpr.v1`:** `{plate_count, plates[{bbox,det_confidence,text,raw_text,ocr_confidence,confidence}], model}`, `model = "<detector-repo>+cct-xs-v2-global-model"`. Measured 2026-07-01: detect+OCR p50 ~21ms / p95 ~40ms on GPU.
- `dashboard/` — control-room web UI (`:8080`). FastAPI backend subscribes MQTT `perception/#` + legacy `ai_worker/alerts/#` + `frigate/events` → one WebSocket `/ws`; reverse-proxies Frigate `/api/*` **GET/HEAD-only** (writes→405) + `POST /api/webrtc` for go2rtc signaling + `/live/*` WS; `/benchmark/run` multi-model compare (reuses `smart_city_common` clustering); `/system/health` aggregate; serves the built SPA. Live tiles: tiered player **webrtc→snapshot**, with SVG overlays drawn on the tile: crowd cluster/member boxes, loiter box + dwell tag, fire/smoke boxes, LPR plate box+text. **Events timeline = live problem-alerts only** (crowd/loitering/fire + a dedicated LPR card with plate crop) — no raw per-frame Frigate rows. **Pop-up alert toasts were REMOVED (2026-07-01)** — they overlapped the events panel and had no dismiss; the events panel is the single alert surface. Stateless (no SQLite).
- `docker-compose.yml` — services: `mqtt`; `frigate` (8g, GPU); `crowd_gpu` (12g, GPU); `locate_gpu` (16g, GPU, `benchmark` profile); `fire_gpu` (6g, GPU); `lpr_gpu` (6g, GPU, YOLO11 detector on CUDA + `cct-xs` OCR on CPU); `ai_worker` (512m, no GPU, legacy inactive); `perception_worker` (2g, no GPU, realtime RTSP lane, default-on); `dashboard` (512m, no GPU, `:8080`); `stream_core` (512m, `realtime-benchmark` profile, optional metadata service). Frigate detection stays CPU; heavy models on the GB10.
- `storage/` — Frigate output (mounted, retains ~60s to prevent bloat). `videos/` — mock mp4 sources. `mosquitto/` — broker config/log (persistence disabled). `docs/` — human reference (do NOT auto-read, §5).
- `ROADMAP.md` — **benchmark milestone register (M0–M4):** cleanup/context alignment, stable Frigate baseline, realtime benchmark path, dashboard result history, new model candidates per task.
- `SYSTEM.md` — detailed technical reference (the "deep" doc): end-to-end data flow, the crowd-clustering formula, exact MQTT/WebSocket/`/detect`/`/benchmark` schemas, dashboard routes & proxy rules, port map. Does NOT duplicate this file/`config.py`/`ROADMAP.md`. Manual-update; edit when logic/schemas/wiring change.

## 4. Current status (as of 2026-07-01)
- ✅ **Realtime baseline running:** `mqtt`, `frigate` (healthy, CPU OpenVINO), `crowd_gpu`, `fire_gpu`, `lpr_gpu`, `perception_worker`, `dashboard` (`:8080`) up. `ai_worker` up but inactive (`ACTIVE_PROBLEM=`). `locate_gpu`/`stream_core` on-demand under profiles.
- ✅ **Crowd + Loitering + Fire/Smoke + LPR all run through `perception_worker`** (RTSP realtime lane). `PERCEPTION_CAMERAS=cam1_VIRAT_1,cam_loiter,cam_fire,cam_lpr`; `PERCEPTION_PERSON_CAMERAS=cam1_VIRAT_1,cam_loiter`; `PERCEPTION_FIRE_CAMERAS=cam_fire`; `PERCEPTION_LPR_CAMERAS=cam_lpr`.
  - **Crowd:** person frames → `crowd_gpu`; clusters via `smart_city_common.compute_crowd_clusters`; publishes `perception/crowd/<cam>` (every frame) + debounced `perception/alerts/crowd`.
  - **Loitering:** ByteTrack in `perception_worker`; alert when a track's age ≥ `LOITERING_DWELL_SECONDS` (40s), `dwell_time` field; cleared when the track is lost > `PERCEPTION_TRACK_LOST_SECONDS`.
  - **Fire/Smoke:** `cam_fire` → `fire_gpu`; N-of-M debounce (`FIRE_PERSIST_N/M`, `FIRE_CONFIDENCE`); publishes `perception/fire_smoke/<cam>` + `perception/alerts/fire_smoke` (with `active:false` clear).
  - **LPR:** `cam_lpr` (`LPR_FPS=3`) → `lpr_gpu` (YOLO11 GPU + cct-xs OCR CPU); publishes `perception/lpr/<cam>` + `perception/alerts/lpr` (stable-read OR confident-single-read gate; alert carries base64 `plate_crop`). Dashboard has a dedicated LPR event card. Known limit: OCR on small/far plates is noisy (raise `LPR_DETECTOR_IMGSZ` or add plate-tracking to improve).
- ✅ **Frigate tuned:** `cpus=14` (trần CPU 1400%), `detect.fps` = 5 on `cam1_VIRAT_1`/`cam_fire`, 8 on `cam_loiter`, 2 on `cam_lpr`. `cam_loiter` motion tune: `motion.threshold=25`, `motion.contour_area=5`, `detect.stationary.interval=5` (catch small/distant + freshen stationary box). Detection stays CPU (OpenVINO SSDLite); heavy AI on GB10.
- ✅ **Single-source go2rtc for all 4 active cams:** one `-c copy` looping producer per cam (~5% CPU/cam) → live + detect share it → refresh joins mid-loop instead of restarting at 0. Measured with all up: frigate CPU well under the 1400% ceiling, no skip/drop.
- ✅ **Dashboard:** disabled cams filtered; WebRTC + snapshot fallback; multi-model benchmark UI; crowd/loiter/fire/lpr SVG overlays on the live tile; events timeline = problem-alerts only; **pop-up toasts removed.**
- ✅ **LocateAnything-3B (`locate_gpu`):** loads FP16, `/detect` sane (~18–20s/frame); available in `/benchmark/run` compare (RF-DETR vs Locate). Benchmark clustering uses the SAME shared module; keep `CLUSTER_*` env in sync with the perception lane.
- ☐ **Remaining AI problems** (multi-camera Re-ID/tracking; Face Rec): not started — add as new `model_workers/<model>/` services + a `perception_worker` lane. See `ROADMAP.md`.

### Hard constraints (verified)
- **No GPU path inside prebuilt Frigate on GB10** (no sm_121 support; TensorRT detector removed in 0.16). Frigate detection stays CPU; heavy GPU AI runs in separate `model_workers/` services.
- **sm_121 JIT gotcha:** torch's bundled nvrtc 12.8 tops out at sm_120 → eager JIT crashes with `nvrtc: invalid value for --gpu-architecture`. Fix: `pip install nvidia-cuda-nvrtc-cu12==12.9.86` AFTER torch (`TORCH_CUDA_ARCH_LIST` does NOT fix it). Apply to every GPU container.
- **Native Frigate Face Rec / LPR / Semantic Search need AVX+AVX2 → impossible on ARM64.** Must run as a GPU worker service, not in Frigate.
- **Frigate 0.17 recording is tiered:** `record.continuous` + `record.alerts.retain` + `record.detections.retain`. The old `record.retain.{days,mode}` breaks.
- **Modal:** legacy alias only. Always use the local endpoint. No cloud deployment code.
- **Frigate native UI can't overlay external AI** on the live view → that is why the custom dashboard exists.
- `vllm` (neighbour, port 8000, ~98GB) is **not running** as of 2026-06-22; if it returns it is other users' — don't touch, recheck headroom.

### Integration contract (do NOT break)
- `perception_worker` ↔ Frigate/go2rtc: RTSP only via `rtsp://frigate:8554/<cam>` by default. Do not switch realtime AI back to `/api/<cam>/latest.jpg`; `PERCEPTION_ALLOW_HTTP_FETCH=false` is intentional.
- `perception_worker` ↔ model services: HTTP POST raw JPEG. Person `{person_count,detections[],model}` at `crowd_gpu:8000/detect`; fire `{detections[{...,class}],fire_count,smoke_count,model}` at `fire_gpu:8000/detect`; LPR `{plate_count,plates[],model}` at `lpr_gpu:8000/detect`.
- `perception_worker` → dashboard: MQTT `perception/objects/#`, `perception/tracks/#`, `perception/crowd/#`, `perception/fire_smoke/#`, `perception/lpr/#`, `perception/alerts/#`.
- Crowd clustering lives ONLY in `smart_city_common.clustering`. Do not re-fork it into `ai_worker`/dashboard; import the shared function.
- `ai_worker` ↔ Frigate: legacy fallback only; keep inactive unless explicitly re-enabled. Fire/smoke/LPR must stay out of `ai_worker`.

## 5. Exclude — do NOT read (waste tokens / not source)
- Dirs: `.venv/`, `docs/`, `mosquitto/`, `storage/`, `videos/`, `model_cache/` (HF weights), `dashboard/frontend/node_modules/`, `dashboard/frontend/dist/`, any `__pycache__/`.
- Media: `*.mp4`, `*.webp`, `*.pt`, `*.onnx`.
- Human-only docs: everything under `docs/`.

## 6. Working rules
- Respond to the user in **Vietnamese**; write code in **English**.
- **No comments** in code/config. Exceptions: `# noqa`; the root `config.py`; `docs/`.
- Concise by default; full detail when correctness needs it.
- Only read named/needed files — don't browse. Large searches → subagent.
- Never sudo / install system-wide / act outside the workspace.
- Check `free -h` / `nvidia-smi` before heavy or long-running commands.
- **Config/hyperparameter centralization:** every legacy `ai_worker` hyperparameter lives in root `config.py`. Parameters that physically live elsewhere (frontend TS constants, docker-compose env, `config.yml`, model servers) MUST at minimum be comment-declared in the `config.py` header block (where they are + how to change + the apply command).

## 7. Benchmark Rules & System Requirements
This repository is a **benchmark harness** simulating a minimal production environment for AI object detection and tracking.

### Benchmark Modes
1. **Frigate-triggered (Baseline):** Frigate handles recording, stream consuming, CPU motion-detection triggering (legacy `ai_worker` path).
2. **Realtime-stream (Primary):** `perception_worker` feeds streams into the GPU detectors at constant FPS, independent of Frigate motion detection.
3. **Direct model endpoint:** REST tests against `model_workers` with static datasets (`/benchmark/run`).

### Mandatory Metrics
- **Accuracy:** proxy precision/recall, false alerts/hour, missed events.
- **Speed:** inference latency (p50/p95), end-to-end latency, FPS.
- **Cost:** GPU memory, CPU %, RAM, container count, est. local/cloud cost.
- **System Requirements:** must run within ARM64 / GB10 (unified memory) / strict `mem_limit`.

### Non-Goals
- Full production NATS/Redis backbone.
- Authentication, Authorization, TLS.
- Complete MediaMTX recorder replacement.
- Triton/DeepStream integration (unless scale requires).
- Operator workflows / complex acknowledgment flows beyond a minimal UI demo.
