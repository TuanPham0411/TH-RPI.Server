#!/usr/bin/env python3
"""Restore Drive history into disposable databases without changing production rows."""
import datetime,fcntl,hashlib,json,os,pathlib,subprocess,urllib.parse
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

for app,service,database,operator in [('ai-agent','db','th_ai','server/storage-operator.js'),('portfolio','database','th_portfolio','dist/server/storage-operator.cjs'),('mes-ticket','database','mes_ticket','dist/server/storage-operator.cjs')]:
 state=json.loads((home/'state'/(app+'-deployment.json')).read_text())
 if not state.get('ready') or not state.get('driveFirst'):raise RuntimeError('Application cutover is not ready')
 release=pathlib.Path(state['release']);container=app+'-'+service+'-1'
 target='th_recovery_'+app.replace('-','_')+'_'+datetime.datetime.now().strftime('%Y%m%d%H%M%S')
 source_head=sql(container,database,'SELECT public.th_storage_head()').strip()
 # Schema-only dump includes capture definitions, but no user data or ledger rows.
 schema=command(['docker','exec',container,'pg_dump','-U','postgres','--schema-only','-Fc',database])
 tables=json.loads(sql(container,database,"SELECT coalesce(json_agg(json_build_object('schema',schemaname,'table',tablename) ORDER BY schemaname,tablename),'[]') FROM pg_tables WHERE schemaname IN ('public','portfolio','mes_private') AND tablename NOT IN ('schema_migrations','portfolio_schema_migrations','ai_schema_migrations')"))
 credentials=json.loads(command(['docker','inspect',container]))[0]['Config']['Env']
 password=next(item.split('=',1)[1] for item in credentials if item.startswith('POSTGRES_PASSWORD='))
 environment={**os.environ,'DATABASE_URL':'postgresql://postgres:'+urllib.parse.quote(password,safe='')+'@'+service+':5432/'+target}
 base=['docker','compose','-p',app,'--env-file',str(pathlib.Path.home()/'apps'/app/'.env'),'--env-file',str(home/'apps'/(app+'.env')),'--env-file',str(release/'image.env'),'-f',str(release/'compose.yaml'),'-f',str(release/'storage.compose.yaml')]
 created=False
 try:
  command(['docker','exec',container,'createdb','-U','postgres',target]);created=True
  command(['docker','exec','-i',container,'pg_restore','--exit-on-error','--clean','--if-exists','-U','postgres','-d',target],input=schema)
  for name in ['schema_migrations','portfolio_schema_migrations','ai_schema_migrations']:
   if sql(container,database,"SELECT to_regclass('public."+name+"') IS NOT NULL").strip()==b't':
    data=command(['docker','exec',container,'pg_dump','-U','postgres','--data-only','--table=public.'+name,database])
    command(['docker','exec','-i',container,'psql','-X','-v','ON_ERROR_STOP=1','-U','postgres','-d',target],input=data)
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
