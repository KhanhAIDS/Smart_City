Nếu detect.fps quá cao so với CPU → Frigate queue/skip frame → event trễ, dồn cục.

Chiến lược scale (ROADMAP): detect trên substream 360/720p + record mainstream. Về câu hỏi "frame gửi model có nên scale theo fps hiển thị?" — không cần bằng: live là full-fps qua go2rtc, detect.fps chỉ cần đủ dày để kết quả AI hiển thị kịp (8fps là hợp lý). Frame xử lý mà không hiển thị thì đúng là vô nghĩa, nên detect.fps nên ≥ tần suất bạn muốn thấy cập nhật, không cần hơn.

"3 crowd just now" là frame nào? — Là thời điểm PUBLISH alert, KHÔNG phải frame hiện tại.

Live freeze (go2rtc) chỉ ảnh hưởng hiển thị, KHÔNG ảnh hưởng detect. 

Khựng hình = frigate bão hòa CPU






Frigate (decode CPU + motion + OpenVINO/SSDLite detect person @5fps)
   → MQTT frigate/events (type=new|update|end)
       → ai_worker: lọc → GET 1 snapshot.jpg → nén → POST 1 JPEG tới crowd_gpu /detect
                    → nhận detections → cụm → publish 1 alert

| Thuộc tính | Bằng chứng trong code | Hệ quả |
| --- | --- | --- |
| **Single-frame** | `/detect` nhận 1 JPEG (`request.body()`); Trả box tĩnh; `handle_crowd` fetch 1 snapshot | Không có thông tin thời gian/chuyển động |
| **Event-driven (pull)** | `on_message` kích hoạt bởi `frigate/events` + `label=="person"` + `type=="new"` | AI chỉ thức dậy khi Frigate phát hiện object COCO |
| **Bị tiết lưu** | `COOLDOWN_SECONDS=5` per-camera; `detect.fps=5`; HTTP round-trip snapshot | Không "liên tục", không "tức thời" |

Kiến trúc này CÓ phù hợp cho ẩu đả / trộm / quấy rối / khói-lửa không?
Không — và lý do mang tính cấu trúc, không phải tinh chỉnh được:

Hành vi = chuyển động theo thời gian, không phải 1 khung. Một cú đấm, một cú giật đồ, một cú ngã, khói lan ra — định nghĩa bằng chuỗi frame, không thể nhận từ 1 ảnh tĩnh. Model hiện tại (/detect 1 JPEG → box) về nguyên lý không bắt được.

Trigger sai bản chất. ai_worker chỉ thức khi Frigate phát type=new cho một object COCO. Hệ quả chí mạng:

Khói/lửa KHÔNG phải class COCO → SSDLite không bao giờ phát hiện → không bao giờ có event → AI mù hoàn toàn. Đây là ví dụ sắc nhất: không có cách nào "vá" lên Frigate hiện tại.
Ẩu đả/quấy rối giữa 2 người đã được track → không sinh object new → không trigger (hoặc chỉ cho 1 snapshot ở 1 thời điểm bất kỳ).
Trộm/giật diễn ra trong <1s → snapshot lệch pha là trượt.
Trễ + tiết lưu. Cooldown 5s + detect 5fps + pull snapshot ⇒ không đáp ứng "scan liên tục, phản ứng tức thời".

Làm rõ "Frigate chậm": chỗ chậm là CPU detector OpenVINO @5fps — đó là giới hạn của detector

Mismatch nằm ở một component duy nhất (nạp/trigger).

| Tái dùng (giữ) | Làm mới |
| --- | --- |
| MQTT bus + hợp đồng alert | Service streaming-analytics mới đọc stream liên tục |
| Dashboard (WS, grid, timeline, toast); thêm 1 tab/alert type | NVDEC decode + ring buffer frame trên GPU |
| Pattern `model_workers/` (Dockerfile, fix nvrtc sm_121, mem_limit, GPU) | Model thời gian (video-classifier, pose-sequence, flow) |
| Frigate (ingest, record, UI, snapshot-problems) | I/O contract mới (clip/window, không dùng 1 JPEG) |
| go2rtc RTSP restream `rtsp://...:8554/<cam>` (có sẵn) | Cổng motion-gate + debounce (chống FPR) |

