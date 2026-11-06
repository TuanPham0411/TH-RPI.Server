#!/usr/bin/env python3
"""CMS AuthEnvelopedData relay: recipient private key remains on SIKA1."""
import argparse,os,pathlib,socket,subprocess,sys
p=argparse.ArgumentParser();p.add_argument('action',choices=['key','seal','receive','cleanup']);args=p.parse_args();os.umask(0o077)
work=pathlib.Path.home()/'th-platform/ai-sealed-relay';relay=pathlib.Path('ai-public-relay');relay.mkdir(exist_ok=True)
def run(command,**kwargs):
    result=subprocess.run(command,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=120,**kwargs)
    if result.returncode:raise RuntimeError('Private relay operation failed: '+command[0])
    return result.stdout
if args.action=='key':
    if socket.gethostname().upper()!='SIKA1':raise SystemExit('Wrong recipient host')
    if (pathlib.Path.home()/'th-platform/config/runtime.json').exists():raise SystemExit('Existing configuration retained; no new relay required')
    work.mkdir(parents=True,exist_ok=True,mode=0o700)
    if (work/'key.pem').exists():raise SystemExit('Existing relay key retained; clean up before retry')
    run(['openssl','req','-x509','-newkey','rsa:3072','-nodes','-keyout',str(work/'key.pem'),'-out',str(work/'recipient.pem'),'-subj','/CN=SIKA1-AI-Recovery','-days','1'])
    (relay/'recipient.pem').write_bytes((work/'recipient.pem').read_bytes())
elif args.action=='seal':
    if socket.gethostname()!='th-server':raise SystemExit('Wrong source host')
    script=pathlib.Path(__file__).with_name('ai-recovery-source.py').resolve()
    plaintext=run(['docker','run','--rm','--log-driver','none','--network','none','--read-only','--user','0','--cap-drop','ALL','--cap-add','DAC_OVERRIDE','--mount','type=bind,src=/,dst=/host,readonly','--mount',f'type=bind,src={script},dst=/inspect.py,readonly','--entrypoint','python3','ghcr.io/tuanpham0411/th-rpi-console:0dab2b0a6e61b26c9fb345d89503ac57f1301597','/inspect.py','credentials'])
    if not 0<len(plaintext)<100000:raise SystemExit('Invalid bootstrap size')
    sealed=run(['openssl','cms','-encrypt','-aes-256-gcm','-binary','-outform','DER',str(relay/'recipient.pem')],input=plaintext)
    (relay/'bootstrap.cms').write_bytes(sealed)
    print('Bootstrap sealed to SIKA1 recipient; plaintext never written to artifacts')
elif args.action=='receive':
    if socket.gethostname().upper()!='SIKA1':raise SystemExit('Wrong recipient host')
    sealed=(relay/'bootstrap.cms').read_bytes()
    if len(sealed)>120000:raise SystemExit('Invalid sealed bootstrap size')
    plaintext=run(['openssl','cms','-decrypt','-binary','-inform','DER','-recip',str(work/'recipient.pem'),'-inkey',str(work/'key.pem')],input=sealed)
    result=run([sys.executable,str(pathlib.Path(__file__).with_name('receive-ai-bootstrap.py'))],input=plaintext)
    print('Authenticated bootstrap received privately by SIKA1')
else:
    for name in ['key.pem','recipient.pem']:(work/name).unlink(missing_ok=True)
    for name in ['bootstrap.cms','recipient.pem']:(relay/name).unlink(missing_ok=True)
