# Mẫu và chứng cứ kiểm thử

- `english-sample.wav` (~440 KB): giọng đọc tổng hợp bằng voice Windows qua `Make-Sample.ps1`, không phải bản ghi cuộc họp. Nội dung: “Hello everyone. Today we will learn how to build a useful application. Please open the settings and check your audio device.”
- `offline-check.json`: kết quả nhận dạng/dịch thật trên mẫu, kiểm tra khi các lời gọi kết nối socket bị chặn. Số thời gian là một lần chạy trên máy kiểm thử, không phải cam kết hiệu năng.
- `ui-check.json`: phạm vi GUI đã kiểm tra ở cùng chế độ DPI với app thật, gồm cửa sổ tối thiểu, bản dịch hiện tại, bật/tắt tiếng Anh, overlay co giãn theo nội dung và thông báo lỗi dài. Capture thật không được mở. Lifecycle bắt đầu/dừng dùng nguồn giả, còn dịch file dùng mô hình thật.
- `controls.png`, `subtitles.png`: chỉ render cửa sổ của ứng dụng trong lần thử file mẫu; không chụp màn hình cuộc họp hay ứng dụng khác.

Các test có thể ghi đè các chứng cứ này khi chạy lại. Những file khác bạn đặt trong thư mục này được `.gitignore` bỏ qua; không commit âm thanh cá nhân.
