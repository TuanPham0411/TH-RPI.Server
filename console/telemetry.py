"""Bounded, read-only host/network measurements shared by all console viewers."""

import datetime
import json
import os
import pathlib
import shutil
import socket
import subprocess
import time

PROBE_URL = "https://www.google.com/generate_204"
PROBE_INTERVAL = 30
WINDOW = 3600


def read(path, default=""):
    try:
        return pathlib.Path(path).read_text().strip()
    except OSError:
        return default


def run(args, timeout=2):
    try:
        result = subprocess.run(
            args, text=True, capture_output=True, timeout=timeout, check=False
        )
        return result.stdout.strip() if result.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def data(args):
    try:
        value = json.loads(run(args))
        return value if isinstance(value, list) else None
    except (ValueError, TypeError):
        return None


def interfaces(previous, elapsed):
    values = data(["ip", "-j", "address"])
    if values is None:
        return None
    old = {row["name"]: row for row in previous or []}
    rows = []
    for item in values:
        name = item.get("ifname", "")
        if name == "lo":
            continue
        counters = {}
        for key in (
            "rx_bytes",
            "tx_bytes",
            "rx_errors",
            "tx_errors",
            "rx_dropped",
            "tx_dropped",
        ):
            raw = read("/sys/class/net/" + name + "/statistics/" + key)
            counters[key] = int(raw) if raw.isdigit() else None
        rates = {}
        for direction in ("rx", "tx"):
            key = direction + "_bytes"
            before = old.get(name, {}).get("counters", {}).get(key)
            after = counters[key]
            rates[direction] = (
                round((after - before) / elapsed, 1)
                if elapsed
                and elapsed > 0
                and before is not None
                and after is not None
                and after >= before
                else None
            )
        addresses = [
            a["local"]
            for a in item.get("addr_info", [])
            if a.get("scope") == "global" and a.get("local")
        ]
        rows.append(
            {
                "name": name,
                "state": item.get("operstate", "UNKNOWN"),
                "up": "UP" in item.get("flags", []),
                "addresses": addresses,
                "carrier": read("/sys/class/net/" + name + "/carrier", "?"),
                "counters": counters,
                "rates": rates,
            }
        )
    return rows


def check_network(route, route_known=True):
    result = {
        "at": time.time(),
        "target": PROBE_URL,
        "gateway": None,
        "dns": None,
        "https": None,
        "latency_ms": None,
        "http_code": None,
    }
    if not route_known:
        result["note"] = "Routing discovery unavailable"
        return result
    if not route:
        result["note"] = "No default route"
        result["https"] = False
        return result
    gateway = route.get("gateway")
    if gateway and shutil.which("ping"):
        result["gateway"] = (
            run(["ping", "-n", "-c", "1", "-W", "1", gateway]) is not None
        )
    if shutil.which("getent"):
        result["dns"] = bool(run(["getent", "ahosts", "www.google.com"]))
    if shutil.which("curl"):
        response = run(
            [
                "curl",
                "--silent",
                "--output",
                "/dev/null",
                "--proto",
                "=https",
                "--connect-timeout",
                "2",
                "--max-time",
                "3",
                "--write-out",
                "%{http_code} %{time_total}",
                PROBE_URL,
            ],
            timeout=4,
        )
        result["https"] = False
        if response:
            try:
                code, seconds = response.split()
                result.update(
                    https=code == "204",
                    http_code=int(code),
                    latency_ms=round(float(seconds) * 1000),
                )
            except (ValueError, TypeError):
                pass
    else:
        result["note"] = "curl unavailable; HTTPS not measured"
    return result


