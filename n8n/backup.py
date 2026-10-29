#!/usr/bin/env python3
"""Consistent n8n PostgreSQL snapshot + encrypted JSON Drive export, owned by TH-Server."""
import argparse,datetime,fcntl,gzip,json,os,pathlib,re,shutil,subprocess,uuid
HOME=pathlib.Path.home()/'th-platform';SOURCE=pathlib.Path(__file__).resolve().parent
DB='ai-agent-db-1';NAME='th_n8n'
def run(args,**kwargs):
 result=subprocess.run(args,capture_output=True,**kwargs)
 if result.returncode:raise RuntimeError('n8n backup subprocess failed: '+str(args[0]))
 return result.stdout
def sql(query,database=NAME):
 return run(['docker','exec','-i',DB,'psql','-X','-qAt','-v','ON_ERROR_STOP=1','-U','postgres','-d',database],input=query.encode()).decode().strip()
COUNTS="""SELECT coalesce(jsonb_object_agg(tablename,((xpath('/row/c/text()',query_to_xml(format('SELECT count(*) AS c FROM %I.%I',schemaname,tablename),false,true,'')))[1]::text)::bigint),'{}') FROM pg_tables WHERE schemaname='public';"""
def backup(verify=False,locked=False):
 os.umask(0o077)
 lock=None
 if not locked:
  lock=open(HOME/'deploy.lock','a');fcntl.flock(lock,fcntl.LOCK_EX)
 state=json.loads((HOME/'state/n8n-deployment.json').read_text())
 identifier=datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid.uuid4().hex[:12]
 folder=HOME/'state/backups/n8n'/identifier;folder.mkdir(parents=True,mode=0o700)
 p=subprocess.Popen(['docker','exec','-i',DB,'psql','-X','-qAt','-v','ON_ERROR_STOP=1','-U','postgres','-d',NAME],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True)
 try:
  p.stdin.write('BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY; SELECT pg_export_snapshot();\n');p.stdin.flush();snapshot=p.stdout.readline().strip()
  if not re.fullmatch('[0-9A-Fa-f-]+',snapshot):raise RuntimeError('Database snapshot unavailable')
  prefix=f"BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY; SET TRANSACTION SNAPSHOT '{snapshot}'; "
  counts=json.loads(sql(prefix+COUNTS+' COMMIT;'))
  workflows=json.loads(sql(prefix+"SELECT coalesce(json_agg(row_to_json(w)),'[]') FROM workflow_entity w; COMMIT;"))
  dump=subprocess.Popen(['docker','exec',DB,'pg_dump','-U','postgres','--clean','--if-exists','--no-owner','--no-privileges','--snapshot='+snapshot,NAME],stdout=subprocess.PIPE,stderr=subprocess.DEVNULL)
  with gzip.open(folder/'database.sql.gz','wb') as stream:shutil.copyfileobj(dump.stdout,stream,1024*1024)
  if dump.wait()!=0:raise RuntimeError('Database dump failed')
  p.stdin.write('COMMIT;\n\\q\n');p.stdin.flush();p.wait(timeout=20)
 finally:
  if p.poll() is None:p.kill();p.wait()
 (folder/'workflows.json').write_text(json.dumps(workflows))
 configuration={'env':(HOME/'apps/n8n.env').read_text(),'deployment':state}
 (folder/'configuration.json').write_text(json.dumps(configuration))
 image=run(['docker','inspect','th-platform-storage-1','--format','{{.Config.Image}}']).decode().strip()
 with open(folder/'volume.tar.gz','wb') as stream:
  result=subprocess.run(['docker','run','--rm','--network','none','--user','0','--cap-drop','ALL','--cap-add','DAC_OVERRIDE','--mount','type=volume,src=th-n8n_data,dst=/source,readonly','--entrypoint','tar',state['n8nImage'],'-C','/source','-czf','-','.'],stdout=stream,stderr=subprocess.DEVNULL)
  if result.returncode:raise RuntimeError('n8n configuration volume snapshot failed')
 for name in ['codec.mjs','drive.mjs']:shutil.copyfile(SOURCE/name,folder/name)
 root=json.loads((HOME/'state/drive-root.json').read_text())['folderId']
 input={'action':'upload','id':identifier,'createdAt':datetime.datetime.now(datetime.timezone.utc).isoformat(),'rootId':root,'n8nVersion':state['version'],'tableCounts':counts}
 def transfer(action):
  input['action']=action;(folder/'input.json').write_text(json.dumps(input))
  output=run(['docker','run','--rm','--read-only','--user','1000:1000','--cap-drop','ALL','--memory','192m','--mount',f'type=bind,src={HOME}/config,dst=/config,readonly','--mount',f'type=bind,src={folder},dst=/backup','--entrypoint','node',image,'/backup/drive.mjs'])
  print(output.decode().strip(),flush=True)
 transfer('upload')
 if verify:
  transfer('verify')
  # Restore into an isolated database; never touch production tables.
  temporary='n8n_verify_'+uuid.uuid4().hex[:16]
  sql('CREATE DATABASE '+temporary+' OWNER th_n8n;','postgres')
  try:
   restore=subprocess.Popen(['docker','exec','-i',DB,'psql','-X','-q','-v','ON_ERROR_STOP=1','-U','postgres','-d',temporary],stdin=subprocess.PIPE,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
   restore.stdin.write(b'SET ROLE th_n8n;\n')
   with gzip.open(folder/'restored-database.sql.gz','rb') as stream:shutil.copyfileobj(stream,restore.stdin,1024*1024)
   restore.stdin.close()
   if restore.wait()!=0:raise RuntimeError('Isolated n8n restore failed')
   if json.loads(sql(COUNTS,temporary))!=counts:raise RuntimeError('n8n restored table counts differ')
   print(json.dumps({'n8nRestoreVerified':True,'productionUnchanged':True,'tables':len(counts),'workflowCount':len(workflows)}),flush=True)
  finally:sql('DROP DATABASE '+temporary+' WITH (FORCE);','postgres')
 manifest=json.loads((folder/'manifest.json').read_text())
 result={'id':identifier,'verified':True,'restoreVerified':verify,'createdAt':input['createdAt'],'path':f'ai-agent/n8n/backups/{identifier}','files':len(manifest['files'])}
 target=HOME/'state/n8n-backup.json';tmp=target.with_suffix('.tmp');tmp.write_text(json.dumps(result));tmp.replace(target)
 # Keep only encrypted local artifacts; readable secrets/dumps are temporary.
 for name in ['database.sql.gz','workflows.json','configuration.json','volume.tar.gz','restored-database.sql.gz','restored-workflows.json','restored-configuration.json','restored-volume.tar.gz','input.json']:(folder/name).unlink(missing_ok=True)
 if lock:lock.close()
 return result
if __name__=='__main__':
 parser=argparse.ArgumentParser();parser.add_argument('--verify',action='store_true');args=parser.parse_args()
 try:backup(args.verify)
 except Exception as error:
  print(json.dumps({'n8nBackupFailed':type(error).__name__,'reason':str(error)[:180]}));raise SystemExit(1)
