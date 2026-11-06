#!/usr/bin/env python3
"""Install a restored AI Studio on SIKA1; never replace a nonempty database."""
import argparse,http.client,json,os,pathlib,re,secrets,shutil,socket,subprocess,time,urllib.request
p=argparse.ArgumentParser();p.add_argument('phase',choices=['clone','restore']);p.add_argument('--platform-release',required=True);p.add_argument('--ai-release',required=True);p.add_argument('--ai-source',required=True);a=p.parse_args()
if socket.gethostname().upper()!='SIKA1' or os.getuid()!=1000 or pathlib.Path.home().name!='sika1':raise SystemExit('This deployment requires SIKA1 / sika1 UID 1000')
if not all(re.fullmatch('[a-f0-9]{40}',sha) for sha in [a.platform_release,a.ai_release]):raise SystemExit('Immutable releases required')
os.umask(0o077);home=pathlib.Path.home()/'th-platform';config=home/'config';state=home/'state';run=home/'run';release=home/'releases'/('ai-'+a.ai_release)
for folder in [state,run,release,home/'apps']:folder.mkdir(parents=True,exist_ok=True,mode=0o700)
def command(args,timeout=1800):
    result=subprocess.run(args,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=timeout)
    if result.returncode:
        (state/'ai-command-error.log').write_bytes(result.stderr[-32000:]);(state/'ai-command-error.log').chmod(0o600)
        raise RuntimeError('Command failed; details retained privately on SIKA1: '+args[0])
    return result.stdout
runtime=json.loads((config/'runtime.json').read_text())
if runtime.get('driveRootName')!='th-data-sika1-ai' or runtime.get('origins',{}).get('ai-agent')!='https://thienha.io.vn':raise SystemExit('Isolated AI configuration required')
platform='ghcr.io/tuanpham0411/th-rpi-storage:'+a.platform_release
command(['docker','pull',platform])
if a.phase=='clone':
    command(['docker','run','--rm','--user','1000:1000','--read-only','--cap-drop','ALL','--security-opt','no-new-privileges:true','--mount',f'type=bind,src={config},dst=/config,readonly','--mount',f'type=bind,src={state},dst=/state','--entrypoint','node',platform,'storage/clone-ai.mjs'],timeout=3600)
    cloned=json.loads((state/'ai-clone.json').read_text());print(json.dumps({'verifiedCommits':cloned['commits'],'baseline':cloned['baseline'],'sourceRetained':True}));raise SystemExit(0)
cloned=json.loads((state/'ai-clone.json').read_text())
if not cloned.get('commits'):raise SystemExit('Verified AI recovery journal required')
passwords=home/'apps/ai-database.env'
if not passwords.exists():passwords.write_text('POSTGRES_PASSWORD='+secrets.token_hex(32)+'\nTH_AI_DB_PASSWORD='+secrets.token_hex(32)+'\n');passwords.chmod(0o600)
values=dict(line.split('=',1) for line in passwords.read_text().splitlines())
(home/'apps/ai-agent.env').write_text('TH_STORAGE_TOKEN='+runtime['tokens']['ai-agent']+'\nTH_STORAGE_RUN_DIR='+str(run)+'\n')
for volume in ['ai-agent_ai-db','ai-agent_ai-files']:command(['docker','volume','create',volume])
command(['docker','run','--rm','--network','none','--user','0','--cap-drop','ALL','--cap-add','CHOWN','--mount','type=volume,src=ai-agent_ai-files,dst=/files','--entrypoint','node',platform,'-e',"require('node:fs').chownSync('/files',1000,1000)"])
shutil.copyfile(pathlib.Path(a.ai_source)/'database-init.sh',release/'database-init.sh');(release/'database-init.sh').chmod(0o644)
nginx=release/'nginx.conf';nginx.write_text((pathlib.Path(a.ai_source)/'deploy/docker-nginx.conf').read_text()+'\nserver { listen 80; server_name www.thienha.io.vn; return 308 https://thienha.io.vn$request_uri; }\n');nginx.chmod(0o644)
operator={'DATABASE_URL':'postgresql://postgres:'+values['POSTGRES_PASSWORD']+'@db:5432/th_ai','RUNTIME_DB_ROLE':'th_ai_api','PGOPTIONS':'-c th_storage.authorized=1','TH_STORAGE_SOCKET':'/run/th-storage/storage.sock','TH_STORAGE_TOKEN':runtime['tokens']['ai-agent'],'TH_RELEASE':a.ai_release,'FILES_ROOT':'/var/lib/th-ai/files','PUBLIC_ORIGIN':'https://thienha.io.vn'}
image='ghcr.io/tuanpham0411/th-ai-agent-api:'+a.ai_release
mounts=['ai-files:/var/lib/th-ai/files',str(run)+':/run/th-storage:ro']
services={
 'storage':{'image':platform,'user':'1000:1000','read_only':True,'restart':'unless-stopped','mem_limit':'192m','environment':{'NODE_OPTIONS':'--max-old-space-size=128'},'volumes':[str(config)+':/config:ro',str(state)+':/state',str(run)+':/run/th-storage','ai-files:/source/ai:ro']},
 'db':{'image':'postgres:17-bookworm','restart':'unless-stopped','command':['postgres','-c','shared_buffers=32MB','-c','max_connections=20','-c','work_mem=2MB'],'environment':{'POSTGRES_DB':'th_ai',**values},'volumes':['ai-db:/var/lib/postgresql/data',str(release/'database-init.sh')+':/docker-entrypoint-initdb.d/ai-role.sh:ro'],'healthcheck':{'test':['CMD-SHELL','pg_isready -U postgres -d th_ai'],'interval':'5s','retries':20}},
 'migrate':{'image':image,'environment':operator,'volumes':mounts,'command':['node','server/migrate.js']},
 'api':{'image':image,'restart':'unless-stopped','stop_grace_period':'6m','mem_limit':'224m','cpus':.5,'environment':{**{k:v for k,v in operator.items() if k not in ('PGOPTIONS','RUNTIME_DB_ROLE')},'DATABASE_URL':'postgresql://th_ai_api:'+values['TH_AI_DB_PASSWORD']+'@db:5432/th_ai','TH_STORAGE_MODE':'drive-first'},'volumes':mounts,'ports':['127.0.0.1:3100:3100'],'healthcheck':{'test':['CMD','node','-e',"fetch('http://127.0.0.1:3100/api/ready').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"],'interval':'15s','retries':8}},
 'web':{'image':'ghcr.io/tuanpham0411/th-ai-agent-web:'+a.ai_release,'restart':'unless-stopped','ports':['127.0.0.1:5050:80'],'volumes':[str(nginx)+':/etc/nginx/conf.d/default.conf:ro']}}