def sample(previous=None, now=None):
    now = time.time() if now is None else now
    previous = previous or {}
    boot = read("/proc/sys/kernel/random/boot_id")
    same_boot = bool(boot and boot == previous.get("boot_id"))
    elapsed = now - previous.get("at", now) if same_boot else None
    errors = {}
    memory = {}
    for line in read("/proc/meminfo").splitlines():
        key, value = line.split(":", 1)
        memory[key] = int(value.strip().split()[0]) * 1024
    stat = read("/proc/stat").splitlines()
    ticks = [int(n) for n in stat[0].split()[1:9]] if stat else []
    old_ticks = previous.get("ticks", []) if same_boot else []
    cpu = None
    if len(ticks) >= 5 and len(old_ticks) == len(ticks):
        total = sum(ticks) - sum(old_ticks)
        idle = ticks[3] + ticks[4] - old_ticks[3] - old_ticks[4]
        if total > 0 and 0 <= idle <= total:
            cpu = round(100 * (1 - idle / total), 1)
    links = interfaces(previous.get("links") if same_boot else [], elapsed)
    if links is None:
        errors["links"] = "IP/interface discovery unavailable"
    routes = data(["ip", "-j", "route", "show", "default"])
    if routes == []:
        routes = data(["ip", "-j", "-6", "route", "show", "default"])
    if routes is None:
        errors["route"] = "Default route discovery unavailable"
    route = min(routes, key=lambda r: r.get("metric", 0)) if routes else None
    probe = previous.get("probe", {}) if same_boot else {}
    if (
        not probe
        or now - probe.get("at", 0) >= PROBE_INTERVAL
        or now < probe.get("at", 0)
    ):
        probe = check_network(route, routes is not None)
        probe["at"] = now
    vpn = previous.get("vpn", {}) if same_boot else {}
    if not vpn or now - vpn.get("at", 0) >= PROBE_INTERVAL or now < vpn.get("at", 0):
        raw = run(["tailscale", "status", "--json"])
        try:
            info = json.loads(raw)
            vpn = {
                "at": now,
                "state": info.get("BackendState", "Unknown"),
                "peers": [
                    {
                        "name": p.get("HostName", "?"),
                        "online": p.get("Online", False),
                        "ip": next(iter(p.get("TailscaleIPs") or []), "-"),
                    }
                    for p in info.get("Peer", {}).values()
                ],
            }
        except (ValueError, TypeError, AttributeError):
            vpn = {"at": now, "state": "Unavailable", "peers": []}
    temperature = read("/sys/class/thermal/thermal_zone0/temp")
    temperature = round(float(temperature) / 1000, 1) if temperature else None
    throttled = run(["vcgencmd", "get_throttled"]) if shutil.which("vcgencmd") else None
    try:
        throttle_flags = int(throttled.split("=")[1], 16) if throttled else None
    except (ValueError, IndexError):
        throttle_flags = None
    disk = shutil.disk_usage("/")
    try:
        load = list(os.getloadavg())
    except OSError:
        load = []
    history = [
        r for r in previous.get("history", []) if now - WINDOW <= r.get("at", 0) <= now
    ]
    if not history or probe["at"] != history[-1]["at"]:
        history.append(
            {
                "at": probe["at"],
                "https": probe.get("https"),
                "dns": probe.get("dns"),
                "gateway": probe.get("gateway"),
                "latency_ms": probe.get("latency_ms"),
                "cpu": cpu,
                "boot_id": boot,
            }
        )
    reboot_count = previous.get("observed_reboots", 0) + int(
        bool(previous.get("boot_id") and boot and not same_boot)
    )
    return {
        "at": now,
        "hostname": socket.gethostname(),
        "boot_id": boot,
        "uptime": int(float(read("/proc/uptime", "0").split()[0])),
        "monotonic_uptime": time.monotonic(),
        "cpu": cpu,
        "ticks": ticks,
        "cores": os.cpu_count() or 1,
        "load": load,
        "memory": memory,
        "disk": {"total": disk.total, "free": disk.free},
        "temperature": temperature,
        "throttle_flags": throttle_flags,
        "links": links or [],
        "route": route,
        "probe": probe,
        "vpn": vpn,
        "dns": [
            line
            for line in read("/etc/resolv.conf").splitlines()
            if line.startswith("nameserver")
        ],
        "history": history[-121:],
        "observed_reboots": reboot_count,
        "observed_since": previous.get("observed_since", now),
        "errors": errors,
    }


def stability(value, now=None):
    now = time.time() if now is None else now
    history = [
        r for r in value.get("history", []) if now - WINDOW <= r.get("at", 0) <= now
    ]
    measured = [r for r in history if type(r.get("https")) is bool]
    good = sum(r["https"] for r in measured)
    latencies = [
        r["latency_ms"]
        for r in measured
        if r.get("https") and r.get("latency_ms") is not None
    ]
    transitions = sum(
        a["https"] != b["https"]
        for a, b in zip(measured, measured[1:])
        if b["at"] - a["at"] <= 2 * PROBE_INTERVAL
    )
    gaps = sum(
        b["at"] - a["at"] > 2 * PROBE_INTERVAL for a, b in zip(history, history[1:])
    )
    return {
        "checks": len(measured),
        "good": good,
        "bad": len(measured) - good,
        "success_percent": round(100 * good / len(measured), 1) if measured else None,
        "average_ms": round(sum(latencies) / len(latencies)) if latencies else None,
        "max_ms": max(latencies) if latencies else None,
        "transitions": transitions,
        "gaps": gaps,
        "span_seconds": max(0, history[-1]["at"] - history[0]["at"]) if history else 0,
    }
