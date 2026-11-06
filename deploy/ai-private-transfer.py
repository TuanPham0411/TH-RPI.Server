#!/usr/bin/env python3
"""Ephemeral restricted SSH transport; artifacts contain public keys only."""
import argparse,json,os,pathlib,shutil,socket,subprocess
p=argparse.ArgumentParser();p.add_argument('action',choices=['source-key','target-key','send','cleanup-source','cleanup-target']);p.add_argument('--relay',default='ai-public-relay');args=p.parse_args()
os.umask(0o077)
home=pathlib.Path.home();work=home/'th-platform'/'ai-transfer';key=work/'key';relay=pathlib.Path(args.relay);marker='th-ai-sika1-bootstrap-only'
def run(command,**kwargs):return subprocess.run(command,check=True,**kwargs)
if args.action=='source-key':
    if socket.gethostname()!='th-server':raise SystemExit('Wrong source host')
    work.mkdir(parents=True,exist_ok=True,mode=0o700)
    if key.exists():raise SystemExit('Existing transfer key retained; clean up before retry')
    run(['ssh-keygen','-q','-t','ed25519','-N','','-C',marker,'-f',str(key)])
    relay.mkdir(exist_ok=True);shutil.copyfile(str(key)+'.pub',relay/'source.pub')
elif args.action=='target-key':
    if socket.gethostname().upper()!='SIKA1':raise SystemExit('Wrong target host')
    ssh=home/'.ssh';ssh.mkdir(mode=0o700,exist_ok=True)
    if ssh.is_symlink():raise SystemExit('Refuse symlink SSH directory')
    auth=ssh/'authorized_keys'
    if auth.is_symlink():raise SystemExit('Refuse symlink authorized_keys')
    pub=(relay/'source.pub').read_text().strip()
    if not pub.startswith('ssh-ed25519 ') or '\n' in pub or not pub.endswith(marker):raise SystemExit('Invalid transfer public key')
    binary=home/'th-platform/bin';binary.mkdir(parents=True,exist_ok=True,mode=0o700)
    receiver=binary/'receive-ai-bootstrap.py';shutil.copyfile(pathlib.Path(__file__).with_name('receive-ai-bootstrap.py'),receiver);receiver.chmod(0o700)
    existing=auth.read_text() if auth.exists() else ''
    if marker in existing:raise SystemExit('Existing transfer authorization retained')
    with auth.open('a') as file:file.write('\nrestrict,command="/usr/bin/python3 '+str(receiver)+'" '+pub+'\n')
    auth.chmod(0o600)
    shutil.copyfile('/etc/ssh/ssh_host_ed25519_key.pub',relay/'target-host.pub')
elif args.action=='send':
    pub=(relay/'target-host.pub').read_text().split()
    if len(pub)<2 or pub[0]!='ssh-ed25519':raise SystemExit('Invalid trusted target host key')
    known=work/'known_hosts';known.write_text('100.91.189.78 '+pub[0]+' '+pub[1]+'\n')
    script=pathlib.Path(__file__).with_name('ai-recovery-source.py').resolve()
    producer=subprocess.Popen(['docker','run','--rm','--network','none','--read-only','--user','0','--cap-drop','ALL','--cap-add','DAC_OVERRIDE','--mount','type=bind,src=/,dst=/host,readonly','--mount',f'type=bind,src={script},dst=/inspect.py,readonly','--entrypoint','python3','ghcr.io/tuanpham0411/th-rpi-console:0dab2b0a6e61b26c9fb345d89503ac57f1301597','/inspect.py','credentials'],stdout=subprocess.PIPE)
    try:
        run(['ssh','-i',str(key),'-o','IdentitiesOnly=yes','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','UserKnownHostsFile='+str(known),'-o','ConnectTimeout=20','sika1@100.91.189.78','receive'],stdin=producer.stdout,timeout=60)
    finally:
        producer.stdout.close()
        if producer.wait(timeout=15):raise SystemExit('Credential producer failed')
elif args.action=='cleanup-source':
    for path in [key,pathlib.Path(str(key)+'.pub'),work/'known_hosts']:path.unlink(missing_ok=True)
else:
    auth=home/'.ssh/authorized_keys'
    if auth.exists() and not auth.is_symlink():
        lines=auth.read_text().splitlines(keepends=True)
        auth.write_text(''.join(line for line in lines if marker not in line));auth.chmod(0o600)
    (home/'th-platform/bin/receive-ai-bootstrap.py').unlink(missing_ok=True)
