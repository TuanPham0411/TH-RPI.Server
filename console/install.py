#!/usr/bin/env python3
"""Install the shared SSH/HDMI console on a local Debian/systemd host.

May run natively or from the transient Docker distribution with /host mounted.
No application deployment, hostname, user or network configuration is changed.
"""
import argparse
import json
import os
import pathlib
import re
import shutil
import subprocess
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('release', help='immutable full Git commit SHA')
    parser.add_argument('--root', default='/host' if pathlib.Path('/host').is_dir() else '/')
    parser.add_argument('--hdmi', action='store_true', help='replace tty1 getty with the console (explicit opt-in)')
    parser.add_argument('--admin-user', default=os.environ.get('SUDO_USER', ''), help='existing non-root sudo user for HDMI actions')
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.error('Run the installer with sudo/root on the target host')
    if not re.fullmatch('[a-f0-9]{40}', args.release):
        parser.error('Immutable full release SHA required')
    root = pathlib.Path(args.root).resolve()
    if not (root / 'usr/bin/python3').exists() or not (root / 'run/systemd/system').is_dir():
        parser.error('Target requires Python 3 and running systemd')
    if args.hdmi and not (root / 'dev/tty1').exists():
        parser.error('HDMI mode requires host /dev/tty1; use SSH-only mode on headless devices')
    if args.admin_user:
        if not re.fullmatch('[a-zA-Z_][a-zA-Z0-9_-]*[$]?', args.admin_user) or args.admin_user == 'root':
            parser.error('Choose an existing non-root administrator user')
        entries = (root / 'etc/passwd').read_text().splitlines()
        if not any(line.split(':')[0] == args.admin_user for line in entries):
            parser.error('Administrator user does not exist on target host')
    if args.hdmi and not args.admin_user:
        parser.error('--admin-user is required for authenticated HDMI administration')

    def host(*command, check=True):
        prefix = [] if root == pathlib.Path('/') else ['chroot', str(root)]
        return subprocess.run(prefix + list(command), env={**os.environ, 'SYSTEMD_IGNORE_CHROOT': '1'},
                              text=True, capture_output=True, check=check, timeout=30)

    base = root / 'opt/th-server-console'
    release = base / 'releases' / args.release
    current = base / 'current'
    unit = root / 'etc/systemd/system/th-server-console.service'
    launcher = root / 'usr/local/bin/th-server'
    monitor_unit = root / 'etc/systemd/system/th-server-monitor.service'
    monitor_active = host('systemctl', 'is-active', 'th-server-monitor.service', check=False).returncode == 0
    monitor_enabled = host('systemctl', 'is-enabled', 'th-server-monitor.service', check=False).returncode == 0
    # A native install also safely updates an already-enabled HDMI deployment.
    old_active = host('systemctl', 'is-active', 'th-server-console.service', check=False).returncode == 0
    old_enabled = host('systemctl', 'is-enabled', 'th-server-console.service', check=False).returncode == 0
    getty_enabled = host('systemctl', 'is-enabled', 'getty@tty1.service', check=False).returncode == 0
    getty_active = host('systemctl', 'is-active', 'getty@tty1.service', check=False).returncode == 0
    source = pathlib.Path(__file__).resolve().parent
    files = ('console.py', 'inventory.py', 'monitor.py', 'telemetry.py', 'dashboard.py', 'th-server-console.service', 'th-server-monitor.service')
    new_release = not release.exists()
    if not new_release:
        if release.is_symlink() or any(not (release / name).is_file() or (release / name).is_symlink() or (release / name).read_bytes() != (source / name).read_bytes() for name in files):
            raise SystemExit('Release contents differ; immutable releases are never overwritten')
    previous_link = os.readlink(current) if current.is_symlink() else None
    if current.exists() and not current.is_symlink():
        raise SystemExit('Unexpected current directory; refusing to replace it')
    previous = {path: path.read_bytes() if path.exists() else None for path in (unit, launcher, monitor_unit)}
    if any(path.is_symlink() for path in (unit, launcher, monitor_unit)):
        raise SystemExit('Refusing to overwrite a symlink for unit or launcher')
    release.mkdir(parents=True, mode=0o755, exist_ok=True)
    try:
        if new_release:
            for name in files:
                shutil.copyfile(source / name, release / name)
                (release / name).chmod(0o644)
        host('/usr/bin/python3', '-m', 'py_compile', f'/opt/th-server-console/releases/{args.release}/console.py',
             f'/opt/th-server-console/releases/{args.release}/inventory.py',
             f'/opt/th-server-console/releases/{args.release}/monitor.py')
        temporary = base / 'current.new'
        temporary.unlink(missing_ok=True)
        temporary.symlink_to('releases/' + args.release)
        temporary.replace(current)
        launcher.parent.mkdir(parents=True, exist_ok=True)
        launcher.write_text('#!/bin/sh\nexec /usr/bin/python3 /opt/th-server-console/current/console.py "$@"\n')
        launcher.chmod(0o755)
        shutil.copyfile(release / 'th-server-monitor.service', monitor_unit)
        monitor_unit.chmod(0o644)
        host('systemd-analyze', 'verify', '/etc/systemd/system/th-server-monitor.service')
        host('systemctl', 'daemon-reload')
        host('systemctl', 'enable', 'th-server-monitor.service')
        host('systemctl', 'restart', 'th-server-monitor.service')
        host('systemctl', 'is-active', 'th-server-monitor.service')
        if args.hdmi:
            text = (release / 'th-server-console.service').read_text()
            text = text.replace('Environment=TERM=', f'Environment=TH_CONSOLE_ADMIN_USER={args.admin_user}\nEnvironment=TERM=')
            unit.write_text(text)
            unit.chmod(0o644)
            host('systemd-analyze', 'verify', '/etc/systemd/system/th-server-console.service')
            host('systemctl', 'daemon-reload')
            host('systemctl', 'disable', 'getty@tty1.service')
            host('systemctl', 'enable', 'th-server-console.service')
            host('systemctl', 'restart', 'th-server-console.service')
        elif old_active:
            host('systemctl', 'restart', 'th-server-console.service')
        if args.hdmi or old_active:
            time.sleep(2)
            host('systemctl', 'is-active', 'th-server-console.service')
        print(json.dumps({'installed': True, 'release': args.release, 'sshCommand': 'th-server',
                          'monitor': True, 'hdmi': args.hdmi or old_active, 'hostnameChanged': False}))
    except Exception:
        for path, content in previous.items():
            if content is None:
                path.unlink(missing_ok=True)
            else:
                path.write_bytes(content)
                path.chmod(0o755 if path == launcher else 0o644)
        current.unlink(missing_ok=True)
        if previous_link:
            current.symlink_to(previous_link)
        host('systemctl', 'daemon-reload', check=False)
        host('systemctl', 'enable' if monitor_enabled else 'disable', 'th-server-monitor.service', check=False)
        host('systemctl', 'restart' if monitor_active else 'stop', 'th-server-monitor.service', check=False)
        if args.hdmi:
            host('systemctl', 'enable' if old_enabled else 'disable', 'th-server-console.service', check=False)
            host('systemctl', 'enable' if getty_enabled else 'disable', 'getty@tty1.service', check=False)
        host('systemctl', 'restart' if old_active else 'stop', 'th-server-console.service', check=False)
        if getty_active and not old_active:
            host('systemctl', 'start', 'getty@tty1.service', check=False)
        if new_release:
            shutil.rmtree(release)
        raise


if __name__ == '__main__':
    main()
