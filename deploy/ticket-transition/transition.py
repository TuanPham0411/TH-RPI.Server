#!/usr/bin/env python3
"""Move the Ticket namespace with verified rows, media and Drive history."""
import argparse,fcntl,hashlib,json,os,pathlib,re,shutil,subprocess,sys,time,urllib.request
APP_TAG='2f261d191af3019f5785be5a371e8c1d5e5cda6f'
PLATFORM_TAG='caa276f27da7c21bfc07a7f42e6c9513efe21e4e'
APP_IMAGE='ghcr.io/tuanpham0411/th-ticket:'+APP_TAG
PLATFORM_IMAGE='ghcr.io/tuanpham0411/th-rpi-storage:'+PLATFORM_TAG
protected=re.compile(r'password|secret|token|hash|checksum|digest|verifier|salt|client.?id|folder.?id|drive.?id',re.I)

def renamer(application):
 prefix=application.split('-')[0]
 pattern=re.compile(r'(?<![A-Za-z0-9])'+re.escape(prefix)+r'(?=[^A-Za-z0-9]|$|[A-Z])')
 upper=re.compile(r'(?<![A-Za-z0-9])'+re.escape(prefix.upper())+r'(?=[^A-Za-z0-9]|$)')
 def text(value):
  value=value.replace(application,'ticket').replace(application.replace('-','_'),'ticket')
  value=pattern.sub('ticket',value)
  return upper.sub(lambda m:'TICKET' if value[m.end():m.end()+1]=='_' else 'Ticket',value)
 def value(item,key=''):
  if isinstance(item,dict):return {text(k):value(v,k) for k,v in item.items()}
  if isinstance(item,list):return [value(v,key) for v in item]
  if isinstance(item,str):return item if protected.search(key) else text(item)
  return item
 return text,value

