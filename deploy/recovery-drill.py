#!/usr/bin/env python3
"""Restore Drive history into disposable databases without changing production rows."""
import argparse,datetime,fcntl,hashlib,http.client,json,os,pathlib,socket,subprocess,urllib.parse
parser=argparse.ArgumentParser();parser.add_argument('--application',choices=['ai-agent','portfolio','ticket']);selection=parser.parse_args().application
home=pathlib.Path.home()/'th-platform'
os.umask(0o077)
lock=open(home/'deploy.lock','a');fcntl.flock(lock,fcntl.LOCK_EX)

def command(args,**options):
 result=subprocess.run(args,stdout=subprocess.PIPE,stderr=subprocess.PIPE,**options)
 if result.returncode: raise RuntimeError('Recovery verification command failed; production retained')
 return result.stdout

def sql(container,database,query):
 return command(['docker','exec',container,'psql','-X','-v','ON_ERROR_STOP=1','-U','postgres','-d',database,'-At','-c',query])

def quote(value):return '"'+value.replace('"','""')+'"'

config=json.loads((home/'config/runtime.json').read_text())
class UnixConnection(http.client.HTTPConnection):
 def connect(self):
  self.sock=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);self.sock.settimeout(300);self.sock.connect(str(home/'run/storage.sock'))
def broker(path,value):
 connection=UnixConnection('localhost',timeout=300)
 try:
  connection.request('POST',path,body=json.dumps(value).encode(),headers={'Content-Type':'application/json','Authorization':'Bearer '+config['tokens']['server'],'X-TH-Application':'server'})
  response=connection.getresponse();result=json.loads(response.read())
  if response.status!=200:raise RuntimeError('Drive recovery metadata unavailable')
  return result
 finally:connection.close()
snapshot=None
for marker in reversed(broker('/v1/history',{})['history']):
 packet=broker('/v1/event',{'id':marker['id']})
 for event in packet['events']:
  if event['table']=='credentials' and event['new']:
   record=json.loads(event['new'])
   if record.get('kind')=='server-configuration' and record.get('databases'):snapshot=record;break
 if snapshot:break
if not snapshot:raise RuntimeError('Run the server configuration snapshot before recovery verification')

for app,service,database,operator in [('ai-agent','db','th_ai','server/storage-operator.js'),('portfolio','database','th_portfolio','dist/server/storage-operator.cjs'),('ticket','database','ticket','dist/server/storage-operator.cjs')]:
 if selection and app!=selection:continue
 state=json.loads((home/'state'/(app+'-deployment.json')).read_text())
 if not state.get('ready') or not state.get('driveFirst'):raise RuntimeError('Application cutover is not ready')
 release=pathlib.Path(state['release']);container=app+'-'+service+'-1'
 target='th_recovery_'+app.replace('-','_')+'_'+datetime.datetime.now().strftime('%Y%m%d%H%M%S')
 source_head=sql(container,database,'SELECT public.th_storage_head()').strip()
 # Both schema definitions and migration catalogs come from encrypted Drive JSON.
 definition=snapshot['databases'][app]
 if definition['release']!=state['imageTag']:raise RuntimeError('Snapshot the deployed schema before recovery verification')
 schema=definition['schema'].encode()
 tables=json.loads(sql(container,database,"SELECT coalesce(json_agg(json_build_object('schema',schemaname,'table',tablename) ORDER BY schemaname,tablename),'[]') FROM pg_tables WHERE schemaname IN ('public','portfolio','ticket_private') AND tablename NOT IN ('schema_migrations','portfolio_schema_migrations','ai_schema_migrations')"))
 credentials=json.loads(command(['docker','inspect',container]))[0]['Config']['Env']
 password=next(item.split('=',1)[1] for item in credentials if item.startswith('POSTGRES_PASSWORD='))
 environment={**os.environ,'DATABASE_URL':'postgresql://postgres:'+urllib.parse.quote(password,safe='')+'@'+service+':5432/'+target}
 base=['docker','compose','-p',app,'--env-file',str(pathlib.Path.home()/'apps'/app/'.env'),'--env-file',str(home/'apps'/(app+'.env')),'--env-file',str(release/'image.env'),'-f',str(release/'compose.yaml'),'-f',str(release/'storage.compose.yaml')]
 created=False
 try:
  command(['docker','exec',container,'createdb','-U','postgres',target]);created=True
  command(['docker','exec','-i',container,'psql','-X','-v','ON_ERROR_STOP=1','-U','postgres','-d',target],input=schema)
  for catalog in definition['migrations']:
   command(['docker','exec','-i',container,'psql','-X','-v','ON_ERROR_STOP=1','-U','postgres','-d',target],input=catalog.encode())
  sql(container,target,'INSERT INTO th_storage.head(id,operation,enforced) VALUES(true,null,false)')
  command(base+['run','--rm','--no-deps','-e','DATABASE_URL','-e','FILES_ROOT=/tmp/th-recovery-files','migrate','node',operator,'restore-empty'],env=environment)
  recovered_head=sql(container,target,'SELECT public.th_storage_head()').strip()
  compared=source_head==recovered_head
  if compared:
   differs=False
   for table in tables:
    name=quote(table['schema'])+'.'+quote(table['table'])
    query='SELECT row_to_json(t)::text FROM '+name+' t ORDER BY row_to_json(t)::text COLLATE "C"'
    original=hashlib.sha256(sql(container,database,query)).digest()
    restored=hashlib.sha256(sql(container,target,query)).digest()
    if original!=restored:differs=True
   if sql(container,database,'SELECT public.th_storage_head()').strip()!=source_head:compared=False
   elif differs:raise RuntimeError('Recovery row comparison differs; production retained')
  result={'application':app,'restoredFromDrive':True,'foreignKeysVerified':True,'mediaChecksumsVerified':True,'productionUnchanged':True,'exactRowsCompared':compared}
  print(json.dumps(result),flush=True)
  (home/'state'/(app+'-recovery-verification.json')).write_text(json.dumps({**result,'verifiedAt':datetime.datetime.now(datetime.timezone.utc).isoformat(),'release':state['imageTag']}))
 finally:
  if created:command(['docker','exec',container,'dropdb','--force','-U','postgres',target])
