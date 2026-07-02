# traffic_violation_gpu

Dual model detector for traffic violations.

1. **Vehicle detection**: Uses YOLO11m by default to detect cars, trucks, buses, motorcycles.
2. **Helmet detection**: Uses a custom trained YOLO model for `helmet`, `no_helmet`, `rider`.

### Environment Variables
- `TRAFFIC_VEHICLE_MODEL`: Path to vehicle YOLO model. Default `yolo11m.pt`. Auto downloads if missing.
- `TRAFFIC_HELMET_MODEL`: Path to helmet YOLO model. Default `/opt/hf/helmet_yolo.pt`.
- `TRAFFIC_HELMET_REPO`: HuggingFace repo ID for helmet model (optional).
- `TRAFFIC_HELMET_FILE`: HuggingFace file name for helmet model (optional).
- `DETECTION_THRESHOLD`: Confidence threshold. Default `0.25`.

### Licenses
- `yolo11m`: AGPL-3.0 (acceptable for this local research/benchmark harness).
- Helmet model: Ensure the weights you provide are compatible with your use case.
