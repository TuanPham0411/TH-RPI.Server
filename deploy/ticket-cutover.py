#!/usr/bin/env python3
"""Prepare/apply the Ticket hostname without moving databases or media."""
import argparse,pathlib,subprocess
p=argparse.ArgumentParser()
p.add_argument('--apply',action='store_true')
args=p.parse_args()
source=pathlib.Path(__file__).with_name('ticket-nginx.conf')
target=pathlib.Path.home()/'th-platform/state/ticket-cutover'
target.mkdir(parents=True,exist_ok=True,mode=0o700)
prepared=target/'th-ticket.conf'
prepared.write_text(source.read_text())
prepared.chmod(0o600)
commands=[
 ['sudo','install','-m','644',str(prepared),'/etc/nginx/sites-available/th-ticket'],
 ['sudo','ln','-sfn','/etc/nginx/sites-available/th-ticket','/etc/nginx/sites-enabled/th-ticket'],
 ['sudo','nginx','-t'],
 ['sudo','systemctl','reload','nginx']]
if not args.apply:
 import shlex
 print('Prepared Nginx configuration: '+str(prepared))
 for command in commands: print(shlex.join(command))
else:
 if subprocess.run(['sudo','-n','true'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode:
  raise SystemExit('Host sudo authentication is required. Run the prepared commands in your existing SSH session.')
 for command in commands: subprocess.run(command,check=True)
 print('ticket.th.io.vn Nginx route installed and validated.')
