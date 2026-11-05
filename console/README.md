# TH-Server console: HDMI, SSH và inventory tự động

Console dùng chung trên Raspberry Pi OS / Debian có systemd và Python 3.9+.
Không phụ thuộc hostname `th-server`, tài khoản `thadmin`, domain hay danh sách
ứng dụng TH cố định. Không cần cổng HTTP hoặc OAuth để xem trạng thái.
Docker package được build cho AMD64/ARM64; bản cài native dùng Python của host.
Các model Pi, OS 32 bit và phần cứng HDMI thật cần kiểm tra riêng trước khi triển khai.

## Tự phát hiện

`th-server-monitor.service` quét mỗi 5 giây, kể cả khi không mở console:

- Docker: toàn bộ container chạy/dừng, Compose project/service, cổng publish,
  image reference và image ID thực tế; toàn bộ image local, tag, digest, dung lượng.
- Health Docker: `healthy`, `unhealthy`, `starting`; `not-configured` khi container
  không có HEALTHCHECK. Container `running` không tự động được coi là healthy.
- Systemd: mọi system service đã cài, gồm disabled/masked/inactive, cùng loaded
  service và transient unit; trạng thái, PID, kết quả thực thi và mô tả.
- Thêm, xóa, đổi trạng thái/health/image/service được so với lần quét trước.
  Giữ 200 thay đổi gần nhất trong `/var/lib/th-server-console/inventory.json`,
  ghi atomically và giữ qua reboot. Đây là lịch sử quan sát định kỳ, không phải
  audit log của mọi event: thay đổi xảy ra rồi biến mất giữa hai lần quét có thể
  không được ghi nhận. Container recreate được ghi là xóa/thêm theo container ID.

Không phải sửa code/config mỗi khi thêm ứng dụng hoặc service. Ứng dụng native
được theo dõi qua unit systemd tương ứng. User-session service, process chạy tay
ngoài Docker/systemd, Podman/Kubernetes và registry image từ xa không nằm trong
inventory này. Systemd `active` là trạng thái service, không chứng minh HTTP
readiness. Health ứng dụng trong Docker nên khai báo bằng HEALTHCHECK của image
hoặc `healthcheck` trong Compose; monitor không đoán URL/cổng hoặc gọi endpoint lạ.

Nếu Docker/systemd không đọc được, console báo `DISCOVERY UNAVAILABLE`; monitor
hoặc dữ liệu quá 30 giây được đánh dấu stale. Không biến lỗi quyền truy cập thành
“không có ứng dụng”. Không đọc environment, lệnh khởi động hay healthcheck log
của container, không ghi token/credential vào inventory.

## Giao diện HDMI / SSH

- `1`: CPU, RAM, swap, filesystem, nhiệt độ, uptime, mạng và container.
- `2`: mạng, gateway, DNS và Tailscale (khi đã cài).
- `3`: tự tìm mọi runner systemd `actions.runner.*.service`.
- `4`: USB; `5`: container và images; `6`: mọi host service; `7`: thay đổi.
- `R`: refresh; mũi tên/J/K: cuộn; `Q`: thoát.
- `N`: nmtui nếu có; `U`/`E`: mount/unmount USB đã có filesystem. Thao tác thay
  đổi cần xác nhận và sudo của người quản trị; không format hoặc di chuyển database.
- `L`: chuyển tty2 trên console vật lý; SSH không chuyển virtual terminal.

Sau khi cài, SSH vào host và chạy `th-server`, hoặc `ssh -t user@pi th-server`.
`th-server --snapshot` xuất JSON, dùng được không có TTY. Nếu monitor chưa được
cài/chạy, console quét trực tiếp và chỉ giữ thay đổi trong phiên đang mở; người
xem cần quyền đọc Docker/systemd (dùng sudo nếu cần, không tự thêm user vào nhóm docker).

## Chuẩn bị host — chỉ thực hiện khi có quyết định triển khai

Các lệnh dưới đây là hướng dẫn cài đặt thủ công; CI không chạy chúng trên RPI.
Dùng checkout của release đã review, không dùng một SHA khác với source:

```sh
release="$(git rev-parse HEAD)"
# Native: cài baseline Python/CA/iproute2/util-linux/sudo và monitor + lệnh SSH.
sudo bash console/setup.sh --release "$release"

# Các tùy chọn bổ sung chỉ bật khi cần trên host đích:
# --docker: cài Docker của distro và enable docker.service.
# --ssh: cài OpenSSH server và enable ssh.service; không đổi xác thực SSH.
# --network-manager: cài NetworkManager; không ghi đè cấu hình mạng.
# --hdmi --admin-user YOUR_USER: thay getty tty1 bằng console.
```

