1. Frigate tracking & ý nghĩa của type == "new"
Đúng là Frigate CÓ tracking + gán ID, không phải "đánh dấu mỗi detect mới mỗi frame". Frigate chạy một object tracker (Norfair) cho từng camera: mỗi đối tượng được gán một ID duy nhất và giữ ID đó xuyên suốt các frame khi còn trong khung hình. Vòng đời 1 object phát ra 3 loại event trên frigate/events:

new — object mới bắt đầu vòng đời (vừa được tracker tạo ID). Phát đúng 1 lần.
update — object đang tồn tại có thay đổi (di chuyển, đổi score, vào/ra zone…). Phát nhiều lần.
end — object kết thúc (biến mất quá detect.max_disappeared, mặc định ~5s).
→ Vì worker chỉ nghe new, một đám đông đứng yên sau khi đã xuất hiện sẽ không sinh thêm new (chỉ còn update) → trigger của ta im lặng. Đó là gốc của vấn đề "không có lần thứ 2".

Rời khung hình rồi vào lại? Có một cửa sổ ân hạn (max_disappeared): nếu quay lại trong vài giây và đúng vị trí, tracker có thể nối lại ID cũ. Nhưng nếu đi đủ lâu hoặc vào từ chỗ khác → tracker mất liên tục → gán ID mới → phát new mới. Tức là cùng một người vào lại = một object "mới" với Frigate.

Tracking 1 người across nhiều camera? Không — Frigate không hỗ trợ natively. Mỗi camera track độc lập, ID không liên thông. Đây chính là bài toán §2.1 (Multi-Camera Tracking & Re-ID: ByteTrack → FastReID → MTMC) trong ROADMAP.md và Tong_hop §2.1 — chưa làm, deferred. Frigate 0.16/0.17 có face recognition / semantic search nhưng không có cross-camera Re-ID tổng quát.



Motion filter: so sánh khung-với-khung tìm vùng pixel thay đổi (rẻ, chạy trên CPU). Đây là cái quyết định ở đâu và có nên chạy model nhận diện.
Object detection: Frigate chỉ chạy model NN trên (a) vùng có motion + (b) vùng của object đang được track. Đây là chỗ "tiết kiệm xử lý" — không có gì động đậy thì model chạy ~0 lần.
Tracker: gán các detection qua các frame thành object có id ổn định, giữ box theo thời gian.



Vai trò motion threshold: nó là cổng cho lần kích hoạt detect ban đầu — phải có motion đủ lớn ở một vùng thì Frigate mới chịu chạy model ở đó để phát hiện người mới.

Nhưng một khi đã track được người rồi, Frigate vẫn tự chạy detect trên vùng của người đó để bám tiếp, không cần motion mới. Trừ một ngoại lệ quan trọng: khi người đứng yên, Frigate bật cơ chế stationary (tham số detect.stationary.interval / threshold) → cố tình giảm tần suất re-detect để tiết kiệm CPU. → Đây chính là lý do bạn đo được gap tới 16.4s ở người đứng yên — Frigate biết họ không nhúc nhích nên lười cập nhật, và điều đó vô hại vì box vẫn đúng.



Chỉnh motion threshold theo từng camera (cam gần / cam xa)?
Được — motion là per-camera (set global rồi override từng cam), không cần đụng source. Các tham số:

motion.threshold — pixel phải đổi bao nhiêu mới tính là motion (thấp = nhạy hơn).
motion.contour_area — kích thước vùng motion tối thiểu; đây mới là cái quan trọng cho gần/xa: cam xa → người nhỏ → cần contour_area nhỏ để bắt; cam gần → người to.
motion.mask — vùng bỏ qua.
NHƯNG đừng nhầm lever: motion threshold sửa độ phủ phát hiện (bắt được người hay không, gần/xa), không sửa được "box không bám". Hai núm thật sự ảnh hưởng độ bám là:

detect.fps (bạn đã hạ 8→5 để giảm CPU — chính đánh đổi này làm box di chuyển trễ hơn chút).
detect.stationary.* (chi phối cadence người đứng yên).



Codex đọc instruction khi khởi tạo session.
Sửa AGENTS.md xong thường cần mở session mới (hoặc restart agent) để instruction mới có hiệu lực.






Còn thiếu trong codebase: Code map, tự cập nhật 4 file context: CLAUDE.md, config.py, ROADMAP.md, SYSTEM.md
Edge cases: Nếu như frame bị cắt/loop lại thì xử lý thế nào?
Bugs: Đôi khi loitering mất hơn 40s để detect