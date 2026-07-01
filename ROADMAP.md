# Smart_City Benchmark Roadmap

> Milestone register for the benchmark harness. Status as of **2026-07-01**.
> Deep technical detail lives in `SYSTEM.md`; stable context in `AGENTS.md`/`CLAUDE.md`.

## Milestones

### M0: Cleanup & Context Alignment — ✅ done
- ✅ Removed obsolete containers/documents, `.db` files (dashboard SQLite, mosquitto), stale `storage` clips.
- ✅ Refocused the project explicitly to a benchmark-first architecture.
- ✅ Documented Frigate + realtime benchmark modes.

### M1: Stable Baseline Frigate Benchmark — ✅ largely done
- ✅ Frigate path performs with CPU detection (OpenVINO SSDLite) driving GPU inference.
- ✅ End-to-end verified on `rfdetr-large` (crowd) and `fire_smoke` YOLOv8; `lpr` (YOLO11+cct-xs) added and verified.
- ☐ Automated latency logging / system-requirement verification still ad-hoc (per-service `/metrics` exists; no persisted history yet → M3).

### M2: Realtime Benchmark Path — ✅ now the default lane
- ✅ `perception_worker` is the primary realtime lane (crowd, loitering, fire/smoke, LPR); `stream_core` remains an optional `realtime-benchmark` profile.
- ☐ Collect performance/accuracy metrics: continuous-stream vs Frigate motion-triggered.
- ☐ Assess tracker (ByteTrack) ID-switch frequency and stability.

### M3: Dashboard Benchmark Result Table/History
- ☐ Surface benchmark session history in the UI (currently `/benchmark/run` is one-shot compare, not persisted).
- ☐ Ensure `/benchmark/run` can log comprehensive compare metrics.
- ☐ Disambiguate alert history from production audit logs.

### M4: Add New Model Candidates per Task
- ✅ Fire/smoke and LPR added as `model_workers` services with realtime lanes.
- ☐ Multi-camera Re-ID and Face Recognition (SCRFD→ArcFace→FAISS) — not started; add as new `model_workers/<model>/` + a `perception_worker` lane.
- ☐ Compare accuracy vs speed vs cost against existing baselines (LocateAnything-3B already wired into `/benchmark/run` as a second crowd backend).
