"""Terminal-independent view model: explicit states, never substring colouring."""

import datetime
import math
import re
import time
import unicodedata
from dataclasses import dataclass

import telemetry

PAGES = {
    1: "TONG QUAN",
    2: "MANG / DIA CHI IP",
    3: "GITHUB RUNNER",
    4: "USB / THAO TAC",
    5: "UNG DUNG / IMAGES",
    6: "DICH VU HE THONG",
    7: "LICH SU THAY DOI",
    8: "DO ON DINH",
    9: "HUONG DAN",
}
TONES = ("normal", "title", "ok", "warn", "error", "info", "muted", "unknown")


def clean(text):
    text = str(text).replace("đ", "d").replace("Đ", "D")
    text = "".join(
        c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c)
    )
    return "".join(c if 32 <= ord(c) < 127 else " " for c in text)


def cut(text, width):
    text = clean(text)
    return text if len(text) <= width else text[: max(0, width - 3)] + "..."


@dataclass
class Row:
    text: str
    tone: str = "normal"


@dataclass
class Status:
    label: str
    tone: str
    rank: int


def container_status(row):
    state, health = row.get("state"), row.get("health")
    if (
        state in ("dead", "removing")
        or state in ("running", "restarting")
        and health == "unhealthy"
        or state == "exited"
        and row.get("exit_code", 0) != 0
    ):
        return Status("LOI", "error", 0)
    if state in ("restarting", "paused") or health == "starting":
        return Status("CHO / TAM DUNG", "warn", 1)
    if state == "running" and health == "healthy":
        return Status("HEALTHY", "ok", 3)
    if state == "running":
        return Status("CHUA HEALTHCHECK", "info", 4)
    if state in ("exited", "created"):
        return Status("DA DUNG", "muted", 5)
    return Status("CHUA RO", "unknown", 2)


def service_status(row):
    state, sub, load = row.get("state"), row.get("sub"), row.get("load")
    if state == "failed" or load in ("error", "bad-setting"):
        return Status("LOI", "error", 0)
    if state in ("activating", "deactivating", "reloading"):
        return Status("DANG CHUYEN", "warn", 1)
    if state == "active":
        return Status("HOAN TAT" if sub == "exited" else "DANG CHAY", "ok", 3)
    if load == "template" or state == "not-instantiated":
        return Status("MAU UNIT", "muted", 6)
    if row.get("enabled") == "masked" or load == "masked":
        return Status("BI KHOA", "muted", 6)
    if state == "inactive":
        return Status(
            "CHUA CHAY", "info" if row.get("enabled") == "enabled" else "muted", 5
        )
    return Status("CHUA RO", "unknown", 2)


def human_bytes(n):
    if n is None:
        return "--"
    n = float(n)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if abs(n) < 1024 or unit == "TiB":
            return f"{n:.1f}{unit}"
        n /= 1024


def duration(seconds):
    if seconds is None:
        return "--"
    seconds = max(0, int(seconds))
    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    return (f"{days}d " if days else "") + f"{hours:02}:{minutes:02}:{seconds:02}"


def age(at, now=None):
    return duration((time.time() if now is None else now) - at) if at else "--"


def percent(used, total):
    return max(0, min(100, 100 * used / total)) if total else None


def bar(label, value, detail, width=44):
    if value is None:
        return Row(f"{label:<6} [   CHUA DO   ] {detail}", "unknown")
    size = max(6, min(18, width - 30))
    filled = round(size * value / 100)
    tone = "error" if value >= 95 else "warn" if value >= 85 else "ok"
    return Row(
        f'{label:<6} [{"#"*filled}{"."*(size-filled)}] {value:5.1f}%  {detail}', tone
    )


def section(title):
    return [Row(""), Row(title, "title")]


def check(label, value, detail=""):
    status, tone = (
        ("OK", "ok")
        if value is True
        else ("FAIL", "warn") if value is False else ("CHUA DO", "unknown")
    )
    return Row(f"[{status}] {label}  {detail}", tone)


def host(value):
    return value.get("host") or value


