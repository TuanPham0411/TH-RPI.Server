import copy
import time
import unittest

import dashboard as d


def fixture():
    now = time.time()
    return {
        "at": now,
        "inventory_at": now,
        "host": {
            "hostname": "SIKA1",
            "at": now,
            "uptime": 12345,
            "monotonic_uptime": 12345,
            "cpu": 32,
            "cores": 4,
            "memory": {"MemTotal": 1024**3, "MemAvailable": 512 * 1024**2},
            "disk": {"total": 32 * 1024**3, "free": 20 * 1024**3},
            "load": [0.2, 0.4, 0.3],
            "links": [
                {
                    "name": "eth0",
                    "up": True,
                    "state": "UP",
                    "addresses": ["192.168.1.10"],
                },
                {
                    "name": "tailscale0",
                    "state": "UNKNOWN",
                    "up": True,
                    "addresses": ["100.90.1.2"],
                },
            ],
            "route": {"gateway": "192.168.1.1", "dev": "eth0"},
            "probe": {
                "at": now,
                "https": True,
                "dns": True,
                "gateway": False,
                "latency_ms": 42,
            },
            "history": [],
            "errors": {},
        },
        "inventory": {
            "errors": {},
            "containers": [
                {
                    "id": "c1",
                    "name": "camera",
                    "state": "running",
                    "health": "not-configured",
                    "image": "camera:1",
                    "ports": {},
                }
            ],
            "images": [],
            "services": [
                {
                    "id": "not-failed.service",
                    "description": "Harmless",
                    "load": "loaded",
                    "state": "inactive",
                    "sub": "dead",
                    "enabled": "disabled",
                    "pid": 0,
                },
                {
                    "id": "backup.service",
                    "load": "loaded",
                    "state": "active",
                    "sub": "exited",
                    "enabled": "enabled",
                    "pid": 0,
                },
                {
                    "id": "broken.service",
                    "load": "loaded",
                    "state": "failed",
                    "sub": "failed",
                    "enabled": "enabled",
                    "pid": 0,
                    "result": "exit-code",
                },
            ],
        },
        "usb": [],
        "changes": [],
    }


class DashboardTests(unittest.TestCase):
    def test_status_is_typed_and_inactive_is_not_a_false_failure(self):
        rows = fixture()["inventory"]["services"]
        self.assertEqual(d.service_status(rows[0]).tone, "muted")
        self.assertEqual(d.service_status(rows[1]).label, "HOAN TAT")
        self.assertEqual(d.service_status(rows[2]).tone, "error")
        self.assertEqual(
            d.container_status(fixture()["inventory"]["containers"][0]).tone, "info"
        )
        self.assertEqual(
            d.container_status({"state": "exited", "exit_code": 137}).tone, "error"
        )
        self.assertEqual(
            d.container_status({"state": "exited", "exit_code": 0}).tone, "muted"
        )

    def test_attention_filter_and_search_keep_real_failures_visible(self):
        value = fixture()
        text = "\n".join(r.text for r in d.page_rows(value, 6, mode="attention"))
        self.assertIn("broken.service", text)
        self.assertNotIn("not-failed.service", text)
        text = "\n".join(r.text for r in d.page_rows(value, 6, query="backup"))
        self.assertIn("backup.service", text)
        self.assertNotIn("broken.service", text)

    def test_overview_fits_tty_with_ips_and_network_state_visible(self):
        value = fixture()
        rows, offset, total, visible = d.frame(value, 1, 80, 24)
        self.assertEqual(len(rows), 24)
        self.assertTrue(all(len(r.text) <= 79 for r in rows))
        text = "\n".join(r.text for r in rows)
        for token in (
            "SIKA1",
            "192.168.1.10",
            "100.90.1.2",
            "CPU",
            "RAM",
            "HTTPS",
            "broken.service",
        ):
            self.assertIn(token, text)
        self.assertTrue(
            any(r.tone == "error" and "broken.service" in r.text for r in rows)
        )

    def test_all_pages_resize_scroll_and_missing_host_data_are_safe(self):
        for width, height in ((40, 12), (80, 24), (120, 36)):
            for page in range(1, 10):
                rows, offset, total, visible = d.frame(
                    fixture(), page, width, height, offset=99999
                )
                self.assertEqual(len(rows), height)
                self.assertTrue(all(len(r.text) <= width - 1 for r in rows))
                self.assertGreaterEqual(offset, 0)
        for value in (
            None,
            {
                "inventory": {
                    "errors": {},
                    "containers": [],
                    "images": [],
                    "services": [],
                }
            },
        ):
            for page in range(1, 10):
                d.frame(value, page, 80, 24)

    def test_stale_and_unknown_data_are_never_reported_as_fresh(self):
        value = fixture()
        value["inventory_at"] -= 60
        rows, *_ = d.frame(value, 1, 80, 24)
        self.assertIn("CU / CHUA XAC MINH", rows[0].text)
        value["inventory"]["errors"]["containers"] = "denied"
        rows = d.page_rows(value, 5)
        self.assertIn("DISCOVERY UNAVAILABLE", rows[0].text)
        self.assertTrue(any(r.tone == "unknown" and "camera" in r.text for r in rows))

    def test_untrusted_labels_cannot_emit_terminal_escape_sequences(self):
        value = fixture()
        value["host"]["hostname"] = "bad\x1b[31m\nname"
        rows, *_ = d.frame(value, 1, 80, 24)
        self.assertNotIn("\x1b", "".join(r.text for r in rows))
        self.assertNotIn("\n", "".join(r.text for r in rows))

    def test_network_checks_and_stability_are_distinct_from_gateway_ping(self):
        value = fixture()
        rows = d.page_rows(value, 2)
        https = next(r for r in rows if "HTTPS /" in r.text)
        ping = next(r for r in rows if "ICMP gateway" in r.text)
        self.assertEqual(https.tone, "ok")
        self.assertEqual(ping.tone, "warn")
        rows = d.page_rows(value, 8)
        self.assertIn("CHUA DU LIEU", "\n".join(r.text for r in rows))
