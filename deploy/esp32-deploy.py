#!/usr/bin/env python3
"""Inspect retained ESP32 configuration without printing credential values."""
import json
import pathlib
import subprocess


def inspect():
    home = pathlib.Path.home()
    for directory in [pathlib.Path('/srv/th-esp32'), pathlib.Path('/srv/th-esp32/app'), home/'apps/esp32', home/'apps/th-esp32', home/'th-platform/apps/esp32']:
        try:
            entries = sorted(p.name for p in directory.iterdir())
        except OSError:
            entries = []
        print(json.dumps({'directory': str(directory), 'entries': entries}))
        path = directory/'.env'
        if path.is_file():
            names = []
            for line in path.read_text().splitlines():
                key, sep, value = line.partition('=')
                if sep and not key.lstrip().startswith('#') and value.strip().strip('\"\x27'):
                    names.append(key.strip())
            print(json.dumps({'environment': str(path), 'setNames': sorted(set(names))}))
    site = pathlib.Path('/etc/nginx/sites-available/esp.th.io.vn')
    if site.is_file():
        routing = [line.strip() for line in site.read_text().splitlines() if line.strip().startswith(('listen ', 'server_name ', 'proxy_pass '))]
        print(json.dumps({'nginxRouting': routing}))
    for command in [['docker','ps','-a','--format','{{.Names}} {{.Image}} {{.Status}}'], ['docker','volume','ls','--format','{{.Name}}'], ['systemctl','is-active','th-server-monitor.service','th-server-console.service','ssh','nginx','cloudflared']]:
        subprocess.run(command, check=True)


if __name__ == '__main__':
    inspect()
