#!/usr/bin/env python3
"""Retire n8n without deleting databases, credentials, volumes or Drive backups."""
import os,pathlib,signal,subprocess,time,json,fcntl
home=pathlib.Path.home()/'th-platform'
if os.getuid()!=1000:raise SystemExit('Expected thadmin UID 1000')
for entry in pathlib.Path('/proc').iterdir():
 if not entry.name.isdigit():continue
 try:
  args=(entry/'cmdline').read_bytes().split(b'\0')
  if len(args)>1 and b'th-n8n-resume.' in args[1] and args[1].endswith(b'/deploy.py'):
   os.kill(int(entry.name),signal.SIGTERM)
 except (FileNotFoundError,PermissionError,ProcessLookupError):pass
lock=open(home/'deploy.lock','a');fcntl.flock(lock,fcntl.LOCK_EX)
script="""import os,subprocess
env={**os.environ,'SYSTEMD_IGNORE_CHROOT':'1'}
subprocess.run(['chroot','/host','systemctl','disable','--now','th-n8n-backup.timer'],env=env,check=True)
subprocess.run(['chroot','/host','systemctl','stop','th-n8n-backup.service'],env=env)
"""
command=['docker','run','--rm','--network','none','--pid','host','--user','0','--cap-drop','ALL','--cap-add','SYS_CHROOT','--cap-add','DAC_OVERRIDE','--cap-add','FOWNER','--cap-add','CHOWN',
'--mount','type=bind,src=/,dst=/host,readonly','--mount','type=bind,src=/etc/systemd/system,dst=/host/etc/systemd/system','--mount','type=bind,src=/run/systemd,dst=/host/run/systemd','--mount','type=bind,src=/run/dbus,dst=/host/run/dbus,readonly',
'--entrypoint','python3','ghcr.io/tuanpham0411/th-rpi-console:e8db24f19e9f74c1d16438d6cd3355fe098ee89a','-c',script]
subprocess.run(command,check=True)
for name in ['th-n8n-gateway-1','th-n8n-n8n-1']:
 subprocess.run(['docker','update','--restart=no',name],check=True,stdout=subprocess.DEVNULL)
 subprocess.run(['docker','stop','-t','20',name],check=True,stdout=subprocess.DEVNULL)
subprocess.run(['docker','start','ai-agent-api-1','ai-agent-web-1'],check=True,stdout=subprocess.DEVNULL)
path=home/'state/n8n-deployment.json'
if path.exists():
 state=json.loads(path.read_text());state.update(ready=False,retired=True)
 (home/'state/n8n-retired.json').write_text(json.dumps(state));path.unlink()
for attempt in range(60):
 p=subprocess.run(['curl','--fail','--silent','--max-time','10','https://ai.th.io.vn/api/ready'],capture_output=True)
 if p.returncode==0:break
 time.sleep(3)
else:raise SystemExit('Custom AI readiness did not recover')
subprocess.run(['curl','--fail','--silent','--max-time','30','https://ai.th.io.vn/'],stdout=subprocess.PIPE,check=True)
print(json.dumps({'n8nRetired':True,'legacyAiRestored':True,'databasesAndDriveBackupsPreserved':True}))
