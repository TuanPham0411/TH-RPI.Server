#!/usr/bin/env python3
"""Fixed SSH forced command: receive AI bootstrap privately, never execute input."""
import json,os,pathlib,secrets,sys
os.umask(0o077)
root=pathlib.Path.home()/'th-platform'
config=root/'config'
config.mkdir(parents=True,exist_ok=True,mode=0o700)
if config.is_symlink() or (config/'runtime.json').exists():raise SystemExit('AI configuration already exists; retained unchanged')
raw=sys.stdin.buffer.read(100001)
if len(raw)>100000:raise SystemExit('Bootstrap exceeds size limit')
value=json.loads(raw)
oauth=value['oauth'];drive=value['drive']
if oauth.get('expectedEmail','').lower()!='eric.scorp93@gmail.com' or drive.get('email','').lower()!=oauth['expectedEmail'].lower():raise SystemExit('Recovery owner differs')
if not all(isinstance(oauth.get(k),str) and oauth[k] for k in ['clientId','clientSecret','expectedEmail']) or not drive.get('refreshToken'):raise SystemExit('Incomplete recovery authorization')
runtime={'oauth':oauth,'tokens':{'ai-agent':secrets.token_hex(32),'server':secrets.token_hex(32)},'driveRootName':'th-data-sika1-ai','origins':{'ai-agent':'https://thienha.io.vn'}}
def write(name,data):
    path=config/name
    with path.open('x') as file:json.dump(data,file)
    path.chmod(0o600)
write('drive-credentials.json',drive)
if value.get('firebase'):
    (config/'ai-studio').mkdir(mode=0o700,exist_ok=True)
    write('ai-studio/firebase-service-account.ai-agent-67316.json',value['firebase'])
write('runtime.json',runtime)
print('AI bootstrap received privately; original configuration retained on source')
