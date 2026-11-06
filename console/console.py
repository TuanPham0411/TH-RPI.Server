#!/usr/bin/env python3
"""TH-Server physical HDMI console. No HTTP listener or arbitrary command input."""

import argparse, curses, datetime, json, os, pathlib, pwd, re, shutil, socket, subprocess, threading, time, textwrap
import inventory
import monitor as inventory_monitor
import telemetry
import dashboard


def command(args, timeout=8):
    try:
        result = subprocess.run(
            args, text=True, capture_output=True, timeout=timeout, check=False
        )
        return result.stdout.strip() if result.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


def json_command(args, fallback=None):
    try:
        return json.loads(command(args))
    except (ValueError, TypeError):
        return fallback


def read(path, default=""):
    try:
        return pathlib.Path(path).read_text().strip()
    except OSError:
        return default


def bytes_text(n):
    n = float(n)
    for unit in ["B", "KiB", "MiB", "GiB", "TiB"]:
        if abs(n) < 1024 or unit == "TiB":
            return f"{n:.1f} {unit}"
        n /= 1024


def block_devices():
    tree = json_command(
        [
            "lsblk",
            "--json",
            "--bytes",
            "-o",
            "NAME,PATH,TYPE,SIZE,FSTYPE,LABEL,MOUNTPOINTS,TRAN,RM,UUID",
        ],
        {},
    )
    found = []

    def walk(items, usb=False):
        for item in items:
            external = usb or item.get("tran") == "usb"
            if external and item.get("type") == "part" and item.get("fstype"):
                found.append(
                    {
                        k: item.get(k)
                        for k in [
                            "path",
                            "size",
                            "fstype",
                            "label",
                            "mountpoints",
                            "uuid",
                        ]
                    }
                )
            walk(item.get("children", []), external)

    walk(tree.get("blockdevices", []))
    return found


def protected_mount(mountpoint):
    mount = pathlib.Path(mountpoint).resolve()
    if mount == pathlib.Path("/") or str(mount).startswith(
        ("/boot", "/var/lib/docker")
    ):
        return True
    if not command(["docker", "info", "--format", "{{.ServerVersion}}"]):
        return True
    ids = command(["docker", "ps", "-q"]).split()
    if ids:
        # Inspect one container at a time: newline-delimited JSON is not one array.
        for identifier in ids:
            mounts = json_command(
                ["docker", "inspect", "--format", "{{json .Mounts}}", identifier], None
            )
            if mounts is None:
                return True
            for item in mounts:
                source = pathlib.Path(item.get("Source", "/")).resolve()
                if source == mount or mount in source.parents:
                    return True
    return False


def usb_action(device, detach=False):
    parts = {p["path"]: p for p in block_devices()}
    if device not in parts or not re.fullmatch(r"/dev/[a-zA-Z0-9]+", device):
        raise ValueError("Choose an existing USB partition")
    part = parts[device]
    mounts = [m for m in part.get("mountpoints") or [] if m]
    if detach:
        if not mounts:
            raise ValueError("Partition is not mounted")
        for mount in mounts:
            if not mount.startswith("/mnt/th-usb-") or protected_mount(mount):
                raise ValueError(
                    "System, application or externally managed mount: refusing to unmount"
                )
        subprocess.run(["sync"], check=True)
        for mount in mounts:
            subprocess.run(["umount", mount], check=True)
    else:
        if mounts:
            raise ValueError("Partition is already mounted")
        if part["fstype"] not in [
            "ext4",
            "ext3",
            "ext2",
            "exfat",
            "vfat",
            "ntfs",
            "ntfs3",
        ]:
            raise ValueError("Filesystem not supported by this menu")
        suffix = re.sub(
            "[^a-zA-Z0-9-]", "", part.get("uuid") or pathlib.Path(device).name
        )
        target = pathlib.Path("/mnt/th-usb-" + suffix)
        if target.is_symlink():
            raise ValueError("Unsafe mountpoint")
        target.mkdir(mode=0o750, exist_ok=True)
        subprocess.run(["mount", "-o", "nosuid,nodev", device, str(target)], check=True)
        print(
            "Mounted:",
            target,
            "\nExisting files were preserved. This does not relocate application databases.",
        )


def snapshot(previous_host=None):
    shared = inventory_monitor.load()
    discovered = shared["inventory"] if shared else inventory.collect()
    measured = shared.get("host") if shared else None
    measured = measured or telemetry.sample(previous_host)
    if shared and time.time() - shared["at"] > 30:
        discovered = {
            **discovered,
            "errors": {
                **discovered["errors"],
                "monitor": "Last sample is older than 30 seconds",
            },
        }
    route = measured.get("route") or {}
    return {
        **measured,
        "host": measured,
        "inventory": discovered,
        "gateway": f"default via {route.get('gateway','--')} dev {route.get('dev','--')}",
        "connections": "\n".join(
            f"{r['name']}:{r['state']}" for r in measured.get("links", [])
        ),
        "tailscale": measured.get("vpn", {}).get("state", "Unavailable"),
        "peers": measured.get("vpn", {}).get("peers", []),
        "changes": shared["changes"] if shared else [],
        "inventory_at": shared["at"] if shared else measured["at"],
        "usb": block_devices(),
    }


