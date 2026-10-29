#!/usr/bin/env python3
"""Install n8n on the existing TH-Server without exposing first-owner signup."""
import argparse,datetime,fcntl,gzip,http.cookies,json,os,pathlib,re,secrets,shutil,subprocess,sys,time,urllib.error,urllib.request,uuid
HERE=pathlib.Path(__file__).resolve().parent;HOME=pathlib.Path.home()/'th-platform';DB='ai-agent-db-1'
def run(args,**kwargs):
 result=subprocess.run(args,capture_output=True,**kwargs)
 if result.returncode:raise RuntimeError('Deployment command failed: '+str(args[0]))
 return result.stdout

def sql(value,database='postgres'):
 return run(['docker','exec','-i',DB,'psql','-X','-qAt','-v','ON_ERROR_STOP=1','-U','postgres','-d',database],input=value.encode()).decode().strip()
def request(port,path,method='GET',data=None,cookie=None):
 headers={'Content-Type':'application/json','Host':'ai.th.io.vn','Origin':'https://ai.th.io.vn'}
 if cookie:headers['Cookie']=cookie
 req=urllib.request.Request(f'http://127.0.0.1:{port}'+path,data=json.dumps(data).encode() if data is not None else None,headers=headers,method=method)
 with urllib.request.urlopen(req,timeout=120) as res:
  body=res.read();value=json.loads(body) if body else {};return value,res.headers

