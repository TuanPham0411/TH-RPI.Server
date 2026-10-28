#!/usr/bin/env python3
"""Version server configuration as an encrypted Drive ledger record, only on change."""
import fcntl,hashlib,http.client,json,os,pathlib,socket,time,uuid
home=pathlib.Path.home()/'th-platform'
os.umask(0o077)
lock=open(home/'deploy.lock','a');fcntl.flock(lock,fcntl.LOCK_EX)
config=json.loads((home/'config/runtime.json').read_text())
class UnixConnection(http.client.HTTPConnection):
 def connect(self):
  self.sock=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);self.sock.settimeout(300);self.sock.connect(str(home/'run/storage.sock'))
def broker(path,value):
 connection=UnixConnection('localhost',timeout=300)
 try:
  connection.request('POST',path,body=json.dumps(value).encode(),headers={'Content-Type':'application/json','Authorization':'Bearer '+config['tokens']['server'],'X-TH-Application':'server'})
  response=connection.getresponse();result=json.loads(response.read())
  if response.status!=200:raise RuntimeError('Encrypted server snapshot was not acknowledged')
  return result
 finally:connection.close()
files={};paths=[pathlib.Path('/etc/hostname'),pathlib.Path('/etc/hosts'),pathlib.Path('/etc/nginx/nginx.conf'),pathlib.Path('/etc/systemd/system/cloudflared.service')]
paths+=list(pathlib.Path('/etc/nginx/sites-available').glob('*'))
paths+=list((home/'config').glob('*.json'))+list((home/'apps').glob('*.env'))
for app in ['ai-agent','portfolio','mes-ticket']:
 paths+=[pathlib.Path.home()/'apps'/app/name for name in ['.env','compose.yaml','storage.compose.yaml','database-init.sh']]
 state=home/'state'/(app+'-deployment.json')
 if state.exists():
  paths.append(state);release=pathlib.Path(json.loads(state.read_text())['release'])
  paths+=[release/name for name in ['compose.yaml','storage.compose.yaml','database-init.sh','image.env']]
for path in paths:
 try:
  if path.is_file() and not path.is_symlink() and path.stat().st_size<=1024*1024:files[str(path)]=path.read_text()
 except (PermissionError,UnicodeError):pass
snapshot={'kind':'server-configuration','files':files}
encoded=json.dumps(snapshot,sort_keys=True,separators=(',',':'))
digest=hashlib.sha256(encoded.encode()).hexdigest();last=home/'state/server-snapshot-hash'
if last.exists() and last.read_text()==digest:
 print(json.dumps({'serverSnapshot':'unchanged','verified':True}));raise SystemExit(0)
id=str(uuid.uuid4())
for attempt in range(3):
 try:
  history=broker('/v1/history',{})['history']
  if not any(item['id']==id for item in history):
   broker('/v1/commit',{'schemaVersion':1,'id':id,'parent':history[-1]['id'] if history else None,'events':[{'schema':'server','table':'credentials','action':'INSERT','old':None,'new':encoded}]})
  event=broker('/v1/event',{'id':id})
  if event['events'][0]['new']!=encoded:raise RuntimeError('Encrypted server snapshot verification differs')
  temporary=last.with_suffix('.tmp');temporary.write_text(digest);temporary.replace(last)
  print(json.dumps({'serverSnapshot':id,'verified':True,'changed':True}));break
 except Exception:
  if attempt==2:raise SystemExit('Server snapshot failed without changing the source configuration')
  time.sleep(2)