class Monitor:
    def __init__(self):
        self.value = None
        self.error = None
        self.stop = threading.Event()
        self.refresh = threading.Event()
        self.events = []

    def loop(self):
        while not self.stop.is_set():
            try:
                value = snapshot(self.value.get("host") if self.value else None)
                self.events = (
                    inventory.changes(
                        self.value["inventory"] if self.value else None,
                        value["inventory"],
                    )
                    + self.events
                )[:100]
                value["changes"] = value.get("changes") or self.events
                self.value = value
                self.error = None
            except Exception as error:
                self.error = type(error).__name__
            self.refresh.wait(5)
            self.refresh.clear()


def lines_for(value, page):
    return [row.text for row in dashboard.page_rows(value, page)]


def admin_command(action, *args):
    script = str(pathlib.Path(__file__).resolve())
    if os.geteuid() != 0:
        return ["sudo", "--", "/usr/bin/python3", script, action, *args]
    # HDMI runs as root, but administrative changes require an existing user's
    # own sudo authentication. No fixed hostname or account is assumed.
    user = os.environ.get("TH_CONSOLE_ADMIN_USER", "")
    if not user or user == "root":
        raise ValueError(
            "Configure an administrator user before changing network or USB from HDMI"
        )
    pwd.getpwnam(user)
    return [
        "runuser",
        "-u",
        user,
        "--",
        "sudo",
        "--",
        "/usr/bin/python3",
        script,
        action,
        *args,
    ]


def set_cursor(visible):
    try:
        curses.curs_set(visible)
    except curses.error:
        pass