def main():
 p=argparse.ArgumentParser();p.add_argument('--release',required=True);p.add_argument('--platform-release',required=True);p.add_argument('--source',type=pathlib.Path,required=True);a=p.parse_args()
 if not all(re.fullmatch('[a-f0-9]{40}',x) for x in [a.release,a.platform_release]):raise RuntimeError('Immutable release SHAs required')
 if os.getuid()!=1000:raise RuntimeError('Expected thadmin UID 1000')
 os.umask(0o077);lock=open(HOME/'deploy.lock','a');fcntl.flock(lock,fcntl.LOCK_EX)
 folder=HOME/'n8n/releases'/a.release;folder.mkdir(parents=True,exist_ok=True,mode=0o700)
 for name in ['compose.yaml','workflow.json']:shutil.copyfile(a.source/name,folder/name)
 # Management implementation is pinned separately from application configuration.
 helper=['docker','run','--rm','--network','none','--pid','host','--user','0','--cap-drop','ALL','--cap-add','SYS_CHROOT','--cap-add','DAC_OVERRIDE','--cap-add','FOWNER','--cap-add','CHOWN',
 '--mount','type=bind,src=/,dst=/host,readonly','--mount','type=bind,src=/opt,dst=/host/opt','--mount','type=bind,src=/var/lib,dst=/host/var/lib','--mount','type=bind,src=/etc/systemd/system,dst=/host/etc/systemd/system','--mount','type=bind,src=/run/systemd,dst=/host/run/systemd','--mount','type=bind,src=/run/dbus,dst=/host/run/dbus,readonly','--mount',f'type=bind,src={HERE},dst=/control,readonly','--entrypoint','python3','ghcr.io/tuanpham0411/th-rpi-console:e8db24f19e9f74c1d16438d6cd3355fe098ee89a','/control/host.py',a.platform_release]
 # Install swap before starting the larger Node runtime. No existing swap is removed.
 output=run(helper);print(output.decode().strip(),flush=True)
 envpath=HOME/'apps/n8n.env'
 fresh=not envpath.exists()
 if fresh:
  if sql("SELECT 1 FROM pg_roles WHERE rolname='th_n8n'"):raise RuntimeError('n8n database exists without recovery environment')
  values={'DB_POSTGRESDB_PASSWORD':secrets.token_hex(32),'N8N_ENCRYPTION_KEY':secrets.token_hex(32)}
 else:values=dict(line.split('=',1) for line in envpath.read_text().splitlines() if '=' in line)
 values.update({'N8N_IMAGE':'docker.n8n.io/n8nio/n8n:2.45.6','GATEWAY_IMAGE':'ghcr.io/tuanpham0411/th-ai-n8n-gateway:'+a.release,'PUBLIC_PORT':'8083'})
 envpath.write_text(''.join(k+'='+v+'\n' for k,v in values.items()));envpath.chmod(0o600)
 localenv=folder/'.env';localenv.unlink(missing_ok=True);localenv.symlink_to(envpath)
 if not sql("SELECT 1 FROM pg_roles WHERE rolname='th_n8n'"):
  password=values['DB_POSTGRESDB_PASSWORD'];assert re.fullmatch('[a-f0-9]{64}',password)
  sql("CREATE ROLE th_n8n LOGIN PASSWORD '"+password+"' NOSUPERUSER NOCREATEDB NOCREATEROLE;")
 if not sql("SELECT 1 FROM pg_database WHERE datname='th_n8n'"):
  sql('CREATE DATABASE th_n8n OWNER th_n8n;')
 command=['docker','compose','-p','th-n8n','--env-file',str(envpath),'-f',str(folder/'compose.yaml')]
 run(command+['pull']);run(command+['up','-d'])
 ready=False
 for attempt in range(180):
  try:
   value,_=request(8083,'/api/ready')
   if value.get('status')=='ready':ready=True;break
  except Exception:pass
  time.sleep(5)
 if not ready:raise RuntimeError('Staged n8n is not ready; existing AI site was retained')
 ownerfile=HOME/'config/n8n-owner-login.json'
 if not ownerfile.exists():
  owner={'email':'eric.scorp93@gmail.com','password':'TH1!'+secrets.token_urlsafe(24),'firstName':'TH','lastName':'Owner'}
  ownerfile.write_text(json.dumps(owner));ownerfile.chmod(0o600)
 else:owner=json.loads(ownerfile.read_text())
 if not (HOME/'state/n8n-workflow.json').exists():
  if not sql("SELECT 1 FROM public.\"user\" WHERE email='eric.scorp93@gmail.com';",'th_n8n'):
   response,headers=request(8083,'/rest/owner/setup','POST',owner)
  else:
   response,headers=request(8083,'/rest/login','POST',{'emailOrLdapLoginId':owner['email'],'password':owner['password']})
  cookies=http.cookies.SimpleCookie();[cookies.load(item) for item in headers.get_all('Set-Cookie',[])]
  cookie='; '.join(k+'='+v.value for k,v in cookies.items())
  if not cookie:raise RuntimeError('n8n owner setup did not issue a session')
  credential={'name':'TH-Server Private Chat Memory','type':'postgres','data':{'host':DB,'database':'th_n8n','user':'th_n8n','password':values['DB_POSTGRESDB_PASSWORD'],'port':5432,'ssl':'disable','sshTunnel':False}}
  response,_=request(8083,'/rest/credentials','POST',credential,cookie);memory=response.get('data',response)
  workflow=json.loads((folder/'workflow.json').read_text());workflow.pop('id',None)
  for node in workflow['nodes']:
   if node['type'].endswith('.memoryPostgresChat'):node['credentials']={'postgres':{'id':memory['id'],'name':credential['name']}}
  response,_=request(8083,'/rest/workflows','POST',workflow,cookie);created=response.get('data',response)
  workflow_id=created['id']
  (HOME/'state/n8n-workflow.json').write_text(json.dumps({'id':workflow_id,'name':workflow['name'],'active':False}))
 elif (HOME/'state/n8n-workflow.json').exists():workflow_id=json.loads((HOME/'state/n8n-workflow.json').read_text())['id']
 else:raise RuntimeError('Existing n8n owner needs workflow recovery; not importing duplicates')
 # Never expose an unclaimed n8n owner setup page through the public hostname.
 if sql("SELECT count(*) FROM public.\"user\" WHERE email='eric.scorp93@gmail.com';",'th_n8n')!='1':raise RuntimeError('Owner provisioning not verified')
 state={'application':'n8n','ready':False,'imageTag':a.release,'platformRelease':a.platform_release,'release':str(folder),'version':'2.45.6','n8nImage':values['N8N_IMAGE'],'gatewayImage':values['GATEWAY_IMAGE'],'workflowId':workflow_id,'ownerEmail':owner['email'],'publicPort':8081,'backupPolicy':'postgresql-plus-drive','legacyAiRetained':True}
 (HOME/'state/n8n-deployment.json').write_text(json.dumps(state))
 # Initial encrypted Drive backup and isolated restore verification precede cutover.
 sys.path.insert(0,str(HERE));from backup import backup
 backup(verify=True,locked=True)
 was_running={name:run(['docker','inspect',name,'--format','{{.State.Running}}']).decode().strip()=='true' for name in ['ai-agent-api-1','ai-agent-web-1']}
 try:
  run(['docker','stop','ai-agent-web-1','ai-agent-api-1'])
  values['PUBLIC_PORT']='8081';envpath.write_text(''.join(k+'='+v+'\n' for k,v in values.items()))
  run(command+['up','-d','--no-deps','--force-recreate','gateway'])
  for attempt in range(30):
   try:
    value,_=request(8081,'/api/ready')
    if value.get('status')=='ready':break
   except Exception:pass
   time.sleep(2)
  else:raise RuntimeError('New AI gateway did not become ready')
  run(['curl','--fail','--silent','--show-error','--retry','5','--max-time','30','https://ai.th.io.vn/'])
  run(['curl','--fail','--silent','--show-error','--max-time','20','https://ai.th.io.vn/api/ready'])
 except Exception:
  run(command+['stop','gateway'])
  for name,running in was_running.items():
   if running:run(['docker','start',name])
  raise RuntimeError('n8n public cutover failed; original AI service restored')
 state['ready']=True;(HOME/'state/n8n-deployment.json').write_text(json.dumps(state))
 print(json.dumps({'n8nReady':True,'version':'2.45.6','url':'https://ai.th.io.vn/workflow/'+workflow_id,'workflowActive':False,'credentialsRequired':['Google Gemini','Perplexity Header Auth','Google Sheets OAuth'],'ownerLoginFile':str(ownerfile),'driveRestoreVerified':True}),flush=True)
if __name__=='__main__':
 try:main()
 except Exception as error:
  print(json.dumps({'n8nDeploymentFailed':type(error).__name__,'reason':str(error)[:250]}));raise SystemExit(1)
