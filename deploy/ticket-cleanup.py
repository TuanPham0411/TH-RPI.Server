#!/usr/bin/env python3
"""Plan or remove retired Ticket resources after a verified Drive recovery."""
import argparse,fcntl,json,os,pathlib,re,shlex,shutil,subprocess
parser=argparse.ArgumentParser();parser.add_argument('--apply',action='store_true');args=parser.parse_args()
home=pathlib.Path.home()/'th-platform';stage=home/'state/ticket-transition'
if os.getuid()!=1000:raise SystemExit('Run as the existing server administrator.')
lock=open(home/'deploy.lock','a');fcntl.flock(lock,fcntl.LOCK_EX)
proof_path=home/'state/ticket-recovery-verified.json'
if not proof_path.exists():raise SystemExit('Complete and verify Ticket recovery before cleanup.')
proof=json.loads(proof_path.read_text());state=json.loads((home/'state/ticket-deployment.json').read_text())
if proof.get('imageTag')!=state['imageTag'] or not all(proof.get(k) for k in ['restoredFromDrive','foreignKeysVerified','mediaChecksumsVerified','exactRowsCompared']):raise SystemExit('A current, exact Ticket recovery verification is required.')
if not stage.exists():print('Retired Ticket transition resources have already been cleaned.');raise SystemExit(0)
completed=json.loads((stage/'completed.json').read_text());old=completed['oldApplication'];prefix=old.split('-')[0]
if old=='ticket' or not re.fullmatch(r'[a-z][a-z0-9-]+',old):raise SystemExit('Invalid retired namespace')
def run(command,allow_failure=False):
 result=subprocess.run(command,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
 if result.returncode and not allow_failure:raise SystemExit('Cleanup stopped; inspect resource ownership before retrying.')
 return result.stdout.decode().strip()
target=json.loads(run(['docker','inspect','ticket-ticket-1']))[0]
if not target['State']['Running'] or target['State'].get('Health',{}).get('Status')!='healthy':raise SystemExit('The new Ticket application must be healthy.')
run(['curl','-fsS','--max-time','15','https://ticket.th.io.vn/api/health'])
containers=run(['docker','ps','-aq','--filter','label=com.docker.compose.project='+old]).split()
volumes=run(['docker','volume','ls','-q','--filter','label=com.docker.compose.project='+old]).split()
if completed.get('oldVolume') not in volumes:volumes.append(completed['oldVolume'])
active_mounts=json.loads(run(['docker','inspect','th-platform-storage-1','ticket-ticket-1','ticket-database-1']))
mounted={m.get('Name') for c in active_mounts for m in c['Mounts'] if m.get('Name')}
if mounted.intersection(volumes):raise SystemExit('An active service still uses a retired volume; cleanup is forbidden.')
networks=run(['docker','network','ls','-q','--filter','label=com.docker.compose.project='+old]).split()
paths=[pathlib.Path.home()/'apps'/old,home/'apps'/old,home/'apps'/(old+'.env'),home/'state'/(old+'-deployment.json'),home/'state'/(old+'.json'),home/'state/backups'/old,home/'run'/('maintenance-'+old)]
for name in [old,prefix,'th-'+prefix,'th-'+prefix+'.ticket']:
 directory=pathlib.Path.home()/'actions-runners'/name
 if directory.exists():
  metadata=directory/'.runner'
  if metadata.exists():
   registration=json.loads(metadata.read_text(encoding='utf-8-sig'))
   if registration.get('agentName')=='th-ticket':raise SystemExit('The current runner is in a retired directory; move it before cleanup.')
  paths.append(directory)
nginx=[]
for path in pathlib.Path('/etc/nginx/sites-enabled').glob('*'):
 try:content=path.read_text()
 except (PermissionError,OSError):continue
 names=[n for group in re.findall(r'\bserver_name\s+([^;]+);',content) for n in group.split()]
 if names and set(names)=={prefix+'.th.io.vn'}:
  target_path=path.resolve()
  if target_path.parent==pathlib.Path('/etc/nginx/sites-available'):nginx.extend([str(path),str(target_path)])
plan={'retiredContainers':len(containers),'retiredVolumes':volumes,'retiredPaths':[str(p) for p in paths if p.exists()],'nginxFilesRequiringSudo':sorted(set(nginx))}
print(json.dumps(plan,ensure_ascii=False,indent=2))
if not args.apply:
 print('Review this plan, then run the same command with --apply.');raise SystemExit(0)
if containers:
 run(['docker','stop','-t','30',*containers]);run(['docker','rm',*containers])
for volume in volumes:
 if run(['docker','ps','-aq','--filter','volume='+volume]):raise SystemExit('Another container uses a retired volume; volume retained.')
 run(['docker','volume','rm',volume])
for network in networks:run(['docker','network','rm',network],allow_failure=True)
for path in paths:
 if path.is_symlink() or path.is_file():path.unlink(missing_ok=True)
 elif path.is_dir():shutil.rmtree(path)
repository='ghcr.io/tuanpham0411/th-'+old
for image in run(['docker','image','ls','--format','{{.Repository}}:{{.Tag}}']).splitlines():
 if image.startswith(repository+':'):run(['docker','image','rm',image],allow_failure=True)
shutil.rmtree(stage)
(home/'state/ticket-cleanup.json').write_text(json.dumps({'application':'ticket','dataRecoveryVerified':True,'retiredResourcesRemoved':True}))
print('Retired application, Docker data copies and runner directories removed. Active Ticket data retained.')
if nginx:
 print('Run to remove the retired Nginx host:')
 print(shlex.join(['sudo','rm','-f','--',*sorted(set(nginx))]))
 print('sudo nginx -t && sudo systemctl reload nginx')
