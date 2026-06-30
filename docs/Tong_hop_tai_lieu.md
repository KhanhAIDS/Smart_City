# TỔNG HỢP KIẾN TRÚC & LỘ TRÌNH HỆ THỐNG AI CAMERA (CẤP PHƯỜNG/XÃ)

## 1. NỀN TẢNG HỆ THỐNG LÕI (FRIGATE NVR)
### 1.1. Kiến trúc tổng thể
- **Hybrid Edge-Server:** Mini PC ở phường xử lý, gửi siêu dữ liệu (MQTT) về Quận. Có cơ chế *Store-and-Forward* chống đứt mạng.
- **Cơ chế Phân luồng (Bifurcated):**
  - *Luồng phụ (Detect):* Độ phân giải thấp (360p/720p@5fps) -> Lọc chuyển động CPU -> Đẩy sang GPU/TPU (Hailo-8, OpenVINO, TensorRT) phân tích.
  - *Luồng chính (Record):* Độ phân giải cao (4K), ghi trực tiếp vào ổ cứng không qua giải mã.
  - *Xem trực tiếp:* Dùng WebRTC/MSE (`go2rtc`) để giảm tải luồng.
- **Đa luồng & Tối ưu Biên (Zero-copy IPC):** 
  - Tách tiến trình bằng `multiprocessing.shared_memory` (trên `/dev/shm`) để giao tiếp dữ liệu siêu tốc (<1ms) thay vì Queue.
  - Khuyến nghị dùng **GStreamer (DMA-BUF)** truyền hình ảnh trực tiếp từ Hardware Decoder sang GPU/NPU (giảm 50-70% tải CPU).

### 1.2. Thách thức cần giải quyết
- **Kỹ thuật & Mở rộng:** Dùng HWAccel giảm 60% tải CPU. Quy mô 100+ cam cần VLAN 10Gbps, VRAM lớn, hệ thống lưu trữ ZFS RAID.
- **Độ trễ & Đồng bộ:** Chỉnh I-Frame = FPS, ưu tiên giao thức TCP. Dùng NTP/PTP đồng bộ thời gian liên camera.
- **Bảo mật biên:** Tự động làm mờ mặt, ẩn danh bằng khung xương (Skeletonization) hoặc mặt nạ mã hóa (ReCAM).

## 2. 5 BÀI TOÁN AI & NGHIỆP VỤ (VIDEO ANALYTICS)
### 2.1. Phát hiện & Theo dõi liên camera (Multi-Camera Tracking & Re-ID)
- **Nhiệm vụ:** Giữ ID người/xe xuyên suốt các camera, xử lý che khuất (occlusion) và chênh lệch môi trường.
- **Phát hiện (Detection):** YOLOv9 (cơ chế PGI) hoặc **YOLO26** (End-to-End NMS-Free, tối ưu độ trễ cho Edge).
- **Theo dõi Đơn camera (MOT):** **ByteTrack** (liên kết 2 pha, "giải cứu" đối tượng, cực nhanh) hoặc DeepSORT.
- **Định danh lại (Re-ID):** Framework **FastReID** (đặc trưng cục bộ, chưng cất tri thức) hoặc **OpenUnReID** (học không giám sát).
- **Liên kết Toàn cục (MTMC) & Nâng cao:**
  - *Ràng buộc Không gian-Thời gian:* Camera Topology (Ma trận chuyển tiếp, Cửa sổ thời gian di chuyển).
  - *Định vị 3D:* Chiếu tọa độ Bird's-Eye View (BEV) và tinh chỉnh hình học (Late Multi-view Aggregation).
  - *Gán ID toàn cục:* Dùng **Graph Neural Networks (GNN)** kết hợp Multicut/Hungarian trên cửa sổ thời gian trượt.
  - *Trường hợp biên:* Xử lý che khuất bằng Transformer (OART) hoặc kết hợp đa chế độ (Camera Nhiệt + RGB).
- **Đánh giá:** Dịch chuyển sang chỉ số **HOTA** thay vì chỉ dùng IDF1.

### 2.2. Nhận diện khuôn mặt (Face Recognition)
- **Nhiệm vụ:** Nhận diện trong môi trường mở (In the wild) với bài toán Open-Set (nhận diện 1:N và chặn người lạ).
- **Pipeline tiêu chuẩn (InsightFace-REST):**
  - *Phát hiện & Căn chỉnh (Detection & Alignment):* Dùng RetinaFace/SCRFD để localize và xoay chuẩn khuôn mặt dựa trên 5 điểm landmarks (112x112 pixel).
  - *Đánh giá chất lượng (FIQA):* Lọc khung hình mờ nhòe bằng thuật toán Heuristic (Laplacian Variance) hoặc Learning-based (SER-FIQ) trước khi đưa vào mạng.
  - *Trích xuất đặc trưng (Embedding):*
    - **Edge/Mobile:** MobileFaceNet, EdgeFace (kiến trúc lai CNN-Transformer, tối ưu cả hiệu năng lẫn độ chính xác).
    - **Server/GPU:** ArcFace / InsightFace (chuẩn công nghiệp, dùng IResNet-100 với hàm mất mát Angular Margin Loss, trích xuất vector 512 chiều).
    - **Môi trường khắc nghiệt:** AdaFace (tự động điều chỉnh chất lượng nhận diện dựa trên độ mờ của ảnh, cực kỳ phù hợp cho camera an ninh).
  - *Tìm kiếm & Đối chiếu (Matching):* Sử dụng Vector Database tối ưu xấp xỉ lân cận gần nhất (ANN). Dùng FAISS cho tốc độ cao, hoặc Milvus/Qdrant cho hệ thống lớn cần sharding và lọc theo metadata (như khu vực camera).
