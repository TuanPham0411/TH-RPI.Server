#!/usr/bin/env python3
"""Upgrade only the retained AI API/web containers on SIKA1, with private backup and rollback."""
import argparse
import copy
import datetime
import fcntl
import hashlib
import json
import os
import pathlib
import re
import shutil
import socket
import subprocess
import time
import urllib.request


def updated_compose(original, release):
    if not re.fullmatch('[a-f0-9]{40}', release):
        raise ValueError('An immutable source SHA is required')
    result = copy.deepcopy(original)
    services = result.get('services', {})
    if not {'api', 'web', 'migrate', 'db', 'storage'} <= services.keys():
        raise ValueError('Retained AI compose services are incomplete')
    for name in ('ai-db', 'ai-files'):
        if result.get('volumes', {}).get(name, {}).get('name') != 'ai-agent_' + name:
            raise ValueError('Unexpected data volume identity')
    for name in ('api', 'migrate'):
        services[name]['image'] = 'ghcr.io/tuanpham0411/th-ai-agent-api:' + release
        services[name]['environment']['TH_RELEASE'] = release
    services['web']['image'] = 'ghcr.io/tuanpham0411/th-ai-agent-web:' + release
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--release', required=True)
    parser.add_argument('--inspect', action='store_true')
    args = parser.parse_args()
    if socket.gethostname().upper() != 'SIKA1' or os.getuid() != 1000 or pathlib.Path.home().name != 'sika1':
        raise RuntimeError('Only SIKA1 / sika1 UID 1000 may run this upgrade')
    if not re.fullmatch('[a-f0-9]{40}', args.release):
        raise RuntimeError('A full source SHA is required')
    os.umask(0o077)
    platform = pathlib.Path.home() / 'th-platform'
    state = platform / 'state'
    lock = (state / 'ai-upgrade.lock').open('w')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    metadata_path = state / 'ai-agent-deployment.json'
    previous = json.loads(metadata_path.read_text())
    old_path = pathlib.Path(previous['compose']).resolve()
    if not old_path.is_relative_to(platform.resolve()) or previous.get('publicOrigin') != 'https://thienha.io.vn':
        raise RuntimeError('Unexpected retained deployment identity')
    original = json.loads(old_path.read_text())
    next_config = updated_compose(original, args.release)
    release = platform / 'releases' / ('ai-upgrade-' + args.release)
    release.mkdir(parents=True, exist_ok=True, mode=0o700)
    log_path = release / 'last-command-error.log'

    def run(command, timeout=1200):
        result = subprocess.run(command, capture_output=True, timeout=timeout)
        if result.returncode:
            log_path.write_bytes(result.stderr[-32000:])
            raise RuntimeError('Command failed; details kept privately on SIKA1: ' + command[0])
        return result.stdout

    old_base = ['docker', 'compose', '-p', 'ai-agent', '-f', str(old_path)]
    counts_sql = "SELECT json_build_object('workflows',(SELECT count(*) FROM ai_workflows),'credentials',(SELECT count(*) FROM ai_credentials),'mediaJobs',(SELECT count(*) FROM ai_media_jobs),'files',(SELECT count(*) FROM files),'active',(SELECT count(*) FROM ai_workflows WHERE active),'running',(SELECT count(*) FROM ai_runs WHERE status IN ('queued','running')))"

    def counts(base):
        return json.loads(run(base + ['exec', '-T', 'db', 'psql', '-U', 'postgres', '-d', 'th_ai', '-At', '-c', counts_sql]))

    before = counts(old_base)
    print(json.dumps({'phase': 'retained-deployment', 'release': previous.get('imageTag'), 'dataCounts': before}), flush=True)
    if args.inspect:
        return
    for target in ('api', 'web'):
        expected = next_config['services'][target]['image']
        image = json.loads(run(['docker', 'image', 'inspect', expected]))[0]
        if image.get('Architecture') != 'arm64' or image.get('Config', {}).get('Labels', {}).get('org.opencontainers.image.revision') != args.release:
            raise RuntimeError('Build the pinned ARM64 images before upgrading')
    # Check Drive acknowledgement before stopping the existing API.
    run(old_base + ['run', '--rm', '--no-deps', 'migrate', 'node', 'server/storage-operator.js', 'probe'], timeout=120)
    for _ in range(120):
        if not counts(old_base)['running']:
            break
        print(json.dumps({'phase': 'waiting-for-active-workflow'}), flush=True)
        time.sleep(5)
    else:
        raise RuntimeError('Active workflow did not finish; deployment retained')
    new_path = release / 'compose.json'
    new_path.write_text(json.dumps(next_config))
    shutil.copyfile(old_path, release / 'previous-compose.json')
    (release / 'previous-deployment.json').write_text(json.dumps(previous))
    base = ['docker', 'compose', '-p', 'ai-agent', '-f', str(new_path)]
    run(base + ['config', '--quiet'])
    switched = False
    try:
        switched = True
        run(old_base + ['stop', '-t', '360', 'api'], timeout=390)
        before = counts(old_base)
        if before['running']:
            raise RuntimeError('A workflow arrived while stopping; retained release will resume for review')
        backup = release / ('database-before-' + datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '.dump')
        with backup.open('wb') as output:
            result = subprocess.run(old_base + ['exec', '-T', 'db', 'pg_dump', '-U', 'postgres', '-Fc', 'th_ai'], stdout=output, stderr=subprocess.PIPE, timeout=180)
        if result.returncode or backup.stat().st_size < 1000:
            raise RuntimeError('Database backup was not verified; retained release will resume')
        with backup.open('rb') as source:
            verified = subprocess.run(old_base + ['exec', '-T', 'db', 'pg_restore', '--list'], stdin=source, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=30)
        if verified.returncode:
            raise RuntimeError('Backup archive cannot be read')
        print(json.dumps({'phase': 'backup-verified', 'bytes': backup.stat().st_size}), flush=True)
        run(base + ['run', '--rm', '--no-deps', 'migrate'], timeout=180)
        run(base + ['run', '--rm', '--no-deps', 'migrate', 'node', 'server/storage-operator.js', 'probe'], timeout=120)
        after = counts(base)
        for key in ('workflows', 'credentials', 'mediaJobs', 'files', 'active'):
            if before[key] != after[key]:
                raise RuntimeError('Data counts changed during upgrade: ' + key)
        run(base + ['up', '-d', '--no-build', '--no-deps', '--wait', '--wait-timeout', '180', 'api'], timeout=210)
        run(base + ['up', '-d', '--no-build', '--no-deps', '--wait', '--wait-timeout', '90', 'web'], timeout=120)
        # Compare what Nginx actually serves to the immutable web image.
        image_html = run(['docker', 'run', '--rm', '--network', 'none', '--entrypoint', 'cat', next_config['services']['web']['image'], '/usr/share/nginx/html/index.html'])
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        for endpoint in ('/api/health', '/api/ready', '/api/auth/session', '/'):
            with opener.open(urllib.request.Request('http://127.0.0.1:5050' + endpoint, headers={'Host': 'thienha.io.vn'}), timeout=30) as response:
                body = response.read()
                if response.status != 200 or endpoint == '/' and body != image_html:
                    raise RuntimeError('Local release verification failed')
        next_metadata = {**previous, 'imageTag': args.release, 'compose': str(new_path), 'upgradedAt': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'previousCompose': str(old_path), 'backup': str(backup)}
        temporary = metadata_path.with_suffix('.upgrade.tmp')
        temporary.write_text(json.dumps(next_metadata))
        temporary.replace(metadata_path)
        print(json.dumps({'deployed': True, 'release': args.release, 'origin': previous['publicOrigin'], 'retainedData': after, 'htmlSha256': hashlib.sha256(image_html).hexdigest()}), flush=True)
    except BaseException:
        if switched:
            run(old_base + ['up', '-d', '--no-build', '--no-deps', '--wait', '--wait-timeout', '180', 'api', 'web'], timeout=240)
            print(json.dumps({'rolledBack': True, 'release': previous.get('imageTag')}), flush=True)
        raise


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(json.dumps({'error': str(error)}), flush=True)
        raise SystemExit(1)
