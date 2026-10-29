# TH-Server HDMI console

Giao diện terminal hiển thị trực tiếp trên HDMI/tty1 của Raspberry Pi. Không có hostname, cổng HTTP hay quyền Google OAuth mới.

- `1`: CPU, RAM, swap, thẻ SD, nhiệt độ, uptime, IP, health AI/Portfolio/Ticket, dịch vụ và bản triển khai.
- `2`: giao diện mạng, IP, gateway, DNS, Tailscale và các máy đang kết nối.
- `3`: các GitHub Actions runner và trạng thái systemd thực tế.
- `4`: các phân vùng USB có filesystem, dung lượng và mountpoint.
- `R`: làm mới; phím mũi tên/J/K: cuộn.
- `N`: mở NetworkManager/nmtui để thiết lập Wi-Fi hoặc Ethernet.
- `U` / `E`: gắn hoặc tháo phân vùng USB đã có, không format. Không tháo ổ hệ thống hoặc ổ đang dùng bởi container. Không tự di chuyển database. Thay đổi mạng và USB cần xác nhận và mật khẩu sudo của thadmin ngay trên HDMI.
- `L`: chuyển sang màn hình đăng nhập tty2. `Ctrl+Alt+F1` quay về console; `Ctrl+Alt+F2` về đăng nhập.

Console chạy bằng Python chuẩn của Pi để đọc đúng trạng thái host và điều khiển tty1. Docker `th-rpi-console:<commit>` là gói phân phối bất biến, chứa mã, unit systemd và trình cài đặt; container cài đặt kết thúc sau khi service host hoạt động. Không chạy một container đặc quyền thường trực.

GitHub Actions kiểm thử, build AMD64/ARM64, chờ bản Ticket được xác minh, cài service rồi kiểm tra health. Console hoạt động lại sau reboot. Snapshot cấu hình console được mã hóa lên Drive cùng cấu hình server.

Khôi phục màn hình đăng nhập tty1 (từ SSH hoặc tty2):

```sh
sudo systemctl disable --now th-server-console.service
sudo systemctl enable --now getty@tty1.service
```

Xem log: `journalctl -u th-server-console.service -n 30`. Chạy kiểm thử: `python3 -m unittest discover -s console -p 'test_*.py'`.
