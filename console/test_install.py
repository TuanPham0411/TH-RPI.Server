import contextlib
import importlib.util
import io
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('installer', pathlib.Path(__file__).with_name('install.py'))
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)
SHA = 'a' * 40


class InstallTests(unittest.TestCase):
    def target(self, directory):
        root = pathlib.Path(directory)
        for path in ('usr/bin', 'etc/systemd/system', 'run/systemd/system', 'dev'):
            (root / path).mkdir(parents=True, exist_ok=True)
        (root / 'usr/bin/python3').touch()
        (root / 'etc/passwd').write_text('operator:x:1000:1000::/home/operator:/bin/bash\n')
        return root

    def invoke(self, root, extra=(), fail=None):
        calls = []

        def host(args, **kwargs):
            command = args[2:] if args[0] == 'chroot' else args
            calls.append(command)
            if fail and command == fail and kwargs.get('check'):
                raise subprocess.CalledProcessError(1, command)
            status = 3 if command[:2] in (['systemctl', 'is-active'], ['systemctl', 'is-enabled']) and not kwargs.get('check') else 0
            return subprocess.CompletedProcess(args, status, '', '')

        with patch.object(sys, 'argv', ['install.py', SHA, '--root', str(root), *extra]), patch.object(installer.os, 'geteuid', return_value=0), patch.object(installer.subprocess, 'run', side_effect=host), patch.object(installer.time, 'sleep'), patch.dict(installer.os.environ, {'SUDO_USER': ''}), contextlib.redirect_stdout(io.StringIO()):
            installer.main()
        return calls

    def test_install_arbitrary_host_ssh_and_monitor_without_taking_tty(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.target(directory)
            calls = self.invoke(root)
            self.assertTrue((root / 'usr/local/bin/th-server').exists())
            self.assertEqual((root / 'opt/th-server-console/current').readlink(), pathlib.Path('releases') / SHA)
            self.assertIn(['systemctl', 'restart', 'th-server-monitor.service'], calls)
            self.assertNotIn(['systemctl', 'disable', 'getty@tty1.service'], calls)
            self.assertFalse((root / 'etc/systemd/system/th-server-console.service').exists())
            # Reinstalling the same release does not corrupt or rewrite its contents.
            self.invoke(root)

    def test_hdmi_requires_real_target_tty_and_administrator(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.target(directory)
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                self.invoke(root, ['--hdmi', '--admin-user', 'operator'])
            (root / 'dev/tty1').touch()
            calls = self.invoke(root, ['--hdmi', '--admin-user', 'operator'])
            self.assertIn(['systemctl', 'disable', 'getty@tty1.service'], calls)
            self.assertIn('TH_CONSOLE_ADMIN_USER=operator', (root / 'etc/systemd/system/th-server-console.service').read_text())

    def test_failed_install_restores_previous_release_unit_and_launcher(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.target(directory)
            base = root / 'opt/th-server-console'
            (base / 'releases/old').mkdir(parents=True)
            (base / 'current').symlink_to('releases/old')
            unit = root / 'etc/systemd/system/th-server-monitor.service'
            unit.write_text('old monitor unit')
            launcher = root / 'usr/local/bin/th-server'
            launcher.parent.mkdir(parents=True)
            launcher.write_text('old launcher')
            with self.assertRaises(subprocess.CalledProcessError):
                self.invoke(root, fail=['systemctl', 'restart', 'th-server-monitor.service'])
            self.assertEqual((base / 'current').readlink(), pathlib.Path('releases/old'))
            self.assertEqual(unit.read_text(), 'old monitor unit')
            self.assertEqual(launcher.read_text(), 'old launcher')
            self.assertFalse((base / 'releases' / SHA).exists())


if __name__ == '__main__':
    unittest.main()