def primary_ips(value):
    h = host(value)
    dev = (h.get("route") or {}).get("dev")
    physical = sorted(
        h.get("links", []),
        key=lambda row: (
            row["name"] != dev,
            row["name"].startswith(("docker", "br-", "veth")),
        ),
    )
    lan, vpn = [], []
    for row in physical:
        for address in row.get("addresses", []):
            if row["name"].startswith("tailscale"):
                vpn.append(address)
            elif not row["name"].startswith(("docker", "br-", "veth")):
                lan.append(address)
    return next((ip for ip in lan if ":" not in ip), next(iter(lan), "--")), next(
        iter(vpn), "--"
    )


def summary(value):
    inv = value.get("inventory", {})
    containers, services = inv.get("containers", []), inv.get("services", [])
    return {
        "apps": len(containers),
        "healthy": sum(container_status(c).label == "HEALTHY" for c in containers),
        "unchecked": sum(
            c.get("state") == "running" and c.get("health") == "not-configured"
            for c in containers
        ),
        "app_errors": sum(container_status(c).tone == "error" for c in containers),
        "services": len(services),
        "active": sum(s.get("state") == "active" for s in services),
        "failed": sum(service_status(s).tone == "error" for s in services),
        "inactive": sum(s.get("state") == "inactive" for s in services),
        "errors": {**inv.get("errors", {}), **host(value).get("errors", {})},
    }


def overview(value, width):
    h, inv, counts = host(value), value.get("inventory", {}), summary(value)
    m, disk = h.get("memory", {}), h.get("disk", {})
    used = m.get("MemTotal", 0) - m.get("MemAvailable", 0)
    ram = percent(used, m.get("MemTotal")) if "MemAvailable" in m else None
    space = percent(disk.get("total", 0) - disk.get("free", 0), disk.get("total"))
    probe = h.get("probe", {})
    rows = [
        bar("CPU", h.get("cpu"), f'{h.get("cores","?")} cores', width),
        bar("RAM", ram, f'{human_bytes(used)}/{human_bytes(m.get("MemTotal"))}', width),
        bar("SD", space, f'con {human_bytes(disk.get("free"))}', width),
        Row(
            f'NHIET {h.get("temperature") if h.get("temperature") is not None else "--"} C  | SWAP {human_bytes(m.get("SwapTotal",0)-m.get("SwapFree",0))}/{human_bytes(m.get("SwapTotal"))}',
            "warn" if (h.get("temperature") or 0) >= 75 else "normal",
        ),
        Row(
            "LOAD  "
            + " / ".join(f"{v:.2f}" for v in h.get("load", []))
            + " (1/5/15 phut)",
            "muted",
        ),
        Row("MANG / PROBE GAN NHAT", "title"),
        check(
            "HTTPS www.google.com",
            probe.get("https"),
            f'{probe.get("latency_ms") if probe.get("latency_ms") is not None else "--"} ms | {age(probe.get("at"))} truoc',
        ),
        check(
            "DNS",
            probe.get("dns"),
            f'Gateway: {(h.get("route") or {}).get("gateway","--")}',
        ),
        Row("UNG DUNG / DICH VU", "title"),
        Row(
            f'DOCKER {counts["apps"]} | {counts["healthy"]} healthy | {counts["app_errors"]} loi | {counts["unchecked"]} chua check',
            (
                "unknown"
                if counts["errors"]
                else "error" if counts["app_errors"] else "info"
            ),
        ),
        Row(
            f'SYSTEM {counts["active"]}/{counts["services"]} active | {counts["failed"]} loi | {counts["inactive"]} inactive',
            "unknown" if counts["errors"] else "error" if counts["failed"] else "info",
        ),
    ]
    alerts = [
        Row(f'[LOI] {r["id"]}: {r.get("result") or r.get("sub")}', "error")
        for r in inv.get("services", [])
        if service_status(r).tone == "error"
    ]
    alerts += [
        Row(f'[LOI] {r["name"]}: {r.get("state")} / {r.get("health")}', "error")
        for r in inv.get("containers", [])
        if container_status(r).tone == "error"
    ]
    rows += alerts[:3] or [
        Row(
            (
                "Khong thay FAILED/UNHEALTHY."
                if not counts["errors"]
                else "Nguon chua day du: kiem tra discovery."
            ),
            "info" if not counts["errors"] else "unknown",
        )
    ]
    rows += [Row("Chi tiet: [2] Mang  [5] Apps  [6] Dich vu  [8] On dinh", "muted")]

    return rows


