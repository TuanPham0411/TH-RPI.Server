"""Exercise real curses, keyboard input and resize in a local pseudo-terminal."""

import fcntl
import os
import pathlib
import pty
import select
import signal
import struct
import subprocess
import sys
import termios
import time
import unittest


class TerminalTests(unittest.TestCase):
    def start(self, term="xterm-256color"):
        master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 24, 80, 0, 0))
        code = "import curses,console;from test_dashboard import fixture;console.snapshot=lambda *args: fixture();curses.wrapper(console.console)"
        proc = subprocess.Popen(
            [sys.executable, "-c", code],
            cwd=pathlib.Path(__file__).parent,
            stdin=slave,
            stdout=slave,
            stderr=slave,
            env={**os.environ, "TERM": term},
        )
        os.close(slave)

        def cleanup():
            if proc.poll() is None:
                proc.kill()
            proc.wait(timeout=3)
            os.close(master)

        self.addCleanup(cleanup)
        return proc, master

    def output(self, master, seconds=0.65):
        result = b""
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if select.select([master], [], [], max(0, deadline - time.monotonic()))[0]:
                try:
                    result += os.read(master, 65536)
                except OSError:
                    break
        return result

    def ready(self, master):
        result=b"";deadline=time.monotonic()+5
        while b"CPU" not in result and time.monotonic()<deadline:
            result+=self.output(master,.2)
        return result

    def test_real_terminal_navigation_filter_detail_resize_and_clean_exit(self):
        proc, master = self.start()
        out = self.ready(master)
        self.assertIn(b"SIKA1", out)
        self.assertIn(b"CPU", out)
        os.write(master, b"6")
        out = self.output(master)
        self.assertIn(b"broken.service", out)
        os.write(master, b"/")
        self.output(master, 0.1)
        os.write(master, b"backup\n")
        out = self.output(master)
        self.assertIn(b"backup", out)
        os.write(master, b"cfd")
        out = self.output(master)
        self.assertIn(b"exit-code", out)
        os.write(master, b"8")
        out = self.output(master)
        self.assertIn(b"CAM KET SLA", out)
        fcntl.ioctl(master, termios.TIOCSWINSZ, struct.pack("HHHH", 16, 60, 0, 0))
        proc.send_signal(signal.SIGWINCH)
        out = self.output(master)
        self.assertNotIn(b"Traceback", out)
        os.write(master, b"q")
        proc.wait(timeout=3)
        self.assertEqual(proc.returncode, 0)

    def test_monochrome_terminal_still_has_explicit_status_labels(self):
        proc, master = self.start("vt100")
        out = self.ready(master)
        self.assertIn(b"SIKA1", out)
        os.write(master, b"6")
        out = self.output(master)
        self.assertIn(b"[LOI]", out)
        os.write(master, b"q")
        proc.wait(timeout=3)
        self.assertEqual(proc.returncode, 0)
