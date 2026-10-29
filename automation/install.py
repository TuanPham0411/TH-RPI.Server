#!/usr/bin/env python3
"""Install the versioned automation supervisor through the existing trusted host helper."""
import os,pathlib,subprocess,sys,json,re
sha=sys.argv[1]
if not re.fullmatch('[a-f0-9]{40}',sha):raise SystemExit('Immutable platform release required')
source=pathlib.Path(__file__).resolve().parent
script=r'''import pathlib,shutil,subprocess,os,sys
root=pathlib.Path('/host');sha=sys.argv[1]
if (root/'etc/hostname').read_text().strip()!='th-server':raise SystemExit('Unexpected server')
base=root/'opt/th-server-automation';release=base/'releases'/sha;release.mkdir(parents=True,exist_ok=True)
shutil.copyfile('/source/supervisor.py',release/'supervisor.py');(release/'supervisor.py').chmod(0o644)
link=base/'current.new';link.unlink(missing_ok=True);link.symlink_to('releases/'+sha);link.replace(base/'current')
unit="""[Unit]
Description=TH-Server workflow timers and isolated Functions
After=docker.service network-online.target
[Service]
User=thadmin
SupplementaryGroups=docker
ExecStart=/usr/bin/python3 /opt/th-server-automation/current/supervisor.py
ExecStopPost=/usr/bin/python3 /opt/th-server-automation/current/supervisor.py --cleanup
Restart=on-failure
RestartSec=5
UMask=0077
NoNewPrivileges=true
MemoryMax=96M
TasksMax=64
TimeoutStopSec=20
[Install]
WantedBy=multi-user.target
"""
(root/'etc/systemd/system/th-automation.service').write_text(unit)
env={**os.environ,'SYSTEMD_IGNORE_CHROOT':'1'}
for cmd in [['daemon-reload'],['enable','th-automation.service'],['restart','th-automation.service']]:subprocess.run(['chroot','/host','systemctl',*cmd],env=env,check=True)
'''
command=['docker','run','--rm','--network','none','--pid','host','--user','0','--cap-drop','ALL','--cap-add','SYS_CHROOT','--cap-add','DAC_OVERRIDE','--cap-add','FOWNER','--cap-add','CHOWN','--mount','type=bind,src=/,dst=/host,readonly','--mount','type=bind,src=/opt,dst=/host/opt','--mount','type=bind,src=/etc/systemd/system,dst=/host/etc/systemd/system','--mount','type=bind,src=/run/systemd,dst=/host/run/systemd','--mount','type=bind,src=/run/dbus,dst=/host/run/dbus,readonly','--mount',f'type=bind,src={source},dst=/source,readonly','--entrypoint','python3','ghcr.io/tuanpham0411/th-rpi-console:e8db24f19e9f74c1d16438d6cd3355fe098ee89a','-c',script,sha]
subprocess.run(command,check=True);print(json.dumps({'automationInstalled':True,'platformRelease':sha}))
