#!/usr/bin/env python3
"""Reconnect the retained Drive broker and inspect Portfolio's recovery metadata.
Credentials stay on the host. Never publish an empty replacement application.
"""
import argparse
import fcntl
import http.client
import json
import os
import pathlib
import re
import socket
import subprocess
import time
import urllib.parse
import urllib.request

HOME = pathlib.Path.home() / 'th-platform'


def run(args, **options):
    result = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **options)
    if result.returncode:
        folder=HOME/'state/portfolio-recovery'
        folder.mkdir(parents=True,exist_ok=True,mode=0o700)
        error_log=folder/'last-command-error.log'
        error_log.write_bytes(result.stderr[-32768:])
        error_log.chmod(0o600)
        raise RuntimeError('Command failed: ' + args[0] + ' (details retained locally; no credentials printed)')
    return result.stdout


class UnixConnection(http.client.HTTPConnection):
    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(str(HOME / 'run/storage.sock'))


def broker(config, application, path, value):
    connection = UnixConnection('localhost', timeout=300)
    try:
        connection.request('POST', path, body=json.dumps(value).encode(), headers={
            'Content-Type': 'application/json', 'Authorization': 'Bearer ' + config['tokens'][application],
            'X-TH-Application': application})
        response = connection.getresponse()
        body = response.read()
        if response.status != 200:
            raise RuntimeError('Drive broker request failed: ' + str(response.status))
        return json.loads(body)
    finally:
        connection.close()


def inspect_storage(source):
    config_path = HOME / 'config/runtime.json'
    credentials = HOME / 'config/drive-credentials.json'
    if not config_path.is_file() or not credentials.is_file():
        raise RuntimeError('Retained Drive configuration is required')
    config = json.loads(config_path.read_text())
    state = json.loads((HOME / 'state/deployment.json').read_text())
    image_tag = state.get('imageTag', '')
    if not re.fullmatch('[a-f0-9]{40}', image_tag):
        raise RuntimeError('A verified retained storage release is required')
    for name in ('ai-agent_ai-files', 'portfolio_media', 'ticket_media'):
        run(['docker', 'volume', 'create', name])
    release = HOME / 'releases' / image_tag
    release.mkdir(parents=True, exist_ok=True, mode=0o700)
    compose = release / 'compose.yaml'
    if not compose.exists():
        compose.write_bytes((source / 'compose.yaml').read_bytes())
    image_env = release / 'reconnect.env'
    image_env.write_text('IMAGE_TAG=' + image_tag + '\nTH_PLATFORM_HOME=' + str(HOME) + '\n')
    image_env.chmod(0o600)
    command = ['docker', 'compose', '-p', 'th-platform', '--env-file', str(image_env), '-f', str(compose)]
    print(json.dumps({'phase':'storage-image','release':image_tag}),flush=True)
    run(command + ['pull'])
    run(command + ['up', '-d', '--no-build'])
    print(json.dumps({'phase':'storage-readiness'}),flush=True)
    for attempt in range(300):
        try:
            portfolio_history = broker(config, 'portfolio', '/v1/history', {})['history']
            break
        except (OSError, RuntimeError, ValueError):
            if attempt and attempt%10==0:
                restarts=int(run(['docker','inspect','--format','{{.RestartCount}}','th-platform-storage-1']).strip())
                if restarts>=3:
                    logs=run(['docker','logs','--tail','40','th-platform-storage-1']).decode(errors='replace')
                    reason=re.search(r'Drive OAuth refresh failed \(\d+\)|Drive owner or offline grant differs|Authenticated Drive owner differs|Missing or corrupt event',logs)
                    raise RuntimeError('Drive broker repeatedly restarted: '+(reason.group(0) if reason else 'inspect private broker logs on host'))
            if attempt == 299:
                raise RuntimeError('Drive broker did not become ready. Check retained OAuth authorization; no Portfolio database or site was published.')
            time.sleep(2)
    if not portfolio_history:
        raise RuntimeError('Portfolio has no Drive history; refusing an empty replacement')
    print(json.dumps({'phase':'schema-snapshot','portfolioOperations':len(portfolio_history)}),flush=True)
    server_history = broker(config, 'server', '/v1/history', {})['history']
    definition = None
    for marker in reversed(server_history):
        packet = broker(config, 'server', '/v1/event', {'id': marker['id']})
        for event in packet['events']:
            if event['table'] == 'credentials' and event['new']:
                value = json.loads(event['new'])
                if value.get('kind') == 'server-configuration' and 'portfolio' in value.get('databases', {}):
                    definition = value['databases']['portfolio']
                    break
        if definition:
            break
    if not definition or not re.fullmatch('[a-f0-9]{40}', definition.get('release', '')):
        raise RuntimeError('Encrypted Drive schema snapshot for Portfolio is missing')
    target = HOME / 'state/portfolio-recovery'
    target.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = target / 'definition.json'
    path.write_text(json.dumps(definition))
    path.chmod(0o600)
    print(json.dumps({'driveConnected': True, 'portfolioOperations': len(portfolio_history),
                      'portfolioHead': portfolio_history[-1]['id'], 'release': definition['release'],
                      'schemaSnapshotAvailable': True, 'migrationCatalogs': len(definition['migrations'])}), flush=True)


