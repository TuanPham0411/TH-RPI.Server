#!/usr/bin/env python3
"""Version server configuration as an encrypted Drive ledger record, only on change."""
import fcntl,hashlib,http.client,json,os,pathlib,socket,subprocess,time,uuid
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
paths+=[pathlib.Path('/etc/systemd/system/th-server-console.service'),home/'state/console-deployment.json']
console=pathlib.Path('/opt/th-server-console/current')
if console.is_dir():paths+=[console/'console.py',console/'th-server-console.service']
paths+=list((home/'config').glob('*.json'))+list((home/'apps').glob('*.env'))
for app in ['ai-agent','portfolio','ticket']:
 paths+=[pathlib.Path.home()/'apps'/app/name for name in ['.env','compose.yaml','storage.compose.yaml','database-init.sh']]
 state=home/'state'/(app+'-deployment.json')
 if state.exists():
  paths.append(state);release=pathlib.Path(json.loads(state.read_text())['release'])
  paths+=[release/name for name in ['compose.yaml','storage.compose.yaml','database-init.sh','image.env']]
for path in paths:
 try:
  if path.is_file() and not path.is_symlink() and path.stat().st_size<=1024*1024:files[str(path)]=path.read_text()
 except (PermissionError,UnicodeError):pass
databases={}
for app,service,database in [('ai-agent','db','th_ai'),('portfolio','database','th_portfolio'),('ticket','database','ticket')]:
 state=json.loads((home/'state'/(app+'-deployment.json')).read_text())
 container=app+'-'+service+'-1'
 def dump(*arguments):
  result=subprocess.run(['docker','exec',container,'pg_dump','-U','postgres',*arguments,database],capture_output=True,text=True)
  if result.returncode:raise SystemExit('Database schema snapshot failed; existing data retained')
  return '\n'.join(line for line in result.stdout.splitlines() if not line.startswith(('\\restrict ', '\\unrestrict ')))+'\n'
 catalogs=[]
 for name in ['schema_migrations','portfolio_schema_migrations','ai_schema_migrations']:
  result=subprocess.run(['docker','exec',container,'psql','-X','-U','postgres','-d',database,'-At','-c',"SELECT to_regclass('public."+name+"') IS NOT NULL"],capture_output=True,text=True)
  if result.returncode:raise SystemExit('Migration catalog snapshot failed')
  if result.stdout.strip()=='t':catalogs.append(dump('--data-only','--table=public.'+name))
 databases[app]={'release':state['imageTag'],'schema':dump('--schema-only','--clean','--if-exists'),'migrations':catalogs}
snapshot={'kind':'server-configuration','files':files,'databases':databases}
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