- **Nâng cao:** Tăng tốc suy luận bằng TensorRT, xử lý lô (Batching), tích hợp Liveness Detection chống giả mạo, thiết lập ngưỡng chặn (Rejection Logic kép) để tránh nhận diện nhầm người lạ.

### 2.3. Cảnh báo Hành vi & Đám đông (Anomaly Detection)
- **Nhiệm vụ:** Phát hiện ẩu đả, đếm đám đông, phân loại thuộc tính (PAR) và cảnh báo dựa trên ngữ cảnh (VLM).
- **Phân luồng Công nghệ:**
  - *Phát hiện Hành vi (Violence/Fall):* Chuyển dịch từ phân tích pixel sang **Skeleton-based Action Recognition (SAR)**. Khai thác **YOLOv8-pose** trích xuất chuỗi khung xương và phân loại bằng **ST-GCN** tối ưu qua Knowledge Distillation cho Edge.
  - *Phân tích Đám đông:* Áp dụng song song **ByteTrack + Polygon/Line Zones** cho đám đông thưa và mạng **CSRNet (Density Map)** cho đám đông cực đặc nhằm xử lý che khuất nặng.
  - *Phân loại Thuộc tính (PAR):* Dùng pipeline 2-stage (YOLO cắt vùng -> ResNet phân loại: màu đồng phục, mũ bảo hiểm).
- **Nâng cao & Lọc nhiễu:**
  - *Lọc Cảnh báo Giả:* Ứng dụng Debounce logic (Sustained/Spike Alerts) tích hợp qua bản tin MQTT.
  - *Zero-shot VLM:* Triển khai kiến trúc **Event-Driven VLM**, dùng YOLO làm "Trigger" xuất frame/clip sự kiện cho VLM cục bộ (Qwen-VL, LLaVA) phân tích thay vì stream liên tục.

### 2.4. Giao thông & Nhận diện biển số (LPR/ANPR)
- **Nhiệm vụ:** Phạt nguội, phát hiện đi ngược chiều, lấn chiếm vỉa hè/đỗ xe trái phép.
- **Phát hiện Biển số (Detection):** Dùng mạng có cơ chế Attention (**YOLOv12**) kết hợp cắt lát ảnh **SAHI** để bắt biển số nhỏ trên luồng 4K.
- **Nhận diện Ký tự (OCR):** Chuyển từ CRNN sang kiến trúc **Mamba-SSM (CMN)** giúp chống nhiễu mờ và chói sáng. Xử lý biển 2 dòng bằng giải pháp tách dòng (Row Merging) hoặc nhận diện từng ký tự (Character Sequencing).
- **Phân tích Vi phạm (Spatial-Temporal):**
  - *Đi ngược chiều/sai luồng:* Dùng **ByteTrack** theo dõi quỹ đạo, áp dụng **Vector Cross Product** so sánh hướng di chuyển với hướng làn đường.
  - *Đỗ xe sai quy định:* Ứng dụng thuật toán **Ray Casting (Point-in-Polygon)** xác định xe trong vùng cấm, kết hợp bộ đếm thời gian (Timer).
- **Nâng cao & Triển khai:**
  - Tối ưu phần cứng Camera (ISP): Đẩy Shutter speed > 1/1000s, dùng kính lọc hồng ngoại (IR-pass).
  - Triển khai bằng pipeline **NVIDIA DeepStream (TensorRT INT8)** trên thiết bị biên.
  - Sẵn sàng tích hợp nhận diện mã QR trên biển số mới theo quy chuẩn 2025 (QCVN 08:2024/BCA).

### 2.5. Trật tự đô thị & Môi trường
- **Nhiệm vụ:** Xử lý đổ rác trộm, lấn chiếm vỉa hè/lòng đường, cảnh báo khói/lửa thực địa.
- **Phân luồng Công nghệ:**
  - *Đổ rác trộm:* Dùng **Máy trạng thái (PFSM)** nối chuỗi hành vi (Dừng -> Bỏ vật -> Rời đi) kết hợp logic kiên trì tạm thời. Khuyến nghị dùng Cảm biến Nhiệt (Thermal) cho ban đêm.
  - *Lấn chiếm vỉa hè:* Dùng **Phân đoạn ngữ nghĩa (U-Net/SegFormer)** trích xuất ranh giới. Áp dụng logic Point-in-Polygon và bộ đếm thời gian (N-frame Debouncing) để lọc dừng đỗ tạm thời.
  - *Khói/Lửa:* Chuyển từ phân tích ảnh tĩnh sang **Spatio-temporal (Optical Flow)**. Áp dụng xác thực đa tầng (màu sắc, tần số Wavelet) để tối ưu hóa Tỷ lệ Báo giả (FPR).
