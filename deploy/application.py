#!/usr/bin/env python3
"""Shared application deployment implementation, owned by TH-RPI.Server.
All secrets remain in existing host files. No data volumes are deleted.
"""
import argparse, datetime, fcntl, json, os, pathlib, shutil, subprocess, time

APPS = {
 'ai-agent': {'service':'api','web':['web'],'database':'db','name':'th_ai','port':8081,'domain':'ai.th.io.vn','operator':'server/storage-operator.js'},
 'portfolio': {'service':'portfolio','web':[],'database':'database','name':'th_portfolio','port':8080,'domain':'th.io.vn','operator':'dist/server/storage-operator.cjs'},
 'ticket': {'service':'ticket','web':[],'database':'database','name':'ticket','port':8082,'domain':'ticket.th.io.vn','operator':'dist/server/storage-operator.cjs'},
}

def run(command, **kwargs): return subprocess.run(command,check=True,**kwargs)

parser=argparse.ArgumentParser()
parser.add_argument('--application',choices=APPS,required=True)
parser.add_argument('--image-tag',required=True)
parser.add_argument('--source',type=pathlib.Path,required=True)
args=parser.parse_args()
if len(args.image_tag)!=40 or any(c not in '0123456789abcdef' for c in args.image_tag): raise SystemExit('Immutable application image SHA required')
if os.getuid()!=1000: raise SystemExit('Expected existing thadmin UID 1000')
os.umask(0o077)
app=APPS[args.application]
home=pathlib.Path.home()/'th-platform'
if not (home/'state/deployment.json').exists(): raise SystemExit('Deploy the central storage platform first')
lock=open(home/'deploy.lock','a');fcntl.flock(lock,fcntl.LOCK_EX)
original=pathlib.Path.home()/'apps'/args.application
if not (original/'.env').is_file(): raise SystemExit('Existing application environment file is missing')
release=home/'apps'/args.application/'releases'/args.image_tag
release.mkdir(parents=True,exist_ok=True,mode=0o700)
for name in ['compose.yaml','storage.compose.yaml','database-init.sh']:
 source=args.source/name
 if not source.is_file() or source.is_symlink(): raise SystemExit('Trusted release file missing: '+name)
 shutil.copyfile(source,release/name)
(release/'image.env').write_text('IMAGE_TAG='+args.image_tag+'\n')
# Service env_file paths resolve against the release, independently of CLI --env-file.
environment=release/'.env'
if environment.is_symlink(): environment.unlink()
elif environment.exists(): raise SystemExit('Unexpected release-local environment file')
environment.symlink_to(original/'.env')
command=['docker','compose','-p',args.application,'--env-file',str(original/'.env'),'--env-file',str(home/'apps'/(args.application+'.env')),'--env-file',str(release/'image.env'),'-f',str(release/'compose.yaml'),'-f',str(release/'storage.compose.yaml')]
run(command+['pull'])
maintenance=home/'run'/('maintenance-'+args.application)
maintenance.write_text('Application migration in progress\n')
run(command+['stop',app['service']]+app['web'])
stamp=datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
backup=home/'state/backups'/args.application/stamp
backup.mkdir(parents=True,mode=0o700)
try:
 with open(backup/'database.dump','xb') as stream:
  run(['docker','exec',args.application+'-'+app['database']+'-1','pg_dump','-U','postgres','-Fc',app['name']],stdout=stream)
  stream.flush();os.fsync(stream.fileno())
 if (backup/'database.dump').stat().st_size<32: raise RuntimeError('Database backup is empty')
 # Migrations run as the operator while application/background writers are stopped.
 run(command+['run','--rm','--no-deps','migrate'])
 run(command+['run','--rm','--no-deps','migrate','node',app['operator'],'install'])
 run(command+['run','--rm','--no-deps','migrate','node',app['operator'],'baseline'])
 maintenance.unlink(missing_ok=True)
 run(command+['up','-d','--no-build','--no-deps',app['service']]+app['web'])
 ready=False
 for attempt in range(60):
  endpoint='/api/ready' if args.application=='ai-agent' else '/api/health'
  response=subprocess.run(['curl','--fail','--silent','--max-time','5','-H','Host: '+app['domain'],'http://127.0.0.1:'+str(app['port'])+endpoint],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
  if response.returncode==0: ready=True;break
  time.sleep(2)
 if not ready: raise RuntimeError('Application readiness failed; database and media volumes retained')
 run(command+['run','--rm','--no-deps','migrate','node',app['operator'],'probe'])
 run(['curl','--fail','--silent','--show-error','--retry','3','--max-time','20','https://'+app['domain']+'/'],stdout=subprocess.DEVNULL)
 state={'application':args.application,'imageTag':args.image_tag,'release':str(release),'backup':str(backup),'driveFirst':True,'ready':True}
 temporary=home/'state'/(args.application+'-deployment.tmp');temporary.write_text(json.dumps(state));temporary.replace(home/'state'/(args.application+'-deployment.json'))
 (original/'MANAGED-BY-TH-SERVER.md').write_text('This application is managed by TH-RPI.Server. Use the GitHub deployment workflow and the recorded release under ~/th-platform. Do not recreate it with the old standalone compose command. Existing .env and data volumes are preserved.\n')
 print(json.dumps({'application':args.application,'ready':True,'driveFirst':True,'imageTag':args.image_tag}))
except Exception:
 maintenance.write_text('Migration needs operator attention; original database backup and volumes retained\n')
 print('Deployment stopped safely. Existing database backup: '+str(backup))
 raise