compose=release/'compose.json';compose.write_text(json.dumps({'services':services,'volumes':{'ai-db':{'external':True,'name':'ai-agent_ai-db'},'ai-files':{'external':True,'name':'ai-agent_ai-files'}}}));compose.chmod(0o600)
base=['docker','compose','-p','ai-agent','-f',str(compose)]
command(base+['pull'])
command(base+['up','-d','--wait','--wait-timeout','180','db','storage'])
class Unix(http.client.HTTPConnection):
    def connect(self):
        self.sock=socket.socket(socket.AF_UNIX);self.sock.settimeout(10);self.sock.connect(str(run/'storage.sock'))
for _ in range(900):
    try:
        conn=Unix('localhost',timeout=10);conn.request('POST','/v1/status','{}',{'Authorization':'Bearer '+runtime['tokens']['ai-agent'],'X-TH-Application':'ai-agent','Content-Type':'application/json'})
        response=conn.getresponse();ready=response.status==200 and json.loads(response.read()).get('connected');conn.close()
        if ready:break
    except (OSError,ValueError,http.client.HTTPException):pass
    time.sleep(2)
else:raise RuntimeError('Storage journal recovery has not completed')
receipt=state/'ai-restored.json'
if not receipt.exists():
    command(base+['run','--rm','--no-deps','migrate'])
    command(base+['run','--rm','--no-deps','-v',str(pathlib.Path(__file__).with_name('prepare-ai-recovery.mjs').resolve())+':/app/prepare-ai-recovery.mjs:ro','-v',str(state/'ai-clone.json')+':/recovery/manifest.json:ro','migrate','node','/app/prepare-ai-recovery.mjs'])
    command(base+['run','--rm','--no-deps','migrate','node','server/storage-operator.js','install'])
    command(base+['run','--rm','--no-deps','migrate','node','server/storage-operator.js','restore-empty'],timeout=3600)
    receipt.write_text(json.dumps({'head':cloned['head'],'release':a.ai_release,'restored':True}))
else:
    if json.loads(receipt.read_text()).get('release')!=a.ai_release:raise RuntimeError('Existing installation requires a separate upgrade, retained unchanged')
command(base+['run','--rm','--no-deps','migrate','node','server/storage-operator.js','probe'])
(state/'ai-agent-deployment.json').write_text(json.dumps({'imageTag':a.ai_release,'publicOrigin':'https://thienha.io.vn','compose':str(compose),'restored':True,'timersEnabled':False}))
command(base+['up','-d','--wait','--wait-timeout','180','api'])
command(base+['up','-d','web'])
command(['python3',str(pathlib.Path(__file__).resolve().parents[1]/'automation/install.py'),a.platform_release,'--sika1'])
for _ in range(30):
    if (run/'automation.sock').exists():break
    time.sleep(1)
conn=Unix('localhost',timeout=20)
conn.connect=lambda: None
conn.sock=socket.socket(socket.AF_UNIX);conn.sock.settimeout(25);conn.sock.connect(str(run/'automation.sock'))
conn.request('POST','/function',json.dumps({'code':'return {total:input.a+input.b};','input':{'a':2,'b':3}}),{'Authorization':'Bearer '+runtime['tokens']['ai-agent'],'Content-Type':'application/json'})
response=conn.getresponse();result=json.loads(response.read());conn.close()
if response.status!=200 or result!={'output':{'total':5}}:raise RuntimeError('Isolated Function verification failed')
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
for endpoint in ['/api/health','/api/ready','/api/auth/session']:
    with opener.open(urllib.request.Request('http://127.0.0.1:5050'+endpoint,headers={'Host':'thienha.io.vn'}),timeout=30) as response:
        if response.status!=200:raise RuntimeError('Local application verification failed')
print(json.dumps({'deployed':True,'domain':'https://thienha.io.vn','restored':True,'aiRelease':a.ai_release,'timersPaused':True}))
