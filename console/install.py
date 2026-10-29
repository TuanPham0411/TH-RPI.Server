#!/usr/bin/env python3
"""Transient Docker distribution installer; installs only the HDMI console."""
import json, os, pathlib, re, shutil, subprocess, sys, time
sha=sys.argv[1] if len(sys.argv)==2 else ''
if not re.fullmatch('[a-f0-9]{40}',sha):raise SystemExit('Immutable full release SHA required')
root=pathlib.Path('/host');base=root/'opt/th-server-console';release=base/'releases'/sha
if (root/'etc/hostname').read_text().strip()!='th-server':raise SystemExit('Unexpected host')
if not (root/'usr/bin/python3').exists():raise SystemExit('Host Python3 is required')
def host(*args, check=True):
    return subprocess.run(['chroot','/host',*args],env={**os.environ,'SYSTEMD_IGNORE_CHROOT':'1'},text=True,capture_output=True,check=check)
old_active=host('systemctl','is-active','th-server-console.service',check=False).stdout.strip()=='active'
unit=root/'etc/systemd/system/th-server-console.service'
previous=unit.read_bytes() if unit.exists() else None
previous_link=os.readlink(base/'current') if (base/'current').is_symlink() else None
getty_enabled=host('systemctl','is-enabled','getty@tty1.service',check=False).returncode==0
release.mkdir(parents=True,exist_ok=True,mode=0o755)
for name in ['console.py','th-server-console.service']:
    shutil.copyfile('/app/console/'+name,release/name);(release/name).chmod(0o644)
try:
    host('/usr/bin/python3','-m','py_compile','/opt/th-server-console/releases/'+sha+'/console.py')
    temporary=base/'current.new';temporary.unlink(missing_ok=True);temporary.symlink_to('releases/'+sha);temporary.replace(base/'current')
    shutil.copyfile(release/'th-server-console.service',unit);unit.chmod(0o644)
    host('systemd-analyze','verify','/etc/systemd/system/th-server-console.service')
    host('systemctl','daemon-reload')
    host('systemctl','disable','getty@tty1.service')
    host('systemctl','enable','th-server-console.service')
    host('systemctl','restart','th-server-console.service')
    time.sleep(5)
    if host('systemctl','is-active','th-server-console.service',check=False).returncode!=0:raise RuntimeError('HDMI console did not become active')
    print(json.dumps({'console':'active','terminal':'tty1','release':sha,'passwordRequiredForChanges':True}))
except Exception:
    if previous is not None:unit.write_bytes(previous)
    else:unit.unlink(missing_ok=True)
    if previous_link:
        (base/'current').unlink(missing_ok=True);(base/'current').symlink_to(previous_link)
    host('systemctl','daemon-reload',check=False)
    if old_active:host('systemctl','restart','th-server-console.service',check=False)
    else:
        host('systemctl','stop','th-server-console.service',check=False)
        host('systemctl','disable','th-server-console.service',check=False)
        if getty_enabled:host('systemctl','enable','getty@tty1.service',check=False)
        host('systemctl','start','getty@tty1.service',check=False)
    raise SystemExit('Console installation failed; previous terminal restored. Server applications were not changed.')