Khi scale (vài chục+ camera, infer liên tục mọi luồng) → lúc đó dùng NVIDIA DeepStream + Triton (NVDEC zero-copy batched, TensorRT INT8, GStreamer). Kể cả khi đó vẫn giữ MQTT+dashboard.

**Kiến trúc tổng thể:** Dual-lane (2 luồng xử lý song song).
* **Nguồn phân phối chung:** Camera/RTSP nạp vào Frigate (đảm nhận Ingest, Record, UI, go2rtc restream).
* **Lane A (ĐÃ CÓ) - Event-driven Snapshot:**
* Đầu vào: Event snapshot từ Frigate.
* Pipeline: `Frigate` → `ai_worker` → `crowd_gpu` (`/detect`).
* Nhiệm vụ: Xử lý crowd, loitering (mở rộng: face, LPR, intrusion, abandoned).


* **Lane B (MỚI) - Continuous Stream Analyzer:**
* Thành phần: Thêm 1 container `stream_analyzer` (dùng GPU).
* Đầu vào: Luồng restream RTSP từ go2rtc (`rtsp://...:8554/<cam>`).
* Tiền xử lý: NVDEC decode luồng substream (360/256p) → Ring buffer (16–32 frame/cam).
* **Stage A (Motion-gate):** Lọc chuyển động/flow cơ bản, chạy always-on chi phí thấp.
* **Stage B (Temporal Model):** Chỉ kích hoạt khi qua Stage A (tránh ghim GPU 24/7).
* Ngã: Pose-sequence / bbox-aspect.
* Khói-lửa: Detector + optical-flow.
* Ẩu đả: Video-classifier (X3D / VideoMAE).
* Trộm/giật: Phân tích interaction.


* Hậu xử lý: Debounce theo K cửa sổ liên tiếp để chống nhiễu (FPR).
* Đầu ra: Publish MQTT topic `ai_worker/alerts/<behavior>`.


* **Đích đến (UI):** Dashboard nhận luồng MQTT tổng, tích hợp thêm tab/cột cho loại alert mới.

Bốn điểm thiết kế then chốt (chính là cái giải quyết "liên tục + tức thời"):

NVDEC decode (PyNvVideoCodec/DeepStream) → đẩy decode sang GPU, không đụng vào nút thắt CPU của Frigate. (Cần verify NVDEC trên GB10 sm_121; fallback = CPU decode 1 substream 256p — vẫn rẻ.)
Decode substream thấp (256–360p) → frame nhỏ = infer nhanh = "tức thời".
Motion-gate Stage A always-on → GPU chỉ chạy model nặng khi "có biến" ⇒ tận dụng GPU idle mà không pin 100%.
Debounce K cửa sổ → đây là vũ khí chống FPR (báo giả/giờ) mà plan.md nhấn mạnh là chỉ số quan trọng nhất.
Liên hệ plan.md: Lane B chính là tầng realtime Tier-1 của bạn, dựng dạng streaming. VLM Tier-3 vẫn hoãn. ai_worker snapshot vẫn lo các bài Tier-2 luật. → nhất quán với khung 3 tầng, chỉ đổi cách hiện thực tầng 1.

Tối ưu hybrid (tuỳ chọn): dùng person-box rẻ của Frigate (qua MQTT) làm gợi ý vùng → Lane B chỉ chạy model thời gian trên ROI có ≥2 người (ẩu đả) → giảm tải GPU thêm.