Không tạo user/password/hostname, không cài application stack, không dùng Docker
socket trong container chạy thường trực. SSH-only không chiếm tty1. Cập nhật bản
cài HDMI đang hoạt động giữ nguyên cấu hình quản trị hiện có; khi bật HDMI mới,
phải chỉ định user sudo đã tồn tại. Reinstall cùng release được chấp nhận nếu nội
dung immutable không thay đổi. Installer rollback link release, launcher và unit
khi lỗi; package đã cài bằng apt và service tùy chọn đã enable không được rollback
bởi installer. Sao lưu và review cấu hình host trước khi chạy provisioning.

Nếu đã có đủ dependency, có thể dùng trực tiếp
`sudo python3 console/install.py "$release" [--hdmi --admin-user YOUR_USER]`.
Installer kiểm tra systemd/Python của host, không yêu cầu Ticket, Portfolio hay AI.
Chạy natively được khuyến nghị cho lần thiết lập máy đầu tiên.

## Docker distribution

Image chứa code, unit, installer và tests. Mặc định chỉ xuất snapshot của chính
container; đó không phải trạng thái host nếu chưa cài native hoặc chưa gắn quyền
đọc host. Không chạy image với `--privileged` để làm dashboard thường trực.

Khi chủ động cài từ image immutable, truyền full release SHA và mount host theo
các đường dẫn installer cần. Ví dụ sau là thao tác triển khai thủ công, không chạy
trong workflow build/publish:

```sh
image="ghcr.io/tuanpham0411/th-rpi-console:$release"
docker run --rm --network none --pid host --user 0 \
  --cap-drop ALL --cap-add SYS_CHROOT --cap-add DAC_OVERRIDE \
  --cap-add FOWNER --cap-add CHOWN \
  --mount type=bind,src=/,dst=/host,readonly \
  --mount type=bind,src=/opt,dst=/host/opt \
  --mount type=bind,src=/usr/local/bin,dst=/host/usr/local/bin \
  --mount type=bind,src=/etc/systemd/system,dst=/host/etc/systemd/system \
  --mount type=bind,src=/run/systemd,dst=/host/run/systemd \
  --mount type=bind,src=/run/dbus,dst=/host/run/dbus,readonly \
  --tmpfs /host/tmp:rw,nosuid,nodev,noexec,size=16m \
  --tmpfs /host/var/tmp:rw,nosuid,nodev,noexec,size=16m \
  "$image" python3 /app/console/install.py "$release" --root /host
```

Thêm `--hdmi --admin-user YOUR_USER` vào cuối nếu đã quyết định thay tty1.
Container cài đặt kết thúc; monitor và console chạy bằng Python của host.

## Kiểm tra, CI và gỡ cài đặt

```sh
python3 -m unittest discover -s console -p 'test_*.py' -v
bash -n console/setup.sh
docker build -f Dockerfile.console -t th-console:test .
docker run --rm --network none th-console:test
```

Workflow `console.yml` kiểm thử PR; main/manual build và publish image
AMD64/ARM64 lên GHCR. Không có self-hosted runner, bước SSH, cài đặt lên host,
chờ Ticket production hay snapshot Drive trong workflow này. Các workflow quản
lý storage/automation hiện có là luồng riêng và không được thay đổi bởi console.

Sau khi triển khai thủ công, kiểm tra `systemctl status th-server-monitor.service`,
`journalctl -u th-server-monitor.service`, `th-server --snapshot`; thử thêm/dừng/xóa
một container thử hoặc một service thử và quan sát trang `7`. Kiểm tra SSH và HDMI
thật trên model Pi đích; CI mocks không thay thế kiểm tra phần cứng.

Khôi phục đăng nhập tty1:

```sh
sudo systemctl disable --now th-server-console.service
sudo systemctl enable --now getty@tty1.service
```

Dừng inventory nền: `sudo systemctl disable --now th-server-monitor.service`.
Các lệnh này không xóa application, image, dữ liệu hay cấu hình mạng hiện có.

## Triển khai thủ công qua runner th-server-1

Workflow `server-bootstrap.yml` chỉ chạy bằng `workflow_dispatch`. Nó kiểm thử
và publish image immutable trên GitHub-hosted runner, sau đó cài monitor/console
trên runner tên `th-server-1`, nhãn `th-server-bootstrap`. Không chạy storage
bootstrap, không dựng lại ứng dụng, không sửa OS/SSH/network configuration.
Runner cần quyền Docker hiện có; không thêm sudo không mật khẩu.

Sau khi mã này có trên GitHub và runner đã Online/Idle, chọn Actions →
Deploy server monitor and console manually → Run workflow. Chọn đúng nhánh
chứa bản sửa, bật `hdmi` nếu muốn thay tty1, và nhập tài khoản sudo hiện có
(với máy hiện tại là `thadmin`). Đây là thao tác triển khai chủ động; workflow
`console.yml` vẫn chỉ đóng gói và không tự deploy khi push.
