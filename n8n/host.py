#!/usr/bin/env python3
"""Install narrowly scoped n8n swap and backup units through the trusted Docker helper."""
import json,os,pathlib,shutil,subprocess,sys
root=pathlib.Path('/host');source=pathlib.Path('/control');sha=sys.argv[1]
if len(sha)!=40 or any(c not in '0123456789abcdef' for c in sha):raise SystemExit('Expected immutable release')
if (root/'etc/hostname').read_text().strip()!='th-server':raise SystemExit('Unexpected server')
def host(*args):
 result=subprocess.run(['chroot','/host',*args],env={**os.environ,'SYSTEMD_IGNORE_CHROOT':'1'},capture_output=True,text=True)
 if result.returncode:raise RuntimeError('Host operation failed: '+args[0]+' '+result.stderr[:300])
 return result.stdout
base=root/'opt/th-server-n8n';release=base/'releases'/sha;release.mkdir(parents=True,exist_ok=True)
for path in source.glob('*'):
 if path.is_file():shutil.copyfile(path,release/path.name);(release/path.name).chmod(0o644)
current=base/'current';tmp=base/'current.new';tmp.unlink(missing_ok=True);tmp.symlink_to('releases/'+sha);tmp.replace(current)
# An additional disk swap is a fallback behind the existing zram/swap, not a replacement.
swap=root/'var/lib/thserver/n8n';swap.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
if not swap.exists():
 with open(swap,'xb') as stream:
  os.chmod(swap,0o600);os.posix_fallocate(stream.fileno(),0,2*1024**3);stream.flush();os.fsync(stream.fileno())
 host('/usr/sbin/mkswap','/var/lib/thserver/n8n')
elif swap.stat().st_size!=2*1024**3:raise RuntimeError('Unexpected existing swap file; not modified')
units={
'var-lib-thserver-n8n.swap':'''[Unit]
Description=TH-Server n8n fallback swap
[Swap]
What=/var/lib/thserver/n8n
Priority=5
[Install]
WantedBy=swap.target
''',
 'th-n8n-backup.service':'''[Unit]
Description=Verified n8n PostgreSQL and JSON backup to private Google Drive
After=docker.service network-online.target
ConditionPathExists=/home/thadmin/th-platform/state/n8n-deployment.json
[Service]
Type=oneshot
User=thadmin
SupplementaryGroups=docker
UMask=0077
ExecStart=/usr/bin/python3 /opt/th-server-n8n/current/backup.py
TimeoutStartSec=20min
Nice=10
''',
 'th-n8n-backup.timer':'''[Unit]
Description=n8n backup every 30 minutes
[Timer]
OnCalendar=*-*-* *:00,30:00
Persistent=true
Unit=th-n8n-backup.service
[Install]
WantedBy=timers.target
'''}
for name,content in units.items():(root/'etc/systemd/system'/name).write_text(content)
host('systemctl','daemon-reload');host('systemctl','enable','--now','var-lib-thserver-n8n.swap')
host('systemctl','enable','--now','th-n8n-backup.timer')
print(json.dumps({'fallbackSwapGiB':2,'n8nBackupTimer':True,'operatorRelease':sha}))