5. Khung plan chi tiết (theo pha, build dần — demo được sớm)
Pha	Nội dung	Mục tiêu / Demo	Train?	Rủi ro chính
0 — Spike decouple	1 service đọc go2rtc restream cam_loiter → NVDEC decode → ring buffer. Chưa có model. Log fps đạt được + CPU/GPU cost.	Chứng minh kéo được stream liên tục off-GB10 mà không hại Frigate/CPU. Đo headroom.	❌	NVDEC trên sm_121; nếu fail → fallback CPU 256p
1 — Gate + model đầu tiên	Stage-A motion-gate + Stage-B 1 hành vi (đề xuất ngã vì rẻ nhất: pose-seq hoặc bbox-aspect) → debounce → MQTT → tab dashboard.	Hành vi liên tục đầu tiên chạy end-to-end, demo bằng cách nằm xuống.	❌ (MVP)	Định nghĩa cửa sổ + ngưỡng debounce
2 — Khói/lửa	Detector fire/smoke + optical-flow/wavelet confirm + debounce.	Ca bắt buộc cần lane mới (khói không phải COCO) → minh hoạ rõ giá trị kiến trúc. Demo bằng máy khói/bật lửa.	◑ (model sẵn có)	Domain-gap ánh sáng/nền
3 — Ẩu đả / trộm / quấy rối	Video-classifier (RWF-2000 cho ẩu đả) và/hoặc interaction 2 người.	Lớp hành vi nặng nhất.	✅	Domain-gap + gán nhãn footage thật (việc của bạn)
4 — Hardening / scale	Batched đa-cam; cân nhắc DeepStream/Triton + TensorRT INT8 nếu số cam tăng; tinh chỉnh FPR trên footage thật.	Sản xuất hoá.	—	Tài nguyên server chung
Xuyên suốt: debounce/FPR · mở rộng hợp đồng MQTT (ai_worker/alerts/<behavior>) · 1 tab dashboard · quản trị tài nguyên (mem_limit, motion-gate, chạy giờ vắng — và bạn duyệt free -h/nvidia-smi trước khi bật lane nặng).



Ba researcher đã về (có nguồn, đã verify weights tồn tại thật). Đây là tổng hợp — và nó thay đổi đáng kể câu chuyện kiến trúc, theo hướng tốt hơn cho bạn.

1. Có model train sẵn không? — Phân tầng theo độ "sẵn sàng demo"
Bài toán	Phán quyết	Option tốt nhất (sẵn weights)	License	Modality
Khói/lửa	✅ Tải về demo ngay, KHÔNG train	AlimTleuliyev/wildfire-detection (YOLOv8, D-Fire fire+smoke) · alt Apache: TommyNgx/YOLOv10-Fire-Smoke	MIT / Apache	1 khung (YOLO)
Ngã	✅ Demo ngay qua pose+luật, KHÔNG train (bản temporal mạnh thì cần train nhẹ)	YOLO/RTMO-pose + luật tư thế (góc thân/đầu gần đất)	pose net AGPL → dùng RTMPose/RTMO hoặc MediaPipe = Apache	1 khung (pose) + rule
Ẩu đả	⚠️ Demo được trên cảnh dàn dựng, nhưng cần fine-tune (domain-gap tụt 20–30% trên CCTV thật)	Demo: TwoStreamSepConvLSTM (RWF-2000, MIT). Trần cao: train VideoMAE-L trên RWF-2000	MIT	Clip thời gian (16–32 frame)
Trộm/giật	⚠️ Research-grade — weights UCF-Crime có (RTFM/MGFN/AnomalyCLIP 90.67 mAUC, có class Robbery/Shoplifting) nhưng chạy trên feature I3D/CLIP, ra điểm bất thường, không phải "báo trộm" sạch	AnomalyCLIP	một số non-commercial	Feature + MIL
Quấy rối	❌ Chưa có gì off-the-shelf — không model, không benchmark public. Chỉ có prototype VLM-reasoning (research).	—	—	—
Backbone để fine-tune khi không có model sẵn (cho ẩu đả/trộm về sau): ít data nhất = skeleton + GCN (PoseConv3D/CTR-GCN, Apache, có pretrain NTU + skeleton trích sẵn) trên pose RTMO (Apache); RGB tốt nhất + license sạch = VideoMAE V2 (MIT, ViT-S/B). Tránh: MoViNet (chỉ TF), VideoMAE V1/TimeSformer (CC-BY-NC), YOLO-pose (AGPL) cho sản phẩm.

2. Phát hiện này thay đổi câu chuyện kiến trúc thế nào (điểm "bàn thêm")
Đây là chỗ đáng bàn nhất, và nó gỡ bỏ phần lớn lo lắng về việc phải làm project mới / Lane B nặng:

Hai bài demo-nhanh (khói/lửa, ngã) đều là inference 1-KHUNG, không phải video-clip thời gian.

