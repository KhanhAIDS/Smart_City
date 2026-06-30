# Smart_City Benchmark Plan

This repository serves as a **benchmark harness** simulating a minimal production environment for AI object detection and tracking. 

## Benchmark Modes
1. **Frigate-triggered mode (Baseline)**: The primary stable stream/trigger path where Frigate handles recording, stream consuming, and CPU motion-detection triggering.
2. **Realtime-stream mode (Optional/Experimental)**: A continuous analysis pipeline (`stream_core` + `perception_worker`) where streams are fed directly into the GPU detector at a constant FPS, independent of Frigate's motion detection.
3. **Direct model endpoint mode**: Running direct REST API tests against `model_workers` using static datasets.

## Mandatory Metrics
Any new model or pipeline iteration must be evaluated against the following criteria:
- **Accuracy**: Proxy for precision/recall, false alerts per hour, missed events.
- **Speed**: Inference latency (p50/p95), end-to-end latency, frames per second (FPS).
- **Cost**: GPU memory footprint, CPU utilization, RAM usage, container count, and estimated local/cloud cost.
- **System Requirements**: Must run within the constraints of ARM64, NVIDIA GB10 architecture (unified memory), and strict `mem_limit` constraints.

## Non-Goals
The following are explicitly out of scope for this repository, as it is focused solely on benchmarking AI models:
- Full production NATS/Redis backbone.
- Authentication, Authorization, and TLS.
- Complete MediaMTX recorder replacement.
- Triton/DeepStream integration (unless benchmarking at scale requires it).
- Operator workflows or complex acknowledgment flows beyond a minimal UI demo.