def network(value, width):
    h = host(value)
    rows = [Row("INTERFACE / IP / LUU LUONG", "title")]
    for link in h.get("links", []):
        good = (
            link.get("state") == "UP"
            and bool(link.get("addresses"))
            and link.get("carrier") != "0"
        )
        vpn = (
            link["name"].startswith(("tailscale", "wg", "tun"))
            and link.get("up")
            and link.get("addresses")
        )
        label = (
            "UP / CO IP"
            if good
            else (
                "VPN / CO IP"
                if vpn
                else "LINK DOWN" if link.get("state") == "DOWN" else "CHUA XAC MINH"
            )
        )
        rows += [
            Row(
                f'[{label}] {link["name"]}  state={link.get("state")}  carrier={link.get("carrier", "?")}',
                (
                    "ok"
                    if good
                    else "info" if vpn else "warn" if link.get("addresses") else "muted"
                ),
            ),
            Row("  IP: " + ", ".join(link.get("addresses") or ["--"]), "info"),
        ]
        rates, counters = link.get("rates", {}), link.get("counters", {})
        rows += [
            Row(
                f'  RX {human_bytes(rates.get("rx"))}/s  TX {human_bytes(rates.get("tx"))}/s',
                "normal",
            ),
            Row(
                f'  Bo dem tu luc tao interface: err RX/TX {counters.get("rx_errors", "--")}/{counters.get("tx_errors", "--")} | drop {counters.get("rx_dropped", "--")}/{counters.get("tx_dropped", "--")}',
                "muted",
            ),
        ]
    if not h.get("links"):
        rows += [Row("Chua doc duoc interface co dia chi.", "unknown")]
    route, probe = h.get("route") or {}, h.get("probe", {})
    rows += section("GATEWAY / DNS / INTERNET")
    rows += [
        Row(
            f'Default: {route.get("gateway", "--")} dev {route.get("dev", "--")}',
            "info",
        )
    ]
    rows += [Row(line, "muted") for line in h.get("dns", [])]
    rows += [
        check("ICMP gateway", probe.get("gateway"), "(router co the chan ICMP)"),
        check("DNS www.google.com", probe.get("dns")),
        check(
            "HTTPS /generate_204",
            probe.get("https"),
            f'code={probe.get("http_code")}  {probe.get("latency_ms")} ms',
        ),
        Row(
            f'Probe {age(probe.get("at"))} truoc; cap nhat ~30s. [8] Lich su.', "muted"
        ),
        Row("Chi kiem tra mot dich HTTPS; khong dai dien toan bo Internet.", "muted"),
    ]
    if probe.get("note"):
        rows += [Row(probe["note"], "unknown")]
    vpn = h.get("vpn", {})
    rows += section("TAILSCALE / TRUY CAP TU XA")
    rows += [
        Row(
            "Backend: " + vpn.get("state", "Unavailable"),
            "ok" if vpn.get("state") == "Running" else "warn",
        )
    ]
    rows += [
        Row(
            f'[{"ONLINE" if p.get("online") else "OFFLINE"}] {p["name"]}  {p["ip"]}',
            "ok" if p.get("online") else "muted",
        )
        for p in vpn.get("peers", [])
    ]
    rows += [
        Row("Peer Online la trang thai Tailscale, khong phai HTTP health.", "muted"),
        Row("N: mo nmtui (can xac nhan + quyen sudo; co the mat SSH).", "warn"),
    ]
    return rows