def read_environment(path):
    values={}
    for line in path.read_text().splitlines():
        key,sep,value=line.partition('=')
        if sep and not key.lstrip().startswith('#'):
            values[key.strip()]=value.strip().strip('"\'')
    return values


def assert_empty_database(query):
    # Schema-only dumps are restored only into the newly initialized database.
    # No existing application's schemas, rows or volumes are ever dropped.
    if query("SELECT count(*) FROM pg_tables WHERE schemaname NOT IN ('pg_catalog','information_schema')").strip() != b'0':
        raise RuntimeError('Target database is not empty; refusing to overwrite it')


def validate_restored_counts(query):
    result=json.loads(query("SELECT json_build_object('users',(SELECT count(*) FROM portfolio.users),'admins',(SELECT count(*) FROM portfolio.users WHERE admin),'content',(SELECT count(*) FROM portfolio.content WHERE id='main'),'media',(SELECT count(*) FROM portfolio.media WHERE ready))"))
    if result['users']<1 or result['admins']<1 or result['content']!=1:
        raise RuntimeError('Restored Portfolio is missing existing accounts, administrator or main content; site remains unpublished')
    return result


def restore_portfolio(source):
    config=json.loads((HOME/'config/runtime.json').read_text())
    definition=json.loads((HOME/'state/portfolio-recovery/definition.json').read_text())
    tag=definition['release']
    if not re.fullmatch('[a-f0-9]{40}',tag):raise RuntimeError('Invalid recovery release')
    environment=pathlib.Path.home()/'apps/portfolio/.env'
    values=read_environment(environment)
    for name in ('POSTGRES_PASSWORD','APP_DB_PASSWORD','PORTFOLIO_ORIGIN','OWNER_EMAIL'):
        if not values.get(name):raise RuntimeError('Retained Portfolio environment is missing '+name)
    if values['PORTFOLIO_ORIGIN']!='https://th.io.vn':raise RuntimeError('Unexpected Portfolio origin')
    release=HOME/'apps/portfolio/recovery-releases'/tag
    release.mkdir(mode=0o700,parents=True,exist_ok=True)
    for name in ('compose.yaml','storage.compose.yaml','database-init.sh'):
        with urllib.request.urlopen('https://raw.githubusercontent.com/TuanPham0411/TH-Portfolio/'+tag+'/'+name,timeout=60) as response:
            (release/name).write_bytes(response.read())
        (release/name).chmod(0o644)
    local_env=release/'.env'
    if local_env.is_symlink():
        if local_env.resolve()!=environment.resolve():raise RuntimeError('Unexpected release environment target')
    elif local_env.exists():raise RuntimeError('Unexpected release environment file')
    else:local_env.symlink_to(environment)
    recovery_env=release/'recovery.env'
    recovery_env.write_text('IMAGE_TAG='+tag+'\nTH_STORAGE_TOKEN='+config['tokens']['portfolio']+'\nTH_STORAGE_RUN_DIR='+str(HOME/'run')+'\n')
    recovery_env.chmod(0o600)
    command=['docker','compose','-p','portfolio','--env-file',str(environment),'--env-file',str(recovery_env),'-f',str(release/'compose.yaml'),'-f',str(release/'storage.compose.yaml')]
    print(json.dumps({'phase':'portfolio-image','release':tag}),flush=True)
    run(command+['pull'])
    guard=HOME/'run/maintenance-portfolio'
    guard.write_text('Portfolio recovery in progress\n')
    run(command+['stop','portfolio'])
    run(command+['up','-d','--no-build','database'])
    for attempt in range(60):
        try:
            if run(['docker','inspect','--format','{{.State.Health.Status}}','portfolio-database-1']).strip()==b'healthy':break
        except RuntimeError:pass
        time.sleep(2)
    else:raise RuntimeError('Recovery database did not become healthy')
    def query(sql):
        return run(['docker','exec','-i','portfolio-database-1','psql','-X','-v','ON_ERROR_STOP=1','-U','postgres','-d','th_portfolio','-At'],input=sql.encode())
    progress=HOME/'state/portfolio-recovery/progress.json'
    previous=json.loads(progress.read_text()) if progress.exists() else {}
    if previous.get('restored') and previous.get('release')!=tag:raise RuntimeError('Recovery progress belongs to a different release')
    if not previous.get('restored'):
        assert_empty_database(query)
        print(json.dumps({'phase':'schema-restore'}),flush=True)
        query(definition['schema'])
        for catalog in definition['migrations']:query(catalog)
        query('INSERT INTO th_storage.head(id,operation,enforced) VALUES(true,null,false)')
        image='ghcr.io/tuanpham0411/th-portfolio-app:'+tag
        run(['docker','run','--rm','--network','none','--user','0','--mount','type=volume,src=portfolio_media,dst=/data',image,'node','-e',"const fs=require('node:fs');fs.mkdirSync('/data/files',{recursive:true});fs.chownSync('/data',1000,1000);fs.chownSync('/data/files',1000,1000)"])
        print(json.dumps({'phase':'records-and-media-restore'}),flush=True)
        run(command+['run','--rm','--no-deps','-v','portfolio_media:/data','-e','FILES_ROOT=/data/files','migrate','node','dist/server/storage-operator.cjs','restore-empty'])
        counts=validate_restored_counts(query)
        restored_head=query('SELECT public.th_storage_head()').strip().decode()
        remote_head=broker(config,'portfolio','/v1/history',{})['history'][-1]['id']
        if restored_head!=remote_head:raise RuntimeError('Restored database and current Drive head differ')
        progress.write_text(json.dumps({'restored':True,'release':tag,'counts':counts,'head':restored_head}))
        progress.chmod(0o600)
    counts=validate_restored_counts(query)
    # The restore operator verifies foreign keys and downloads media with SHA-256
    # verification before committing the recovered rows.
    guard.unlink(missing_ok=True)
    run(command+['run','--rm','--no-deps','migrate','node','dist/server/storage-operator.cjs','probe'])
    global_guard=HOME/'run/maintenance'
    if global_guard.exists():
        names=set(run(['docker','ps','--format','{{.Names}}']).decode().split())
        if names-{'th-platform-storage-1','portfolio-database-1'}:raise RuntimeError('Global maintenance fence is active while other containers run')
        global_guard.replace(HOME/'state/portfolio-recovery/previous-global-maintenance')
    run(command+['up','-d','--no-build','--no-deps','portfolio'])
    for attempt in range(60):
        try:
            with urllib.request.urlopen('http://127.0.0.1:8080/api/health',timeout=5) as response:
                if json.load(response).get('ok'):break
        except (OSError,ValueError):pass
        time.sleep(2)
    else:raise RuntimeError('Portfolio application did not become healthy; route was not enabled')
    with urllib.request.urlopen('http://127.0.0.1:8080/api/portfolio/content/',timeout=20) as response:
        content=json.load(response)
    if not content:raise RuntimeError('Restored Portfolio content endpoint returned empty data')
    route_backup=HOME/'state/portfolio-recovery/nginx'
    route_backup.mkdir(mode=0o700,exist_ok=True)
    helper_image='ghcr.io/tuanpham0411/th-rpi-console:99a1c43518ac4f70165771c7ecb07c60268c8d11'
    run(['docker','pull',helper_image])
    route_command=['docker','run','--rm','--network','none','--pid','host','--user','0',
                   '--cap-drop','ALL','--cap-add','SYS_CHROOT','--cap-add','DAC_OVERRIDE',
                   '--mount','type=bind,src=/,dst=/host,readonly',
                   '--mount','type=bind,src=/etc/nginx/sites-enabled,dst=/host/etc/nginx/sites-enabled',
                   '--mount','type=bind,src=/run/systemd,dst=/host/run/systemd',
                   '--mount','type=bind,src=/run/dbus,dst=/host/run/dbus,readonly',
                   '--tmpfs','/host/var/log/nginx:rw,nosuid,nodev,noexec,size=16m',
                   '--tmpfs','/host/var/lib/nginx:rw,nosuid,nodev,noexec,size=16m',
                   '--mount','type=bind,src='+str(source/'deploy/portfolio-nginx.py')+',dst=/control/portfolio-nginx.py,readonly',
                   '--mount','type=bind,src='+str(route_backup)+',dst=/backup',
                   helper_image,'python3','/control/portfolio-nginx.py']
    print(json.dumps({'phase':'public-route'}),flush=True)
    run(route_command)
    try:
        with urllib.request.urlopen('https://th.io.vn/api/health',timeout=30) as response:
            if not json.load(response).get('ok'):raise RuntimeError('Public Portfolio health failed')
        with urllib.request.urlopen('https://th.io.vn/api/portfolio/content/',timeout=30) as response:
            if not json.load(response):raise RuntimeError('Public Portfolio content is empty')
    except Exception:
        run(route_command+['rollback'])
        raise RuntimeError('Public verification failed; previous Nginx route restored, recovered data retained')
    # Register the recovered layout for later normal managed deployments.
    app_env=HOME/'apps/portfolio.env'
    app_env.write_text('TH_STORAGE_TOKEN='+config['tokens']['portfolio']+'\nTH_STORAGE_RUN_DIR='+str(HOME/'run')+'\n')
    app_env.chmod(0o600)
    (release/'image.env').write_text('IMAGE_TAG='+tag+'\n')
    state={'application':'portfolio','imageTag':tag,'release':str(release),'ready':True,'driveFirst':True,'restoredFromDrive':True}
    (HOME/'state/portfolio-deployment.json').write_text(json.dumps(state))
    print(json.dumps({**state,'restoredCounts':counts,'publicHealthVerified':True,'publicContentVerified':True}),flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=pathlib.Path, required=True)
    parser.add_argument('--restore', action='store_true')
    args = parser.parse_args()
    if os.getuid() != 1000:
        raise SystemExit('Run as the existing management user, UID 1000')
    os.umask(0o077)
    HOME.mkdir(parents=True, exist_ok=True)
    with open(HOME / 'deploy.lock', 'a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        inspect_storage(args.source.resolve())
        if args.restore:restore_portfolio(args.source.resolve())


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        raise SystemExit(str(error) if isinstance(error, RuntimeError) else 'Recovery preflight failed; private configuration and remote history were preserved')
