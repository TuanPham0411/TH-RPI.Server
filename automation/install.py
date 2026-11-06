#!/usr/bin/env python3
"""Install the versioned automation supervisor through the existing trusted host helper."""
import argparse,os,pathlib,subprocess,sys,json,re
parser=argparse.ArgumentParser();parser.add_argument('sha');parser.add_argument('--sika1',action='store_true');args=parser.parse_args();sha=args.sha
if not re.fullmatch('[a-f0-9]{40}',sha):raise SystemExit('Immutable platform release required')
source=pathlib.Path(__file__).resolve().parent
script=r'''import pathlib,shutil,subprocess,os,sys
root=pathlib.Path('/host');sha=sys.argv[1];sika=sys.argv[2]=='1'
if (root/'etc/hostname').read_text().strip()!=('SIKA1' if sika else 'th-server'):raise SystemExit('Unexpected server')
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
if sika:unit=unit.replace('User=thadmin','User=sika1').replace('UMask=0077','UMask=0077\nEnvironment=TH_AUTOMATION_TIMERS=0')
(root/'etc/systemd/system/th-automation.service').write_text(unit)
env={**os.environ,'SYSTEMD_IGNORE_CHROOT':'1'}
for cmd in [['daemon-reload'],['enable','th-automation.service'],['restart','th-automation.service']]:subprocess.run(['chroot','/host','systemctl',*cmd],env=env,check=True)
'''
command=['docker','run','--rm','--network','none','--pid','host','--user','0','--cap-drop','ALL','--cap-add','SYS_CHROOT','--cap-add','DAC_OVERRIDE','--cap-add','FOWNER','--cap-add','CHOWN','--mount','type=bind,src=/,dst=/host,readonly','--mount','type=bind,src=/opt,dst=/host/opt','--mount','type=bind,src=/etc/systemd/system,dst=/host/etc/systemd/system','--mount','type=bind,src=/run/systemd,dst=/host/run/systemd','--mount','type=bind,src=/run/dbus,dst=/host/run/dbus,readonly','--mount',f'type=bind,src={source},dst=/source,readonly','--entrypoint','python3','ghcr.io/tuanpham0411/th-rpi-console:0dab2b0a6e61b26c9fb345d89503ac57f1301597','-c',script,sha,'1' if args.sika1 else '0']
subprocess.run(command,check=True);print(json.dumps({'automationInstalled':True,'platformRelease':sha}))