def listing(value, source, query, mode, detail, width):
    inv = value.get("inventory", {})
    classifier = container_status if source == "containers" else service_status
    source_rows = inv.get(source, [])
    rows = sorted(
        source_rows, key=lambda r: (classifier(r).rank, r.get("name", r["id"]))
    )
    rows = [
        r
        for r in rows
        if clean(query).lower()
        in clean(
            " ".join(
                str(r.get(k, ""))
                for k in ("id", "name", "description", "project", "service", "image")
            )
        ).lower()
    ]
    if mode == "attention":
        rows = [r for r in rows if classifier(r).tone in ("error", "warn", "unknown")]
    elif mode == "active":
        rows = [r for r in rows if r.get("state") in ("active", "running")]
    output = [
        Row(
            f'{len(rows)}/{len(source_rows)} ket qua | Loc: {mode} | Tim: {query or "--"}',
            "info",
        ),
        Row("F doi loc  / tim ten  C xoa tim  D chi tiet", "muted"),
        Row(""),
    ]
    for r in rows:
        status = classifier(r)
        label = r.get("name", r["id"])
        tone = "unknown" if source in inv.get("errors", {}) else status.tone
        output += [Row(f"[{status.label}] {label}", tone)]
        if source == "containers":
            output += [
                Row(
                    f'  state={r.get("state")}  health={r.get("health")}  restarts={r.get("restart_count", 0)}  exit={r.get("exit_code", "--")}',
                    tone,
                ),
                Row(
                    f'  Compose: {r.get("project") or "--"}/{r.get("service") or "--"}  Image: {r.get("image", "--")}',
                    "muted",
                ),
            ]
            if detail:
                output += [
                    Row("  ID: " + r.get("image_id", "--"), "muted"),
                    Row(
                        "  Bat dau: "
                        + r.get("started_at", "--")
                        + "  Ket thuc: "
                        + r.get("finished_at", "--"),
                        "muted",
                    ),
                ]
                for target, bindings in r.get("ports", {}).items():
                    output += [
                        Row(
                            f'  {b.get("HostIp") or "*"}:{b["HostPort"]} -> {target}',
                            "info",
                        )
                        for b in bindings or []
                    ]
        else:
            active_age = (
                duration(
                    max(
                        0,
                        host(value).get(
                            "monotonic_uptime", host(value).get("uptime", 0)
                        )
                        - r.get("active_since_us", 0) / 1e6,
                    )
                )
                if r.get("active_since_us") and r.get("state") == "active"
                else "--"
            )
            output += [
                Row(
                    f'  {r.get("state")}/{r.get("sub")} | {r.get("enabled") or "--"} | PID {r.get("pid",0)} | restart {r.get("restarts",0)} | up {active_age}',
                    tone,
                )
            ]
            if detail:
                output += [
                    Row("  " + r.get("description", ""), "muted"),
                    Row(
                        f'  Load={r.get("load")}  Result={r.get("result") or "--"}',
                        "muted",
                    ),
                ]
        output += [Row("")]
    if not rows:
        output += [Row("Khong co ket qua cho bo loc hien tai.", "info")]
    if source == "services":
        output += [
            Row("DANG CHAY: systemd active. HOAN TAT: oneshot active/exited.", "muted"),
            Row("CHUA CHAY/BI KHOA/MAU UNIT khong dong nghia voi loi.", "muted"),
            Row(
                "Restart la NRestarts systemd; khong phai so lan hong ca doi.", "muted"
            ),
        ]
    else:
        output += [
            Row(
                "CHUA HEALTHCHECK: container chay, chua chung minh app san sang.",
                "info",
            )
        ]
        output += section("LOCAL IMAGES / BAO GOM IMAGE KHONG DUNG")
        for r in inv.get("images", []):
            if (
                query
                and clean(query).lower()
                not in clean(r.get("repository", "") + ":" + r.get("tag", "")).lower()
            ):
                continue
            output += [
                Row(
                    f'{r.get("repository")}:{r.get("tag")}  {r.get("size", "--")}',
                    "info",
                )
            ]
            if detail:
                output += [
                    Row(f'  {r.get("image_id")}  digest={r.get("digest")}', "muted")
                ]
    return output


