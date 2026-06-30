# Từ điển lệnh — Smart_City (Frigate + AI worker + GPU local + Dashboard)

> **Server: 192.168.3.252** &nbsp;|&nbsp; Thư mục dự án: `/home/tts/AI/Luyen-Minh-Khanh/Smart_City`
> Mọi lệnh `docker compose` phải chạy TỪ trong thư mục dự án.

## 0. Hệ thống gồm những container nào

| Container | Vai trò | Truy cập |
|-----------|---------|----------|
| `mqtt` | Broker MQTT (cầu nối sự kiện, cấu hình in-memory không persistence) | nội bộ `:1883` |
| `frigate` | NVR + WebRTC + phát hiện vật thể cơ bản (CPU) | **http://192.168.3.252:5000** |
| `perception_worker` | **(Chính)** Đọc RTSP realtime từ Frigate, đẩy frame tới các model GPU | nội bộ `:8093` |
| `crowd_gpu` | Model RF-DETR-Large trên **GPU GB10** — đếm người/đám đông | nội bộ `:8000` |
| `fire_gpu` | Model YOLOv8 trên **GPU GB10** — phát hiện cháy/khói | nội bộ `:8000` |
| `locate_gpu` | Model LocateAnything-3B trên GPU — model thứ 2 chỉ dùng khi benchmark | nội bộ `:8000` |
| `ai_worker` | **(Legacy)** Cầu nối cũ nghe Frigate events (mặc định đang tắt `ACTIVE_PROBLEM=`) | nội bộ |
| `stream_core` | **(Tùy chọn)** Hỗ trợ metadata khung hình cho luồng benchmark | nội bộ `:8092` |
| `dashboard` | Giao diện điều khiển tùy biến (live stream + cảnh báo + benchmark, stateless) | **http://192.168.3.252:8080** |

```bash
cd /home/tts/AI/Luyen-Minh-Khanh/Smart_City
```

## 1. Khởi động / Dừng hệ thống

```bash
docker compose up -d        # Bật tất cả (chạy ngầm)
docker compose ps           # Xem trạng thái các container đang chạy
docker compose stop         # Dừng tất cả
docker compose down         # Gỡ container và mạng (giữ nguyên code/cấu hình)
docker compose start <service_name>   # Bật 1 container cụ thể (vd: start locate_gpu)
docker compose stop <service_name>    # Tắt 1 container cụ thể
docker compose restart <service_name> # Khởi động lại 1 container
docker compose up -d --build                 # Build lại toàn bộ các service có thay đổi code
docker compose up -d --build <service_name>  # Build lại và cập nhật thay đổi cho 1 service
```

## 2. Đổi gì thì chạy lệnh nào? (QUAN TRỌNG — tránh build lại thừa)

> Quy tắc: **đổi tham số/config → chỉ restart (mất vài giây)**; **đổi code → build lại nhưng nhanh nhờ cache**.

| Bạn thay đổi | Lệnh cần chạy | Build lại? |
|--------------|---------------|------------|
| `config.yml` của Frigate (camera, `detect.fps`, record...) | `docker compose restart frigate` | **Không** |
| Thêm/xóa/đổi video `.mp4` trong `videos/` | `docker compose restart frigate` | **Không** |
| `config.py` (biến fallback cho ai_worker, đã mount) | `docker compose restart ai_worker` | **Không** |
| Biến `environment` / `mem_limit` trong `docker-compose.yml` | `docker compose up -d <service>` | **Không** |
| Code trong `perception_worker/` | `docker compose up -d --build perception_worker` | Có (nhanh) |
| Code trong `model_workers/<model>/` | `docker compose up -d --build <service>` | Có (nhanh) |
| Đổi model lớn, sửa `requirements.txt`, Dockerfile | `docker compose up -d --build <service>` | Có (LÂU — tải lại) |
| Code/giao diện UI trong `dashboard/` | `docker compose up -d --build dashboard` | Có |

## 3. Quản lý mã nguồn (Git & GitHub Operations)

Vì thư mục dự án cần được sao lưu hoặc chia sẻ, dưới đây là các lệnh chuẩn để quản lý Git và đồng bộ lên Github, đã bỏ qua các file rác nhờ `.gitignore`.

