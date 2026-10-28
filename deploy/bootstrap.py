#!/usr/bin/env python3
"""One-time/repeatable broker deployment by the TH-Server management runner.
Never prints Docker environment variables, OAuth credentials or application tokens.
"""
import argparse, json, os, pathlib, secrets, shutil, subprocess, tempfile, hashlib, hmac, fcntl

def run(*args, **kwargs):
    return subprocess.run(args, check=True, text=True, **kwargs)

def atomic_json(path, value):
    fd, temporary = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(value, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary): os.unlink(temporary)

parser = argparse.ArgumentParser()
parser.add_argument('--image-tag', required=True)
args = parser.parse_args()
if not all(c in '0123456789abcdef' for c in args.image_tag) or len(args.image_tag) != 40:
    raise SystemExit('An immutable full commit SHA is required')
if os.getuid() != 1000:
    raise SystemExit('Expected the existing thadmin UID 1000; review volume ownership before deployment')
os.umask(0o077)
home = pathlib.Path.home() / 'th-platform'
for directory in ['config', 'state', 'run', 'apps', 'releases']:
    (home / directory).mkdir(parents=True, exist_ok=True, mode=0o700)
deployment_lock = open(home/'deploy.lock','a')
fcntl.flock(deployment_lock, fcntl.LOCK_EX)
source = pathlib.Path(__file__).resolve().parents[1]
release = home / 'releases' / args.image_tag
release.mkdir(exist_ok=True, mode=0o700)
shutil.copyfile(source / 'compose.yaml', release / 'compose.yaml')
metadata = json.loads(run('docker','inspect','mes-ticket-mes-1', capture_output=True).stdout)[0]
environment = dict(item.split('=',1) for item in metadata['Config']['Env'] if '=' in item)
client_id = environment.get('GOOGLE_DRIVE_CLIENT_ID') or environment.get('GOOGLE_CLIENT_ID')
client_secret = environment.get('GOOGLE_DRIVE_CLIENT_SECRET') or environment.get('GOOGLE_CLIENT_SECRET')
if not client_id or not client_secret: raise SystemExit('Existing MES Drive OAuth client is missing')
credentials = json.loads(run('docker','exec','mes-ticket-mes-1','node','-e',
    'process.stdout.write(require("node:fs").readFileSync(process.env.DRIVE_TOKEN_FILE,"utf8"))', capture_output=True).stdout)
if not credentials.get('refreshToken') or not credentials.get('email'): raise SystemExit('Existing offline Drive authorization is missing')
config_path = home / 'config/runtime.json'
if config_path.exists():
    config = json.loads(config_path.read_text())
    if config['oauth']['clientId'] != client_id or config['oauth']['clientSecret'] != client_secret or config['oauth']['expectedEmail'] != credentials['email']:
        raise SystemExit('OAuth identity/key changed; preserve the old recovery key and review rotation first')
else:
    config = {'oauth':{'clientId':client_id,'clientSecret':client_secret,'expectedEmail':credentials['email']},
              'tokens':{name:secrets.token_hex(32) for name in ['ai-agent','portfolio','mes-ticket','server']}}
    atomic_json(config_path, config)
atomic_json(home / 'config/drive-credentials.json', credentials)
# Preserve a recovery key separately from the encrypted Drive account rows.
prk = hmac.new(b'th-data-storage-v1', client_secret.encode(), hashlib.sha256).digest()
recovery_key = hmac.new(prk, b'private-database-fields'+bytes([1]), hashlib.sha256).digest()
(home/'config/recovery-key.txt').write_text(recovery_key.hex()+'\n')
for app, token in config['tokens'].items():
    path = home / 'apps' / (app+'.env')
    path.write_text('TH_STORAGE_TOKEN='+token+'\nTH_STORAGE_RUN_DIR='+str(home/'run')+'\n')
    path.chmod(0o600)
(home/'.env').write_text('IMAGE_TAG='+args.image_tag+'\nTH_PLATFORM_HOME='+str(home)+'\n')
command = ['docker','compose','-p','th-platform','--env-file',str(home/'.env'),'-f',str(release/'compose.yaml')]
run(*command,'pull')
(home/'run/maintenance').write_text('Broker upgrade in progress\n')
run(*command,'up','-d','--no-build')
# Readiness is checked through the authenticated Unix socket without revealing tokens.
check = '''const fs=require('node:fs'),http=require('node:http');
const c=JSON.parse(fs.readFileSync('/config/runtime.json','utf8'));
const body=JSON.stringify({parent:null});
const r=http.request({socketPath:'/run/th-storage/storage.sock',path:'/v1/history',method:'POST',headers:{Authorization:'Bearer '+c.tokens.server,'X-TH-Application':'server','Content-Type':'application/json','Content-Length':Buffer.byteLength(body)}},res=>{res.resume();res.on('end',()=>process.exit(res.statusCode===200?0:1))});r.on('error',()=>process.exit(1));r.end(body);'''
import time
for attempt in range(45):
    result = subprocess.run(command+['exec','-T','storage','node','-e',check],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    if result.returncode == 0: break
    time.sleep(2)
else:
    run(*command,'logs','--tail=30','storage')
    raise SystemExit('Broker is not ready; application storage modes were not changed')
backup = {'platformConfig':config,'driveCredentials':credentials,'containers':{},'files':{}}
for container in ['ai-agent-api-1','portfolio-portfolio-1','mes-ticket-mes-1']:
    backup['containers'][container] = json.loads(run('docker','inspect',container,capture_output=True).stdout)[0]
paths = [pathlib.Path('/etc/hostname'),pathlib.Path('/etc/hosts'),pathlib.Path('/etc/nginx/nginx.conf'),pathlib.Path('/etc/systemd/system/cloudflared.service')]
paths += list(pathlib.Path('/etc/nginx/sites-available').glob('*'))
paths += [pathlib.Path.home()/'apps'/name/'.env' for name in ['ai-agent','portfolio','mes-ticket']]
for path in paths:
    if path.is_file() and not path.is_symlink() and path.stat().st_size < 1024*1024:
        try: backup['files'][str(path)] = path.read_text()
        except (PermissionError,UnicodeError): pass
backup['packages'] = run('dpkg-query','-W',capture_output=True).stdout
snapshot = '''import {readFile} from 'node:fs/promises';import {randomUUID,createHash} from 'node:crypto';import {brokerRequest} from './storage/client.mjs';
const c=JSON.parse(await readFile('/config/runtime.json','utf8'));let text='';for await(const chunk of process.stdin)text+=chunk;
const config={socketPath:'/run/th-storage/storage.sock',token:c.tokens.server,application:'server'};
const {history}=await brokerRequest(config,'/v1/history',{});const id=randomUUID();
await brokerRequest(config,'/v1/commit',{schemaVersion:1,id,parent:history.at(-1)?.id??null,events:[{schema:'server',table:'credentials',action:'BASELINE',old:null,new:text}]});
const packet=await brokerRequest(config,'/v1/event',{id});if(packet.events[0].new!==text)throw Error('Server recovery round trip differs');
console.log(JSON.stringify({serverSnapshot:id,verified:true}));'''
result = run(*command,'exec','-T','storage','node','--input-type=module','-e',snapshot,input=json.dumps(backup),capture_output=True)
print(result.stdout.strip())
(home/'run/maintenance').unlink(missing_ok=True)
atomic_json(home/'state/deployment.json', {'imageTag':args.image_tag,'release':str(release),'ready':True})
print('Central storage broker is ready. Existing application providers were not changed.')