def stability_page(value, width):
    h = host(value)
    stats = telemetry.stability(h)
    rate = (
        f'{stats["success_percent"]:.1f}%'
        if stats["success_percent"] is not None
        else "CHUA DU LIEU"
    )
    rows = [
        Row("LICH SU QUAN SAT / KHONG PHAI CAM KET SLA", "title"),
        Row(f'Uptime he dieu hanh: {duration(h.get("uptime"))}', "info"),
        Row(
            (
                "Bat dau quan sat: "
                + datetime.datetime.fromtimestamp(h["observed_since"]).strftime(
                    "%d/%m/%Y %H:%M"
                )
                if h.get("observed_since")
                else "Chua co moc bat dau quan sat"
            ),
            "muted",
        ),
        Row(
            f'Reboot da quan sat: {h.get("observed_reboots",0)} (tu khi bat dau luu telemetry)',
            "info",
        ),
        Row(
            f'Cua so: {duration(stats["span_seconds"])} / toi da 1 gio | {stats["checks"]} probe da do',
            "normal",
        ),
        Row(
            f'HTTPS thanh cong: {rate} | OK {stats["good"]} | FAIL {stats["bad"]}',
            "warn" if stats["bad"] else "ok" if stats["checks"] else "unknown",
        ),
        Row(
            f'Chuyen OK/FAIL: {stats["transitions"]} | Khoang trong lay mau: {stats["gaps"]}',
            "warn" if stats["transitions"] or stats["gaps"] else "normal",
        ),
        Row(
            f'Do tre probe thanh cong: TB {stats["average_ms"] if stats["average_ms"] is not None else "--"} ms | Max {stats["max_ms"] if stats["max_ms"] is not None else "--"} ms',
            "info",
        ),
        Row("Moi ~30s: DNS + HTTPS toi www.google.com/generate_204.", "muted"),
        Row("Khong suy dien khoang mat mau la online hoac offline.", "muted"),
        Row("ICMP bi chan khong co nghia Internet bi dut.", "muted"),
    ]
    rows += section("NGUON / NHIET DO RASPBERRY PI")
    flags = h.get("throttle_flags")
    if flags is None:
        rows += [
            Row("Khong doc duoc vcgencmd: chua xac dinh sut ap/throttling.", "unknown")
        ]
    else:
        labels = ("Sut ap", "Gioi han tan so", "Throttled", "Gioi han nhiet mem")
        for i, label in enumerate(labels):
            rows += [
                Row(
                    f'{label}: {"DANG XAY RA" if flags & (1<<i) else "khong"} | lich su boot: {"CO" if flags & (1<<(i+16)) else "khong"}',
                    (
                        "error"
                        if flags & (1 << i)
                        else "warn" if flags & (1 << (i + 16)) else "ok"
                    ),
                )
            ]
    rows += section("CANG MOI CANG O TREN / 30 PROBE GAN NHAT")
    for r in reversed(h.get("history", [])[-30:]):
        stamp = datetime.datetime.fromtimestamp(r["at"]).strftime("%H:%M:%S")
        status = check(
            stamp,
            r.get("https"),
            f'{r.get("latency_ms") if r.get("latency_ms") is not None else "--"} ms  DNS={r.get("dns")}  GW={r.get("gateway")}',
        )
        rows += [status]
    return rows