```bash
# Khởi tạo và kết nối (Chỉ làm 1 lần ban đầu)
git init                                   # Khởi tạo kho lưu trữ Git cục bộ
git config --global user.name "Tên Bạn"    # Cài đặt tên (bắt buộc trước khi commit)
git config --global user.email "Email Bạn" # Cài đặt email (bắt buộc trước khi commit)
git branch -M main # Sửa tên nhánh chính thành main (chuẩn mới của Github)
git remote add origin <URL_của_Github>     # Liên kết với kho chứa trên Github (VD: https://github.com/user/repo.git)

# Quy trình lưu code (Thường xuyên sử dụng)
git status                                 # Kiểm tra các file đã bị thay đổi
git add .                                  # Đưa TẤT CẢ các thay đổi hợp lệ (đã lọc qua .gitignore) vào danh sách chờ
git commit -m "Nội dung thay đổi của bạn"  # Đóng gói thay đổi với thông điệp ghi chú
git push -u origin main                    # Đẩy code lên Github (cần xác thực token/SSH)
git pull origin main                       # Kéo code mới nhất từ Github về máy

# Xử lý khi lỡ commit nhầm file rác/file to quá 25MB
git rm -r --cached .                       # Xóa bộ nhớ cache của Git (không xóa file thật)
git add .                                  # Add lại từ đầu (sẽ tuân thủ chặt .gitignore mới)
git commit -m "Fix gitignore and remove large files"
```

## 4. Giao diện Web

- Frigate (NVR gốc + Cấu hình): **http://192.168.3.252:5000**
- Dashboard tùy biến (Live + Cảnh báo AI realtime + Benchmark): **http://192.168.3.252:8080**

## 5. Xem Logs (Gỡ lỗi)

```bash
docker compose logs -f frigate                 # log Frigate (theo dõi liên tục)
docker compose logs -f perception_worker       # log luồng đọc RTSP realtime
docker compose logs -f ai_worker               # log cầu nối AI cũ
docker compose logs -f crowd_gpu               # log inference đếm người
docker compose logs -f fire_gpu                # log inference lửa/khói
docker compose logs -f dashboard               # log backend/frontend dashboard
docker compose logs --tail=100 -f <service>    # Xem 100 dòng log gần nhất rồi theo dõi tiếp
```

## 6. Vào bên trong container

```bash
docker exec -it frigate /bin/bash
docker exec -it crowd_gpu /bin/bash
docker exec -it perception_worker /bin/bash
```

## 7. Kiểm tra GPU (Nvidia GB10)

```bash
nvidia-smi                              # Tình trạng GPU/RAM/Tiến trình trên máy host
docker exec -it crowd_gpu nvidia-smi    # Xác nhận GPU đã passthrough thành công vào container
nvtop                                   # Theo dõi GPU thời gian thực (giống htop nhưng cho GPU)
```

## 8. Theo dõi tài nguyên (KIỂM TRA TRƯỚC KHI CHẠY NẶNG)

> Do sử dụng server công ty dùng chung, LUÔN đảm bảo RAM không bị tràn (OOM).

```bash
free -h                  # Xem RAM còn trống (nhìn cột 'available')
docker stats             # Xem CPU/RAM realtime của từng container đang chạy
docker stats --no-stream # Chụp snapshot 1 lần trạng thái CPU/RAM rồi thoát
htop                     # Hoặc `btop` để xem tài nguyên CPU của host
```

## 9. Kiểm thử nhanh luồng AI (MQTT)

MQTT là trái tim truyền tín hiệu của toàn bộ hệ thống. Dùng các lệnh sau để bắt gói tin đang bay:

```bash
# Xem CẢNH BÁO tổng hợp từ các model realtime (fire_smoke, crowd, loitering...)
docker exec mqtt mosquitto_sub -h localhost -t 'perception/alerts/#' -v

# Xem RAW FRAME BOUNDING BOX (tọa độ vật thể thô phát ra mỗi frame)
docker exec mqtt mosquitto_sub -h localhost -t 'perception/fire_smoke/#' -v
docker exec mqtt mosquitto_sub -h localhost -t 'perception/crowd/#' -v

# Xem event gốc từ Frigate (dành cho luồng ai_worker cũ)
docker exec mqtt mosquitto_sub -h localhost -t 'frigate/events' -v
```