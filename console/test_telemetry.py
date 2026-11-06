import json
import unittest
from unittest.mock import patch

import telemetry as t


class TelemetryTests(unittest.TestCase):
    def test_probe_distinguishes_blocked_icmp_from_working_https_and_has_timeouts(self):
        def fake(args, timeout=2):
            if args[0] == "ping":
                return None
            if args[0] == "getent":
                return "192.0.2.1 www.google.com"
            if args[0] == "curl":
                self.assertIn("--max-time", args)
                self.assertIn("--proto", args)
                self.assertLessEqual(timeout, 4)
                return "204 0.125"

        with patch.object(t.shutil, "which", return_value="/tool"), patch.object(
            t, "run", side_effect=fake
        ):
            result = t.check_network({"gateway": "192.0.2.254", "dev": "eth0"})
        self.assertFalse(result["gateway"])
        self.assertTrue(result["https"])
        self.assertTrue(result["dns"])
        self.assertEqual(result["latency_ms"], 125)

    def test_unknown_routing_or_missing_tools_are_not_reported_as_online(self):
        with patch.object(t, "run") as run:
            unknown = t.check_network(None, False)
            run.assert_not_called()
            self.assertIsNone(unknown["https"])
        with patch.object(t.shutil, "which", return_value=None):
            result = t.check_network({"gateway": "192.0.2.254"})
            self.assertIsNone(result["https"])
            self.assertIsNone(result["dns"])
        self.assertFalse(t.check_network(None, True)["https"])

    def test_history_excludes_unknown_and_unobserved_gaps_from_success_denominator(
        self,
    ):
        value = {
            "history": [
                {"at": 100, "https": True, "latency_ms": 80},
                {"at": 130, "https": None},
                {"at": 160, "https": False},
                {"at": 500, "https": True, "latency_ms": 120},
                {"at": -5000, "https": False},
            ]
        }
        result = t.stability(value, now=520)
        self.assertEqual(result["checks"], 3)
        self.assertEqual(result["good"], 2)
        self.assertEqual(result["success_percent"], 66.7)
        self.assertEqual(result["average_ms"], 100)
        self.assertEqual(result["gaps"], 1)
        self.assertIsNone(t.stability({}, now=520)["success_percent"])

    def test_rate_counter_reset_and_first_measurement_are_unknown(self):
        link = [
            {
                "ifname": "eth0",
                "operstate": "UP",
                "flags": ["UP"],
                "addr_info": [{"scope": "global", "local": "192.0.2.3"}],
            }
        ]
        with patch.object(t, "data", return_value=link), patch.object(
            t, "read", return_value="100"
        ):
            first = t.interfaces([], None)[0]
            self.assertIsNone(first["rates"]["rx"])
        with patch.object(t, "data", return_value=link), patch.object(
            t, "read", return_value="50"
        ):
            reset = t.interfaces([first], 5)[0]
            self.assertIsNone(reset["rates"]["rx"])
        with patch.object(t, "data", return_value=link), patch.object(
            t, "read", return_value="200"
        ):
            next_sample = t.interfaces([first], 5)[0]
            self.assertEqual(next_sample["rates"]["rx"], 20)

    def test_probe_interval_cpu_reboot_and_persistent_observation(self):
        state = {"boot": "boot1", "cpu": "cpu 100 0 100 800 0 0 0 0", "rx": 100}

        def read(path, default=""):
            values = {
                "/proc/meminfo": "MemTotal: 1024 kB\nMemAvailable: 512 kB",
                "/proc/stat": state["cpu"],
                "/proc/uptime": "1000 200",
                "/proc/sys/kernel/random/boot_id": state["boot"],
                "/sys/class/thermal/thermal_zone0/temp": "42000",
                "/etc/resolv.conf": "nameserver 192.0.2.254",
            }
            return values.get(path, default)

        def data(args):
            return (
                [] if "address" in args else [{"gateway": "192.0.2.254", "dev": "eth0"}]
            )

        with patch.object(t, "read", side_effect=read), patch.object(
            t, "data", side_effect=data
        ), patch.object(
            t, "run", return_value=json.dumps({"BackendState": "Running", "Peer": {}})
        ), patch.object(
            t.shutil, "which", return_value=None
        ), patch.object(
            t,
            "check_network",
            return_value={"at": 0, "https": True, "dns": True, "latency_ms": 10},
        ) as probe:
            first = t.sample(now=100)
            state["cpu"] = "cpu 120 0 100 880 0 0 0 0"
            second = t.sample(first, now=105)
            self.assertEqual(probe.call_count, 1)
            self.assertEqual(second["cpu"], 20)
            self.assertEqual(len(second["history"]), 1)
            third = t.sample(second, now=131)
            self.assertEqual(probe.call_count, 2)
            self.assertEqual(len(third["history"]), 2)
            state["boot"] = "boot2"
            reboot = t.sample(third, now=135)
            self.assertIsNone(reboot["cpu"])
            self.assertEqual(reboot["observed_reboots"], 1)
            self.assertEqual(reboot["observed_since"], 100)
            self.assertEqual(probe.call_count, 3)
            self.assertLessEqual(len(t.sample(reboot, now=5000)["history"]), 121)

    def test_ipv6_only_default_route_is_measured(self):
        def data(args):
            if "address" in args:
                return []
            return [{"gateway": "fe80::1", "dev": "eth0"}] if "-6" in args else []

        with patch.object(t, "data", side_effect=data), patch.object(
            t, "check_network", return_value={"https": True}
        ) as probe, patch.object(t, "run", return_value=None), patch.object(
            t.shutil, "which", return_value=None
        ):
            result = t.sample(now=100)
            self.assertEqual(result["route"]["gateway"], "fe80::1")
            self.assertEqual(probe.call_args.args[0]["dev"], "eth0")