def page_rows(value, page, width=100, query="", mode="all", detail=False):
    if not value:
        return [
            Row("DANG LAY DU LIEU... Neu lau hon 30s, kiem tra monitor.", "unknown")
        ]
    inv = value.get("inventory", {})
    rows = []
    if page == 1:
        rows = overview(value, width)
    elif page == 2:
        rows = network(value, width)
    elif page in (5, 6):
        rows = listing(
            value, "containers" if page == 5 else "services", query, mode, detail, width
        )
    elif page == 8:
        rows = stability_page(value, width)
    elif page == 3:
        runners = [
            r for r in inv.get("services", []) if r["id"].startswith("actions.runner.")
        ]
        rows = [Row("RUNNER / TRANG THAI SERVICE TREN MAY NAY", "title")]
        for r in runners:
            state = service_status(r)
            rows += [
                Row(f'[{state.label}] {r["id"]}', state.tone),
                Row(
                    f'  {r.get("state")}/{r.get("sub")}  PID={r.get("pid")}  restart={r.get("restarts",0)}',
                    state.tone,
                ),
            ]
        if not runners:
            rows += [Row("Khong tim thay runner service.", "info")]
        rows += [
            Row(
                "Service active khong xac nhan runner Online/Idle tren GitHub.", "muted"
            )
        ]
    elif page == 4:
        rows = [Row("THIET BI USB / FILESYSTEM DA CO", "title")]
        for p in value.get("usb", []):
            rows += [
                Row(
                    f'{p["path"]}  {human_bytes(p.get("size"))}  {p.get("fstype")}  {p.get("label") or ""}',
                    "info",
                ),
                Row(
                    (
                        "  " + ", ".join(m for m in p.get("mountpoints", []) or [] if m)
                        if any(p.get("mountpoints") or [])
                        else "  Chua mount"
                    ),
                    "muted",
                ),
            ]
        if not value.get("usb"):
            rows += [Row("Chua thay phan vung USB da format.", "info")]
        rows += section("THAO TAC / CAN XAC NHAN VA QUYEN SUDO")
        rows += [
            Row("N  Cau hinh mang qua nmtui. Co the gian doan SSH.", "warn"),
            Row(
                "U  Mount phan vung USB co san. E  Unmount USB do console mount.",
                "info",
            ),
            Row("L  Sang tty2 de dang nhap (chi tren HDMI).", "info"),
            Row(
                "Khong format, khong di chuyen DB, khong tu restart ung dung.", "muted"
            ),
        ]
    elif page == 7:
        rows = [Row("BIEN DONG INVENTORY / 200 BAN GHI GAN NHAT", "title")]
        for e in value.get("changes", []):
            rows += [
                Row(
                    f'{e.get("at")} | {e.get("source")} | {e.get("kind")}',
                    "warn" if e.get("kind") == "removed" else "info",
                ),
                Row("  " + e.get("name", "--"), "normal"),
            ]
            if e.get("fields"):
                rows += [Row("  Truong doi: " + ", ".join(e["fields"]), "muted")]
        if not value.get("changes"):
            rows += [Row("Chua co thay doi duoc ghi nhan.", "info")]
        rows += [Row("Polling co the bo lo thay doi xay ra giua hai mau.", "muted")]
    else:
        rows = [
            Row("BAN DANG THEO DOI GI?", "title"),
            Row("1 Tong quan: hostname, IP, CPU, RAM, SD, nhiet, canh bao.", "normal"),
            Row("2 Mang: interface, LAN/VPN IP, RX/TX, gateway, DNS, HTTPS.", "normal"),
            Row(
                "3 Runner: systemd runner; trang thai GitHub xem tren GitHub.", "normal"
            ),
            Row("4 USB / thao tac: nmtui, mount/unmount, tty2.", "normal"),
            Row(
                "5 Ung dung: tat ca container + Compose + health + local image.",
                "normal",
            ),
            Row(
                "6 Dich vu: installed/loaded/transient; state, PID, restart.", "normal"
            ),
            Row("7 Bien dong: them/xoa/doi image, health, state.", "normal"),
            Row(
                "8 On dinh: probe 1 gio, do tre, khoang mat mau, sut ap/nhiet.",
                "normal",
            ),
            Row("PHIM / DIEU HUONG", "title"),
            Row("1..9 chon trang | Tab/Left/Right doi trang | R refresh", "info"),
            Row("Up/Down J/K cuon | PgUp/PgDn nhanh | Home/End dau/cuoi", "info"),
            Row("/ tim Apps/Services | C xoa tim | F All/Can chu y/Active", "info"),
            Row("D chi tiet Apps/Services | Q thoat (HDMI service se mo lai)", "info"),
            Row("XANH=trang thai OK | VANG=can xem | DO=loi | XAM=chua ro", "normal"),
            Row("Inactive/disabled/oneshot khong dong nghia FAILED.", "muted"),
            Row("Docker running khong dong nghia healthy neu chua co check.", "muted"),
            Row("Do on dinh la quan sat mot dich HTTPS, khong phai SLA.", "muted"),
            Row(
                "Khong hien thi secrets, command/env hay journal log ung dung.", "muted"
            ),
            Row(
                "User services, Podman, process chay tay khong duoc inventory.", "muted"
            ),
            Row("Du lieu monitor >30s duoc danh dau CU / CHUA XAC MINH.", "warn"),
        ]
    errors = {**inv.get("errors", {}), **host(value).get("errors", {})}
    if errors:
        rows = [
            Row("DISCOVERY UNAVAILABLE / CHUA XAC MINH: " + ", ".join(errors), "error"),
            Row("Du lieu nguon loi co the la mau cu; khong coi la healthy.", "unknown"),
            Row(""),
        ] + rows
    return rows


