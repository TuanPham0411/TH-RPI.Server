#!/usr/bin/env python3
"""Inspect retained ESP32 configuration without printing credential values."""
import json
import pathlib
import subprocess
import argparse
import fcntl
import os
import re
import urllib.request

HOME = pathlib.Path.home()/'th-platform'
STATE = HOME/'state/esp32-deployment'
HELPER_IMAGE = 'ghcr.io/tuanpham0411/th-rpi-console:99a1c43518ac4f70165771c7ecb07c60268c8d11'


def run(command, env=None):
    result = subprocess.run(command, env=env, capture_output=True)
    if result.returncode:
        STATE.mkdir(parents=True, exist_ok=True, mode=0o700)
        diagnostic = STATE/'last-command-error.log'
        diagnostic.write_bytes(result.stderr)
        diagnostic.chmod(0o600)
        raise RuntimeError('Command failed; diagnostic retained privately: '+command[0])
    return result.stdout


def deploy(sha):
    if not re.fullmatch('[a-f0-9]{40}', sha):
        raise RuntimeError('A full published image SHA is required')
    environment = pathlib.Path('/srv/th-esp32/app/.env')
    if not environment.is_file():
        raise RuntimeError('Retained ESP32 environment is missing')
    STATE.mkdir(parents=True, exist_ok=True, mode=0o700)
    release = HOME/'apps/esp32/releases'/sha
    release.mkdir(parents=True, exist_ok=True, mode=0o700)
    for name in ('compose.yaml', 'tools/smoke.py'):
        with urllib.request.urlopen('https://raw.githubusercontent.com/TuanPham0411/TH-ESP32/'+sha+'/'+name, timeout=30) as response:
            path = release/name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(response.read())
    runtime = {**os.environ, 'APP_IMAGE':'ghcr.io/tuanpham0411/th-esp32:'+sha,
               'PUBLIC_URL':'https://esp.th.io.vn', 'GOOGLE_REDIRECT_URI':'https://esp.th.io.vn/auth/callback',
               'BIND_ADDRESS':'127.0.0.1', 'PORT':'5081', 'TRUST_PROXY':'1'}
    compose = ['docker','compose','-p','th-esp32','--env-file',str(environment),'-f',str(release/'compose.yaml')]
    run(compose+['config','--quiet'], runtime)
    print(json.dumps({'phase':'pull-tested-esp32-image','release':sha}), flush=True)
    run(compose+['pull','web'], runtime)
    print(json.dumps({'phase':'start-esp32','volume':'th-esp32_data','bind':'127.0.0.1:5081'}), flush=True)
    run(compose+['up','-d','--no-build','--wait','--wait-timeout','180'], runtime)
    run(['python3',str(release/'tools/smoke.py')])
    health = json.loads(run(['curl','--fail','--silent','--show-error','--max-time','20','http://127.0.0.1:5081/healthz']))
    if health.get('service')!='th-esp32' or health.get('revision')!=sha:
        raise RuntimeError('Running ESP32 image does not match the selected release')
    print(json.dumps({'phase':'local-smoke-passed','firmwareChecksumVerified':True,'publicTools':8}), flush=True)
    source = pathlib.Path(__file__).resolve().parent/'esp32-nginx.py'
    backup = STATE/'nginx'
    backup.mkdir(exist_ok=True, mode=0o700)
    route = ['docker','run','--rm','--network','none','--pid','host','--user','0',
             '--cap-drop','ALL','--cap-add','SYS_CHROOT','--cap-add','DAC_OVERRIDE','--cap-add','CHOWN','--cap-add','FOWNER',
             '--mount','type=bind,src=/,dst=/host,readonly',
             '--mount','type=bind,src=/etc/nginx/sites-enabled,dst=/host/etc/nginx/sites-enabled',
             '--tmpfs','/host/run:rw,nosuid,nodev,noexec,size=16m',
             '--mount','type=bind,src=/run/systemd,dst=/host/run/systemd',
             '--mount','type=bind,src=/run/dbus,dst=/host/run/dbus,readonly',
             '--tmpfs','/host/var/log/nginx:rw,nosuid,nodev,noexec,size=16m',
             '--tmpfs','/host/var/lib/nginx:rw,nosuid,nodev,noexec,size=16m',
             '--mount','type=bind,src='+str(source)+',dst=/control/esp32-nginx.py,readonly',
             '--mount','type=bind,src='+str(backup)+',dst=/backup',HELPER_IMAGE,'python3','/control/esp32-nginx.py']
    print(json.dumps({'phase':'enable-esp32-route'}), flush=True)
    run(route)
    try:
        def public(path):
            return run(['curl','--fail','--silent','--show-error','--max-time','60','https://esp.th.io.vn'+path])
        if json.loads(public('/healthz'))!=health:
            raise RuntimeError('Public ESP32 health/release differs')
        for path in ('/','/downloads','/install','/tools'):
            html = public(path).lower()
            if b'<!doctype html' not in html or b'welcome to nginx' in html:
                raise RuntimeError('Public ESP32 page failed verification')
        if len(json.loads(public('/api/xiaozhi/tools/list'))['tools'])!=8:
            raise RuntimeError('Public ESP32 tool catalog differs')
        import hashlib
        manifest = json.loads(public('/static/xiaozhi/manifest.json'))
        firmware = public('/static/xiaozhi/'+manifest['builds'][0]['parts'][0]['path'])
        if hashlib.sha256(firmware).hexdigest()!='c8e77c6e00c9e38dcd8c0b48ee637a8c37d8aa376dbcfa7ac51cd2bad25bba5a':
            raise RuntimeError('Public ESP32 firmware checksum differs')
    except Exception:
        run(route+['rollback'])
        raise RuntimeError('Public ESP32 verification failed; prior route restored, volume retained')
    metadata = {'application':'esp32','imageTag':sha,'release':str(release),'ready':True,'publicHealthVerified':True,'firmwareChecksumVerified':True}
    (STATE/'deployment.json').write_text(json.dumps(metadata))
    print(json.dumps(metadata), flush=True)


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
        code = "import pathlib,json;print(json.dumps({'nginxRouting':[s.strip() for s in pathlib.Path('/site').read_text().splitlines() if s.strip().startswith(('listen ','server_name ','proxy_pass '))]}))"
        subprocess.run(['docker','run','--rm','--network','none','--read-only','--user','0','--cap-drop','ALL','--cap-add','DAC_OVERRIDE','--mount','type=bind,src='+str(site)+',dst=/site,readonly','ghcr.io/tuanpham0411/th-rpi-console:99a1c43518ac4f70165771c7ecb07c60268c8d11','python3','-c',code],check=True)
    for command in [['docker','ps','-a','--format','{{.Names}} {{.Image}} {{.Status}}'], ['docker','volume','ls','--format','{{.Name}}'], ['systemctl','is-active','th-server-monitor.service','th-server-console.service','ssh','nginx','cloudflared']]:
        subprocess.run(command, check=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--deploy',action='store_true')
    parser.add_argument('--image-sha')
    args = parser.parse_args()
    os.umask(0o077)
    if args.deploy:
        if os.getuid()!=1000:
            raise SystemExit('Use the existing management runner user, UID 1000')
        HOME.mkdir(parents=True,exist_ok=True)
        try:
            with open(HOME/'deploy.lock','a') as lock:
                fcntl.flock(lock,fcntl.LOCK_EX)
                deploy(args.image_sha or '')
        except Exception as error:
            raise SystemExit(str(error))
    else:
        inspect()
