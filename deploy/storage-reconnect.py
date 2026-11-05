#!/usr/bin/env python3
"""Refresh the retained broker process without changing images, grants or data."""
import fcntl
import importlib.util
import json
import os
import pathlib
import time

spec = importlib.util.spec_from_file_location('recovery', pathlib.Path(__file__).with_name('portfolio-recovery.py'))
recovery = importlib.util.module_from_spec(spec)
spec.loader.exec_module(recovery)


def main():
    if os.getuid()!=1000:
        raise RuntimeError('Use the existing management runner user')
    os.umask(0o077)
    home = recovery.HOME
    with open(home/'deploy.lock','a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        config = json.loads((home/'config/runtime.json').read_text())
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
        except Exception:
            # Restore page availability while retaining the write fence on failure.
            recovery.run(['docker','start','portfolio-portfolio-1'])
            raise


if __name__=='__main__':
    try:
        main()
    except Exception as error:
        raise SystemExit(str(error))
