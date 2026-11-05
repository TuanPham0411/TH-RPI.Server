#!/usr/bin/env python3
"""Refresh the retained broker process without changing images, grants or data."""
import fcntl
import importlib.util
import json
import os
import pathlib
import time
import argparse
import re

spec = importlib.util.spec_from_file_location('recovery', pathlib.Path(__file__).with_name('portfolio-recovery.py'))
recovery = importlib.util.module_from_spec(spec)
spec.loader.exec_module(recovery)


def main(broker_sha=None):
    if os.getuid()!=1000:
        raise RuntimeError('Use the existing management runner user')
    os.umask(0o077)
    home = recovery.HOME
    with open(home/'deploy.lock','a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        config = json.loads((home/'config/runtime.json').read_text())
        broker_command=None
        broker_release=None
        if broker_sha:
            if not re.fullmatch('[a-f0-9]{40}',broker_sha):
                raise RuntimeError('A tested immutable broker image SHA is required')
            broker_release=home/'releases'/broker_sha
            broker_release.mkdir(parents=True,exist_ok=True,mode=0o700)
            compose=broker_release/'compose.yaml'
            canonical=pathlib.Path(__file__).resolve().parents[1]/'compose.yaml'
            if not compose.exists():compose.write_bytes(canonical.read_bytes())
            env=broker_release/'reconnect.env'
            env.write_text('IMAGE_TAG='+broker_sha+'\nTH_PLATFORM_HOME='+str(home)+'\n')
            broker_command=['docker','compose','-p','th-platform','--env-file',str(env),'-f',str(compose)]
            print(json.dumps({'phase':'pull-tested-broker','imageTag':broker_sha}),flush=True)
            recovery.run(broker_command+['pull','storage'])
        state = json.loads((home/'state/portfolio-deployment.json').read_text())
        release = pathlib.Path(state['release'])
        if not release.is_relative_to(home/'apps/portfolio'):
            raise RuntimeError('Unexpected Portfolio release path')
        command = ['docker','compose','-p','portfolio',
                   '--env-file',str(pathlib.Path.home()/'apps/portfolio/.env'),
                   '--env-file',str(home/'apps/portfolio.env'),
                   '--env-file',str(release/'image.env'),
                   '-f',str(release/'compose.yaml'),'-f',str(release/'storage.compose.yaml')]
        guard = home/'run/maintenance-portfolio'
        if guard.exists():
            raise RuntimeError('Existing maintenance marker retained; another recovery needs attention')
        guard.write_text('Refreshing retained Drive session\n')
        try:
            print(json.dumps({'phase':'drain-portfolio-writers'}),flush=True)
            recovery.run(['docker','stop','--time','360','portfolio-portfolio-1'])
            print(json.dumps({'phase':'restart-retained-broker'}),flush=True)
            if broker_command:
                recovery.run(broker_command+['up','-d','--no-build','--no-deps','storage'])
            else:
                recovery.run(['docker','restart','--time','360','th-platform-storage-1'])
            for attempt in range(360):
                try:
                    if recovery.broker(config,'portfolio','/v1/status',{},timeout=5).get('connected'):
                        break
                except (OSError,RuntimeError,ValueError):
                    pass
                time.sleep(2)
            else:
                raise RuntimeError('Broker did not recover; configuration and data retained')
            print(json.dumps({'phase':'verify-live-history'}),flush=True)
            history = recovery.broker(config,'portfolio','/v1/history',{})['history']
            head = recovery.run(['docker','exec','portfolio-database-1','psql','-X','-U','postgres','-d','th_portfolio','-At','-c','SELECT public.th_storage_head()']).decode().strip()
            if not history or history[-1]['id']!=head:
                raise RuntimeError('Drive and database heads differ; no overwrite attempted')
            print(json.dumps({'phase':'verify-drive-first-write','operations':len(history)}),flush=True)
            result = recovery.run(command+['run','--rm','--no-deps','migrate','node','dist/server/storage-operator.cjs','probe'])
            if not json.loads(result).get('driveFirstVerified'):
                raise RuntimeError('Drive-first write verification failed')
            guard.unlink()
            recovery.run(['docker','start','portfolio-portfolio-1'])
            for attempt in range(60):
                try:
                    result = recovery.run(['curl','--fail','--silent','--show-error','--max-time','10','https://th.io.vn/api/health'])
                    if json.loads(result).get('ok'):
                        break
                except (RuntimeError,ValueError):
                    pass
                time.sleep(2)
            else:
                raise RuntimeError('Public Portfolio health failed after reconnect')
            print(json.dumps({'storageReconnected':True,'historyVerified':True,'driveFirstWriteVerified':True,'publicHealthVerified':True}),flush=True)
            if broker_release:
                deployment=home/'state/deployment.json'
                previous=json.loads(deployment.read_text())
                temporary=deployment.with_suffix('.tmp')
                temporary.write_text(json.dumps({**previous,'imageTag':broker_sha,'release':str(broker_release),'ready':True}))
                temporary.replace(deployment)
        except Exception:
            # Restore page availability while retaining the write fence on failure.
            recovery.run(['docker','start','portfolio-portfolio-1'])
            raise


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--broker-sha')
    args=parser.parse_args()
    try:
        main(args.broker_sha)
    except Exception as error:
        raise SystemExit(str(error))
