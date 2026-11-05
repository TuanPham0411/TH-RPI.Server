#!/usr/bin/env python3
"""Enable only the retained Portfolio route, with rollback on Nginx errors."""
import json
import os
import pathlib
import subprocess
import sys

ROOT = pathlib.Path('/host')
ENABLED = ROOT / 'etc/nginx/sites-enabled/th-portfolio'
AVAILABLE = ROOT / 'etc/nginx/sites-available/th-portfolio'
BACKUP = pathlib.Path('/backup/portfolio-route.json')


def host(*args):
    subprocess.run(['chroot', str(ROOT), *args], check=True, capture_output=True,
                   env={**os.environ, 'SYSTEMD_IGNORE_CHROOT': '1'}, timeout=30)


def restore():
    value = json.loads(BACKUP.read_text())
    ENABLED.unlink(missing_ok=True)
    if value['link'] is not None:
        ENABLED.symlink_to(value['link'])
    host('/usr/sbin/nginx', '-t')
    host('systemctl', 'reload', 'nginx')


def main():
    if sys.argv[1:] == ['rollback']:
        restore()
        print(json.dumps({'portfolioRouteRolledBack': True}))
        return
    text = AVAILABLE.read_text()
    if 'server_name th.io.vn;' not in text or 'proxy_pass http://127.0.0.1:8080;' not in text:
        raise RuntimeError('Retained Portfolio route does not match the expected domain and loopback port')
    if ENABLED.exists() and not ENABLED.is_symlink():
        raise RuntimeError('Refusing to replace a non-symlink Nginx site')
    BACKUP.write_text(json.dumps({'link': os.readlink(ENABLED) if ENABLED.is_symlink() else None}))
    BACKUP.chmod(0o600)
    try:
        ENABLED.unlink(missing_ok=True)
        ENABLED.symlink_to('/etc/nginx/sites-available/th-portfolio')
        host('/usr/sbin/nginx', '-t')
        host('systemctl', 'reload', 'nginx')
    except Exception:
        restore()
        raise
    print(json.dumps({'portfolioRouteEnabled': True, 'domain': 'th.io.vn'}))


if __name__ == '__main__':
    main()