def frame(
    value, page, width, height, offset=0, query="", mode="all", detail=False, error=None
):
    width, height = max(20, width), max(8, height)
    h = host(value or {})
    name = h.get("hostname") or (value or {}).get("hostname") or "--"
    at = (value or {}).get("inventory_at", (value or {}).get("at", 0))
    stale = bool(error or at and time.time() - at > 30)
    lan, vpn = primary_ips(value or {})
    counts = summary(value or {})
    issue = counts["app_errors"] + counts["failed"]
    state = (
        "CU / CHUA XAC MINH"
        if stale
        else (
            "CAN KIEM TRA NGUON"
            if counts["errors"]
            else f"{issue} LOI CAN XEM" if issue else "DANG THEO DOI"
        )
    )
    head = [
        Row(
            f"TH-SERVER  |  {name}  |  {state}",
            "error" if stale or issue else "warn" if counts["errors"] else "title",
        ),
        Row(f"LAN {lan}  |  VPN {vpn}", "info"),
        Row(
            f'UP {duration(h.get("uptime"))}  |  Mau {age(at)} truoc  |  {datetime.datetime.now().strftime("%d/%m %H:%M:%S")}',
            "muted",
        ),
        Row(
            "THEO DOI  [1] Tong quan  [2] Mang  [5] Apps  [6] Dich vu  [8] On dinh",
            "info",
        ),
        Row("VAN HANH  [3] Runner  [4] USB  [7] Bien dong  [9] Huong dan", "normal"),
        Row("-" * width, "muted"),
        Row(
            PAGES.get(page, "HUONG DAN")
            + ("  | CHI TIET" if detail else "")
            + ("  / " + query if query else ""),
            "title",
        ),
    ]
    # Keep a usable body even in an 80x24 tty or a small SSH pane.
    if height < 20:
        head = [
            head[0],
            head[1],
            Row(
                "1 Home 2 Net 3 Runner 4 USB 5 Apps 6 Svc 7 Log 8 Stable 9 Help", "info"
            ),
            head[-1],
        ]
    body = []
    import textwrap

    for row in page_rows(value, page, width - 4, query, mode, detail):
        for line in textwrap.wrap(
            clean(row.text),
            max(16, width - 4),
            subsequent_indent="  ",
            break_long_words=True,
        ) or [""]:
            body.append(Row(line, row.tone))
    count = max(1, height - len(head) - 3)
    offset = max(0, min(offset, max(0, len(body) - count)))
    shown = body[offset : offset + count]
    footer = [
        Row(
            f"Dong {offset+1 if body else 0}-{min(len(body),offset+count)}/{len(body)}  | F loc:{mode}  / tim  D chi tiet",
            "muted",
        ),
        Row("OK=XANH  CAN XEM=VANG  LOI=DO  CHUA RO=XAM", "normal"),
        Row("1..9 Trang | Tab Doi | J/K/PgUp/PgDn Cuon | R Lam moi | Q Thoat", "info"),
    ]
    grid = head + shown + [Row("")] * max(0, count - len(shown)) + footer
    return (
        [Row(cut(row.text, width - 1), row.tone) for row in grid[:height]],
        offset,
        len(body),
        count,
    )


def ansi(rows):
    colours = {
        "title": "1;36",
        "ok": "32",
        "warn": "33",
        "error": "1;31",
        "info": "36",
        "muted": "2;37",
        "unknown": "2;37",
        "normal": "0",
    }
    return "\n".join(
        "\033[" + colours.get(row.tone, "0") + "m" + clean(row.text) + "\033[0m"
        for row in rows
    )