- **Tối ưu & Data Flywheel:** Thiết lập chu trình tự học (Active Learning). Tự động thu thập mẫu khó (Hard-negatives: đèn xe, sương mù), gán nhãn bằng VLM và tinh chỉnh lại mô hình (LoRA) để đưa xuống Edge.

## 3. LỘ TRÌNH TRIỂN KHAI THỰC CHIẾN TỪ THÍ ĐIỂM ĐẾN MỞ RỘNG
Dựa trên kiến trúc lõi (Phần 1) và 5 nhóm nghiệp vụ AI (Phần 2), lộ trình được thiết kế nhằm vượt rào cản phần cứng biên, đảm bảo hệ thống chạy mượt mà và tự động tối ưu hóa theo thời gian.

### Giai đoạn 1: Dựng Base Pipeline & Thử nghiệm (PoC)
- **Cài đặt & Phân luồng:** Triển khai Frigate NVR (Docker). Giải mã sub-stream (720p@5fps) cho AI, luồng 4K ghi trực tiếp vào đĩa.
- **Mô phỏng (Mocking):** Dùng FFmpeg/MediaMTX đẩy luồng RTSP giả lập từ video thực địa (đêm, mưa, che khuất) để test tải.
- **Giao tiếp Zero-copy IPC:** Dùng `shared_memory` trên `/dev/shm` như một "băng chuyền" dùng chung, các trạm (AI, Record, Display) đọc khung hình trực tiếp mà không cần copy, giảm tải CPU tức thì.

### Giai đoạn 2: Tối ưu phần cứng Biên (Edge Acceleration)
- **Offload giải mã:** Kích hoạt HWAccel chuyển việc giải mã sang iGPU (Intel OpenVINO) hoặc GPU rời.
- **Tối ưu Mô hình:** Lượng tử hóa (INT8/FP16) YOLO26, ByteTrack, EdgeFace xuất ra TensorRT/Hailo-8/Coral. Tùy chọn chạy DeepStream (`Gst-nvinfer`) để batching nhiều luồng.
- **Kiểm chứng:** Theo dõi "Skipped FPS" trên Frigate. Nếu > 0 (quá tải), cần giảm độ phân giải Detect xuống 360p hoặc giới hạn vùng ROI.

### Giai đoạn 3: Lắp ráp 5 Nghiệp vụ AI & Lọc báo giả
- **Định danh & Theo dõi (MTMC + Face):** Cắm module FastReID và Vector DB (FAISS/Qdrant) vào pipeline. Test khả năng giữ ID liên camera.
- **Giao thông & LPR:** Dùng YOLOv12 + SAHI + Mamba-SSM để đọc biển số bị chói/mờ. Áp dụng Ray Casting (đỗ trái phép) và Vector Cross Product (ngược chiều).
- **Trật tự & Cháy nổ:** Dùng Optical Flow cho khói/lửa.
- **Lọc nhiễu (Máy trạng thái - PFSM):** Ví dụ lọc đổ rác trộm như "trạm kiểm lâm 3 bước": (1) Xe dừng -> (2) Bỏ bọc đen -> (3) Xe đi, bọc đen còn. Thiếu 1 bước sẽ không báo động, giúp loại bỏ báo giả.

### Giai đoạn 4: Phân tán & Tích hợp Trung tâm (Hybrid Edge-Server)
- **Liên lạc MQTT (Store-and-Forward):** Gửi metadata từ Phường lên Quận. Hoạt động như "bưu tá thông minh": lưu tạm queue khi rớt mạng và tự động "bơm" lại khi có mạng.
- **Dashboard & Event-Driven VLM:** UI hiển thị Heatmap. Tránh stream liên tục làm sập RAM bằng cách set up Trigger: khi có sự kiện (vd: ẩu đả), Frigate cắt clip 5s gửi cho VLM cục bộ (LLaVA/Qwen-VL) xác nhận.

### Giai đoạn 5: Vận hành tự động & Data Flywheel
- **Bảo trì:** Tự động vá lỗi `resource_tracker` của Python chống rò rỉ RAM khi chạy liên tục. Lên lịch reboot camera định kỳ.
- **Vòng lặp Dữ liệu (Data Flywheel):** Hệ thống như "bộ máy tiêu hóa", bóc tách ca "báo giả" (sương mù -> khói) -> gán nhãn bằng VLM -> Fine-tune (LoRA) -> đẩy bản cập nhật xuống Edge. Giúp AI chính xác hơn mỗi ngày.