def console(screen):
    set_cursor(0)
    screen.timeout(500)
    screen.keypad(True)
    styles = {tone: 0 for tone in dashboard.TONES}
    styles.update(
        title=curses.A_BOLD,
        error=curses.A_BOLD,
        muted=curses.A_DIM,
        unknown=curses.A_DIM,
    )
    if curses.has_colors():
        try:
            curses.start_color()
            background = -1
            try:
                curses.use_default_colors()
            except curses.error:
                background = curses.COLOR_BLACK
            for index, (tone, colour) in enumerate(
                (
                    ("title", curses.COLOR_CYAN),
                    ("ok", curses.COLOR_GREEN),
                    ("warn", curses.COLOR_YELLOW),
                    ("error", curses.COLOR_RED),
                    ("info", curses.COLOR_CYAN),
                    ("muted", curses.COLOR_WHITE),
                    ("unknown", curses.COLOR_WHITE),
                ),
                1,
            ):
                curses.init_pair(index, colour, background)
                styles[tone] |= curses.color_pair(index)
        except curses.error:
            pass
    monitor = Monitor()
    threading.Thread(target=monitor.loop, daemon=True).start()
    page = 1
    offset = 0
    query = ""
    mode = "all"
    detail = False

    def write(y, x, text, style=0):
        height, width = screen.getmaxyx()
        if 0 <= y < height and x < width:
            try:
                screen.addnstr(
                    y, x, dashboard.clean(text), max(0, width - x - 1), style
                )
            except curses.error:
                pass

    def ask(prompt):
        screen.erase()
        write(1, 2, prompt)
        write(3, 2, "Nhap YES de tiep tuc, Enter de huy: ")
        screen.refresh()
        screen.timeout(-1)
        curses.echo()
        set_cursor(1)
        try:
            return screen.getstr(4, 2, 80).decode(errors="replace")
        finally:
            curses.noecho()
            set_cursor(0)
            screen.timeout(500)

    def privileged(action, *args):
        curses.def_prog_mode()
        curses.endwin()
        try:
            subprocess.run(admin_command(action, *args), check=False)
            input("\nPress Enter to return to TH-Server...")
        except (ValueError, KeyError) as error:
            print(str(error))
            input("Press Enter to return...")
        finally:
            curses.reset_prog_mode()
            screen.clear()
            monitor.refresh.set()

    try:
        while True:
            screen.erase()
            height, width = screen.getmaxyx()
            v = monitor.value
            rows, offset, total, visible = dashboard.frame(
                v, page, width, height, offset, query, mode, detail, monitor.error
            )
            for y, row in enumerate(rows):
                if row.text.startswith("OK=XANH  CAN XEM=VANG"):
                    x = 0
                    for text, tone in (
                        ("OK=XANH  ", "ok"),
                        ("CAN XEM=VANG  ", "warn"),
                        ("LOI=DO  ", "error"),
                        ("CHUA RO=XAM", "unknown"),
                    ):
                        write(y, x, text, styles[tone])
                        x += len(text)
                else:
                    write(y, 0, row.text, styles.get(row.tone, 0))
            screen.refresh()
            key = screen.getch()
            if key in map(ord, "123456789"):
                page = int(chr(key))
                offset = 0
                query = ""
                mode = "all"
            elif key in [ord("q"), ord("Q")]:
                break
            elif key in [9, curses.KEY_RIGHT, curses.KEY_LEFT]:
                page = (page - 1 + (-1 if key == curses.KEY_LEFT else 1)) % 9 + 1
                offset = 0
                query = ""
                mode = "all"
            elif key in [curses.KEY_DOWN, ord("j")]:
                offset += 1
            elif key in [curses.KEY_UP, ord("k")]:
                offset = max(0, offset - 1)
            elif key == curses.KEY_NPAGE:
                offset += visible
            elif key == curses.KEY_PPAGE:
                offset = max(0, offset - visible)
            elif key == curses.KEY_HOME:
                offset = 0
            elif key == curses.KEY_END:
                offset = total
            elif key in [ord("r"), ord("R")]:
                monitor.refresh.set()
            elif key in [ord("f"), ord("F")] and page in (5, 6):
                mode = ("all", "attention", "active")[
                    (("all", "attention", "active").index(mode) + 1) % 3
                ]
                offset = 0
            elif key in [ord("d"), ord("D")] and page in (5, 6):
                detail = not detail
                offset = 0
            elif key in [ord("c"), ord("C")] and page in (5, 6):
                query = ""
                offset = 0
            elif key == ord("/") and page in (5, 6):
                screen.erase()
                write(1, 2, "Tim ten ung dung / service (Enter trong de xoa):")
                screen.refresh()
                screen.timeout(-1)
                curses.echo()
                set_cursor(1)
                try:
                    query = screen.getstr(3, 2, 80).decode(errors="replace").strip()
                finally:
                    curses.noecho()
                    set_cursor(0)
                    screen.timeout(500)
                offset = 0
            elif key in [ord("n"), ord("N")]:
                if ask("Mo cau hinh mang? Co the ngat ket noi SSH.") == "YES":
                    privileged("--network")
            elif key in [ord("u"), ord("U"), ord("e"), ord("E")]:
                parts = block_devices()
                screen.erase()
                for i, p in enumerate(parts):
                    write(
                        i + 1,
                        2,
                        f"{i+1}: {p['path']} {bytes_text(p['size'])} {p['fstype']}",
                    )
                write(len(parts) + 3, 2, "Chon so phan vung (0 de huy):")
                screen.refresh()
                screen.timeout(-1)
                curses.echo()
                try:
                    choice = screen.getstr(len(parts) + 4, 2, 10).decode()
                finally:
                    curses.noecho()
                    screen.timeout(500)
                if choice.isdigit() and 0 < int(choice) <= len(parts):
                    device = parts[int(choice) - 1]["path"]
                    detach = key in [ord("e"), ord("E")]
                    if (
                        ask(
                            ("Unmount" if detach else "Mount")
                            + " "
                            + device
                            + "? Giu nguyen tep hien co."
                        )
                        == "YES"
                    ):
                        privileged("--unmount" if detach else "--mount", device)
            elif key in [ord("l"), ord("L")]:
                if not re.fullmatch(r"/dev/tty[0-9]+", os.ttyname(0)):
                    continue
                subprocess.run(
                    ["systemctl", "start", "getty@tty2.service"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                subprocess.run(["chvt", "2"], check=False)
    finally:
        monitor.stop.set()
        monitor.refresh.set()


def main():
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--snapshot", action="store_true")
    group.add_argument("--network", action="store_true")
    group.add_argument("--mount")
    group.add_argument("--unmount")
    args = parser.parse_args()
    if args.snapshot:
        print(json.dumps(snapshot()))
        return
    if args.network or args.mount or args.unmount:
        if os.geteuid() != 0:
            raise SystemExit("Administrator authentication is required")
        if args.network:
            if not shutil.which("nmtui"):
                raise SystemExit(
                    "NetworkManager nmtui is not installed. Install network-manager on the Pi."
                )
            subprocess.run(["nmtui"], check=True)
        else:
            usb_action(args.mount or args.unmount, bool(args.unmount))
        return
    if not os.isatty(0):
        raise SystemExit("Use an HDMI/SSH terminal (ssh -t) or --snapshot")
    if re.fullmatch(r"/dev/tty[0-9]+", os.ttyname(0)):
        os.write(1, b"\x1b[9;0]")
    curses.wrapper(console)


if __name__ == "__main__":
    main()
