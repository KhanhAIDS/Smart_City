# Smart_City Benchmark Roadmap

## Milestones

### M0: Cleanup & Context Alignment (Current)
- Remove obsolete containers from the stack.
- Refactor project focus explicitly to a benchmark-first architecture.
- Document and establish Frigate and realtime benchmark modes.
- ✅ Cleanup obsolete documents, `.db` files (dashboard SQLite, mosquitto), and `storage` clips.

### M1: Stable Baseline Frigate Benchmark
- Ensure Frigate path performs optimally with CPU detection driving GPU inference.
- Verify end-to-end metrics on `rfdetr-large` and `fire_smoke` YOLO models.
- Set up automated latency logging and system requirement verifications.

### M2: Realtime Benchmark Path
- Maintain `stream_core` and `perception_worker` as an optional profiled setup (`--profile realtime-benchmark`).
- Collect performance and accuracy metrics on the continuous stream mode vs Frigate motion-triggered mode.
- Assess ID switch frequency and tracker stability.

### M3: Dashboard Benchmark Result Table/History
- Refactor the dashboard UI to clearly display benchmark session history.
- Ensure the `/benchmark/run` endpoint is capable of logging and surfacing comprehensive compare metrics.
- Disambiguate alert history from production audit logs.

### M4: Add New Model Candidates per Task
- Incorporate additional multi-modal or edge models into `model_workers`.
- Compare accuracy vs speed vs cost with existing baseline models.
- Expand benchmark coverage (e.g. LPR, Face Rec, Re-ID) when models become available.