Hệ quả kiến trúc:

Không cần NVDEC + ring buffer + clip 16–32 frame cho demo đầu. Cái duy nhất Frigate không làm được là trigger cho hiện tượng ngoài COCO (khói không phải object COCO → không bao giờ có event). Vậy thứ duy nhất phải thêm là một "frame pump" liên tục: đọc go2rtc restream (đã có rtsp://…:8554/<cam>) hoặc poll latest.jpg ở fps khiêm tốn (~3–5) → POST tới model.
Model service GIỮ NGUYÊN hợp đồng /detect 1 JPEG hiện tại — model_workers/fire_smoke/ chỉ là một service mới trả box fire/smoke, y hệt pattern crowd_gpu. Tái dùng 100%.
Single-frame YOLO infer <5ms → kể cả pump 5fps liên tục, GPU gần như rảnh. "Tức thời" thoả mãn; "phản ứng nhanh" = persistence N/M khung (vd 3/5 → báo trong ~1s), đây cũng chính là cơ chế chống FPR (sunlight giả lửa, hơi nước giả khói).
→ Lane B "nặng" (NVDEC + temporal clip + X3D/VideoMAE) chỉ thực sự cần cho ẩu đả/trộm — mà mấy bài đó dù sao cũng phải train trên footage của bạn. Nên độ phức tạp nặng được hoãn tự nhiên sang đúng pha cần training.

Tóm lại câu trả lời gốc của bạn:

Kiến trúc hiện tại có phù hợp? — Cho snapshot-class (crowd/loitering/face/LPR): có. Cho hành vi: cần thêm 1 lane, nhưng lane đó khởi đầu RẤT NHẸ (frame-pump + /detect 1 khung), không phải đại tu.
Frigate chậm có cần project mới? — Không. "Chậm" = CPU detector @5fps; lane mới bỏ qua detector đó, đọc thẳng stream, chạy model riêng trên GPU đang idle. Project mới/DeepStream để dành cho pha scale nhiều cam.
3. Khung plan tinh chỉnh (theo độ sẵn sàng)
Pha	Nội dung	Train?	Kiến trúc cần
0 — Frame-pump spike	1 component đọc go2rtc restream → grab ~3–5fps → log. Chứng minh pump liên tục không hại CPU Frigate.	❌	Nhẹ
1 — Khói/lửa	model_workers/fire_smoke (YOLO D-Fire, 1 khung) + pump + persistence + MQTT + tab dashboard. Ca bắt buộc cần lane mới (smoke ∉ COCO) → minh hoạ rõ giá trị.	❌	Nhẹ (1-khung)
2 — Ngã	pose (RTMO/MediaPipe, Apache) + luật tư thế + persistence.	❌	Nhẹ (1-khung)
3 — Ẩu đả	Lúc này mới dựng Lane B "nặng": NVDEC + clip buffer + VideoMAE/SepConvLSTM fine-tune trên RWF-2000 + footage của bạn.	✅	Nặng (temporal)
4 — Trộm	Weakly-supervised (AnomalyCLIP) + feature front-end. Research.	✅	Nặng
—	Quấy rối: bỏ (chưa có gì off-the-shelf).	—	—
Lưu ý license cho triển khai: ưu tiên MIT/Apache. YOLOv8/YOLO11 weights có thể MIT nhưng runtime ultralytics là AGPL-3.0 → demo/pilot nội bộ thì ổn, sản phẩm đóng cần cân nhắc (Enterprise license hoặc detector permissive). Tránh CC-BY-NC.

Đề xuất của tôi: bắt đầu bằng Khói/lửa — nó vừa "tải-về-demo-ngay không train", vừa là ca chứng minh rõ nhất vì sao cần lane mới (Frigate không bao giờ trigger được), vừa giá trị smart-city cao, và kiến trúc khởi đầu nhẹ (frame-pump + /detect 1 khung). Ngã làm ngay sau bằng cùng khuôn.

Bạn muốn chốt theo hướng này không, hay còn điểm nào muốn mổ xẻ thêm (vd: NVDEC trên GB10, cơ chế frame-pump, hay license)?