def main():
 if os.getuid()!=1000:raise RuntimeError('Expected the existing server administrator UID')
 os.umask(0o077)
 home=pathlib.Path.home()/'th-platform';stage=home/'state/ticket-transition';stage.mkdir(parents=True,exist_ok=True)
 lock=open(home/'deploy.lock','a');fcntl.flock(lock,fcntl.LOCK_EX)
 config_path=home/'config/runtime.json';config=json.loads(config_path.read_text())
 old=[k for k in config['tokens'] if k not in ['ai-agent','portfolio','server','ticket']]
 if not old:
  if (stage/'completed.json').exists():print('Ticket namespace migration already completed.');return
  raise RuntimeError('Ticket already configured; inspect the existing transition checkpoint before retrying')
 if len(old)!=1:raise RuntimeError('Ambiguous source application')
 old=old[0];text,change=renamer(old)
 old_state=json.loads((home/'state'/(old+'-deployment.json')).read_text())
 old_container=old+'-'+old.split('-')[0]+'-1';old_db=old+'-database-1';old_database=old.replace('-','_')
 source=pathlib.Path(__file__).parent
 log_number=0
 def run(args,*,data=None,env=None,quiet=True):
  nonlocal log_number
  result=subprocess.run(args,input=data,env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
  if result.returncode:
   log_number+=1;(stage/('command-error-'+str(log_number)+'.log')).write_bytes(result.stderr)
   raise RuntimeError('A transition command failed; private diagnostics retained on the server')
  return result.stdout
 def sql(container,database,query):return run(['docker','exec',container,'psql','-X','-v','ON_ERROR_STOP=1','-U','postgres','-d',database,'-At','-c',query]).decode()
 def atomic(path,value):
  temporary=path.with_suffix(path.suffix+'.tmp');temporary.write_text(json.dumps(value));temporary.chmod(0o600);temporary.replace(path)
 def fetch(repo,tag,path,target):
  target.parent.mkdir(parents=True,exist_ok=True)
  with urllib.request.urlopen('https://raw.githubusercontent.com/TuanPham0411/'+repo+'/'+tag+'/'+path,timeout=60) as response:target.write_bytes(response.read())
 def compose(directory,project):return ['docker','compose','-p',project,'--env-file',str(directory/'.env'),'-f',str(directory/'compose.yaml'),'-f',str(directory/'storage.compose.yaml')]
 def ledger(action):return run(['docker','exec','-i','th-platform-storage-1','node','--input-type=module','-',action],data=(source/'ledger-transition.mjs').read_bytes())
 def announce(message):print(message,flush=True)
 target=pathlib.Path.home()/'apps/ticket'
 if target.exists() and not (stage/'target-owned.json').exists():raise RuntimeError('Target app directory already exists; inspect before moving data')
 target.mkdir(parents=True,exist_ok=True);atomic(stage/'target-owned.json',{'target':str(target)})
 old_env=pathlib.Path.home()/'apps'/old/'.env'
 lines=[]
 for line in old_env.read_text().splitlines():
  if '=' in line and not line.lstrip().startswith('#'):
   key,val=line.split('=',1);lines.append(text(key)+'='+(val if protected.search(key) else text(val)))
  else:lines.append(text(line))
 lines=[line for line in lines if not line.startswith(('IMAGE_TAG=','TH_STORAGE_MODE=','TH_STORAGE_SOCKET=','TH_STORAGE_TOKEN=','TH_STORAGE_RUN_DIR='))]
 lines+=['IMAGE_TAG='+APP_TAG,'TH_STORAGE_MODE=drive-first','TH_STORAGE_SOCKET=/run/th-storage/storage.sock','TH_STORAGE_TOKEN='+config['tokens'][old],'TH_STORAGE_RUN_DIR='+str(home/'run')]
 (target/'.env').write_text('\n'.join(lines)+'\n');(target/'.env').chmod(0o600)
 for name in ['compose.yaml','storage.compose.yaml','database-init.sh']:fetch('TH-Ticket',APP_TAG,name,target/name)
 base=compose(target,'ticket')
 run(['docker','image','inspect',PLATFORM_IMAGE]);run(['docker','pull',APP_IMAGE])
 old_volume=json.loads(run(['docker','inspect',old_container]))[0]['Mounts']
 old_volume=next(m['Name'] for m in old_volume if m.get('Destination')=='/data')
 run(['docker','volume','create','ticket_media'])
 run(base+['up','-d','--no-build','database'])
 for attempt in range(90):
  state=json.loads(run(['docker','inspect','ticket-database-1']))[0]
  if state['State'].get('Health',{}).get('Status')=='healthy':break
  time.sleep(2)
 else:raise RuntimeError('Target database is not ready')
 maintenance=home/'run/maintenance';maintenance.write_text('Ticket namespace transition in progress\n')
 run(['docker','stop','-t','360',old_container])
 run(['docker','run','--rm','--user','0','--entrypoint','sh','-v',old_volume+':/from:ro','-v','ticket_media:/to',APP_IMAGE,'-c','test -z "$(ls -A /to)" && cp -a /from/. /to/'])
 verify_media="const fs=require('fs'),p=require('path'),c=require('crypto');function inventory(root,dir=''){const out=[];for(const e of fs.readdirSync(p.join(root,dir),{withFileTypes:true})){const name=p.join(dir,e.name);if(e.isSymbolicLink())throw Error('Unexpected media symlink');if(e.isDirectory())out.push(...inventory(root,name));else if(e.isFile()){const h=c.createHash('sha256'),fd=fs.openSync(p.join(root,name),'r'),b=Buffer.alloc(65536);let n;try{while((n=fs.readSync(fd,b,0,b.length,null)))h.update(b.subarray(0,n));}finally{fs.closeSync(fd);}out.push([name,h.digest('hex')]);}}return out.sort((a,b)=>a[0].localeCompare(b[0]));}if(JSON.stringify(inventory('/from'))!==JSON.stringify(inventory('/to')))throw Error('Media copy differs');console.log('Media byte checksums verified');"
 announce(run(['docker','run','--rm','--user','0','--entrypoint','node','-v',old_volume+':/from:ro','-v','ticket_media:/to:ro',APP_IMAGE,'-e',verify_media]).decode().strip())
 announce('Source writes stopped; exporting verified database rows and Drive history.')
 if int(sql(old_db,old_database,'SELECT count(*) FROM th_storage.events').strip())!=0:raise RuntimeError('Unacknowledged source events require recovery before migration')
 schema=run(['docker','exec',old_db,'pg_dump','-U','postgres','--schema-only',old_database]).decode()
 (stage/'schema-before.sql').write_text(schema)
 schema=text(schema);(stage/'schema-ticket.sql').write_text(schema)
 tables=json.loads(sql(old_db,old_database,"SELECT json_agg(json_build_object('schema',schemaname,'table',tablename) ORDER BY schemaname,tablename) FROM pg_tables WHERE schemaname IN ('public','portfolio','"+old.split('-')[0]+"_private')"))
 quote=lambda name:'"'+name.replace('"','""')+'"'
 expected=[];statements=['BEGIN;','SET LOCAL session_replication_role=replica;','SET LOCAL th_storage.authorized=1;']
 for table in tables:
  old_name=quote(table['schema'])+'.'+quote(table['table']);new_schema=text(table['schema']);new_table=text(table['table']);new_name=quote(new_schema)+'.'+quote(new_table)
  rows=[json.loads(line) for line in sql(old_db,old_database,'SELECT row_to_json(t)::text FROM '+old_name+' t').splitlines()]
  rows=[change(row) for row in rows];expected.append({'schema':new_schema,'table':new_table,'rows':rows})
  for row in rows:
   payload=json.dumps(row,ensure_ascii=False,separators=(',',':')).replace("'","''")
   statements.append('INSERT INTO '+new_name+' OVERRIDING SYSTEM VALUE SELECT * FROM json_populate_record(NULL::'+new_name+",'"+payload+"');")
 sequences=json.loads(sql(old_db,old_database,"SELECT coalesce(json_agg(json_build_object('schema',schemaname,'name',sequencename,'value',last_value::text)),'[]') FROM pg_sequences WHERE schemaname IN ('public','portfolio','"+old.split('-')[0]+"_private')"))
 for sequence in sequences:
  if sequence['value'] is not None:
   name=quote(text(sequence['schema']))+'.'+quote(text(sequence['name']))
   statements.append("SELECT pg_catalog.setval('"+name.replace("'","''")+"',"+str(int(sequence['value']))+",true);")
 statements.append('COMMIT;');atomic(stage/'expected-rows.json',expected)
 run(['docker','exec','-i','ticket-database-1','psql','-X','-v','ON_ERROR_STOP=1','-U','postgres','-d','ticket'],data=schema.encode())
 run(['docker','exec','-i','ticket-database-1','psql','-X','-v','ON_ERROR_STOP=1','-U','postgres','-d','ticket'],data='\n'.join(statements).encode())
 canon=lambda rows:sorted(json.dumps(row,sort_keys=True,ensure_ascii=False,separators=(',',':')) for row in rows)
 for table in expected:
  name=quote(table['schema'])+'.'+quote(table['table'])
  actual=[json.loads(line) for line in sql('ticket-database-1','ticket','SELECT row_to_json(t)::text FROM '+name+' t').splitlines()]
  if canon(actual)!=canon(table['rows']):raise RuntimeError('Target row verification differs')
 announce('Target database rows verified. Rewriting authenticated Drive history to ticket.')
 announce(ledger('export').decode().strip())
 original=json.loads((stage/'drive-source.json').read_text());converted=[]
 for entry in original['packets']:
  packet=entry['packet'];events=[]
  for event in packet['events']:
   events.append({**event,'schema':text(event['schema']),'table':text(event['table']),**{key:None if event[key] is None else json.dumps(change(json.loads(event[key])),ensure_ascii=False,separators=(',',':')) for key in ['old','new']}})
  converted.append({'packet':{**packet,'application':'ticket','events':events,'baseline':change(packet.get('baseline')),'files':change(packet.get('files',[]))}})
 atomic(stage/'drive-target.json',{'folderId':original['folderId'],'packets':converted})
 announce(ledger('apply').decode().strip())
 verified=json.loads((stage/'drive-verified.json').read_text())
 if not verified.get('verified'):raise RuntimeError('Drive history verification was not acknowledged')
 atomic(stage/'runtime-before.json',config)
 config['tokens']['ticket']=config['tokens'].pop(old);atomic(config_path,config)
 platform_release=home/'releases'/PLATFORM_TAG;platform_release.mkdir(parents=True,exist_ok=True)
 fetch('TH-RPI.Server',PLATFORM_TAG,'compose.yaml',platform_release/'compose.yaml')
 (home/'.env').write_text('IMAGE_TAG='+PLATFORM_TAG+'\nTH_PLATFORM_HOME='+str(home)+'\n')
 platform=['docker','compose','-p','th-platform','--env-file',str(home/'.env'),'-f',str(platform_release/'compose.yaml')]
 run(platform+['up','-d','--no-build','--pull','never'])
 check="const fs=require('fs'),http=require('http');const c=JSON.parse(fs.readFileSync('/config/runtime.json'));const r=http.request({socketPath:'/run/th-storage/storage.sock',path:'/v1/history',method:'POST',headers:{Authorization:'Bearer '+c.tokens.ticket,'X-TH-Application':'ticket','Content-Type':'application/json'}},res=>{res.resume();res.on('end',()=>process.exit(res.statusCode===200?0:1))});r.on('error',()=>process.exit(1));r.setTimeout(60000,()=>r.destroy());r.end('{}');"
 for attempt in range(400):
  result=subprocess.run(['docker','exec','th-platform-storage-1','node','-e',check],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
  if result.returncode==0:break
  time.sleep(2)
 else:raise RuntimeError('Ticket storage readiness was not confirmed')
 announce('Ticket storage is ready; installing capture and publishing the new application.')
 run(base+['run','--rm','--no-deps','migrate'])
 run(base+['run','--rm','--no-deps','migrate','node','dist/server/storage-operator.cjs','install'])
 announce(run(base+['run','--rm','--no-deps','migrate','node','dist/server/storage-operator.cjs','baseline']).decode().strip())
 announce(run(base+['run','--rm','--no-deps','migrate','node','dist/server/storage-operator.cjs','probe']).decode().strip())
 run(base+['up','-d','--no-build','--no-deps','ticket'])
 for attempt in range(90):
  result=subprocess.run(['curl','-fsS','--max-time','5','-H','Host: ticket.th.io.vn','http://127.0.0.1:8082/api/health'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
  if result.returncode==0:break
  time.sleep(2)
 else:raise RuntimeError('Ticket application readiness failed')
 run(['curl','-fsS','--retry','3','--max-time','20','https://ticket.th.io.vn/'])
 release=home/'apps/ticket/releases'/APP_TAG;release.mkdir(parents=True,exist_ok=True)
 for name in ['compose.yaml','storage.compose.yaml','database-init.sh']:shutil.copyfile(target/name,release/name)
 (release/'.env').symlink_to(target/'.env');(release/'image.env').write_text('IMAGE_TAG='+APP_TAG+'\n')
 (home/'apps/ticket.env').write_text('TH_STORAGE_TOKEN='+config['tokens']['ticket']+'\nTH_STORAGE_RUN_DIR='+str(home/'run')+'\n')
 atomic(home/'state/ticket-deployment.json',{'application':'ticket','imageTag':APP_TAG,'release':str(release),'backup':str(stage),'driveFirst':True,'ready':True})
 atomic(home/'state/deployment.json',{'imageTag':PLATFORM_TAG,'release':str(platform_release),'ready':True})
 maintenance.unlink(missing_ok=True)
 atomic(stage/'completed.json',{'complete':True,'driveFolderId':verified['folderId'],'rowsVerified':True,'operations':len(converted),'oldApplication':old,'oldVolume':old_volume})
 announce('Ticket namespace transition completed; database rows and Drive history verified.')

if __name__=='__main__':
 try:main()
 except Exception as error:
  print('Ticket transition paused safely: '+str(error),file=sys.stderr)
  sys.exit(1)
