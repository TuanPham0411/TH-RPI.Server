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

HOME = pathlib.Path.home() / 'th-platform'


def run(args, **options):
    result = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **options)
    if result.returncode:
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
    run(command + ['pull'])
    run(command + ['up', '-d', '--no-build'])
    for attempt in range(300):
        try:
            portfolio_history = broker(config, 'portfolio', '/v1/history', {})['history']
            break
        except (OSError, RuntimeError, ValueError):
            if attempt == 299:
                raise RuntimeError('Drive broker did not become ready. Check retained OAuth authorization; no Portfolio database or site was published.')
            time.sleep(2)
    if not portfolio_history:
        raise RuntimeError('Portfolio has no Drive history; refusing an empty replacement')
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=pathlib.Path, required=True)
    args = parser.parse_args()
    if os.getuid() != 1000:
        raise SystemExit('Run as the existing management user, UID 1000')
    os.umask(0o077)
    HOME.mkdir(parents=True, exist_ok=True)
    with open(HOME / 'deploy.lock', 'a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        inspect_storage(args.source.resolve())


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        raise SystemExit(str(error) if isinstance(error, RuntimeError) else 'Recovery preflight failed; private configuration and remote history were preserved')
