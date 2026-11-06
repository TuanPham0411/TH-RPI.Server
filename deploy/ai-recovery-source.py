#!/usr/bin/env python3
"""Privately emit minimal AI recovery credentials, or print a redacted recovery manifest."""
import http.client,json,pathlib,socket,sys
base=pathlib.Path('/host/home/thadmin/th-platform')
config=json.loads((base/'config/runtime.json').read_text())
if sys.argv[1]=='credentials':
    payload={'oauth':config['oauth'],'drive':json.loads((base/'config/drive-credentials.json').read_text())}
    sa=base/'config/ai-studio/firebase-service-account.ai-agent-67316.json'
    if sa.is_file():payload['firebase']=json.loads(sa.read_text())
    sys.stdout.write(json.dumps(payload))
else:
    class Unix(http.client.HTTPConnection):
        def connect(self):
            self.sock=socket.socket(socket.AF_UNIX);self.sock.settimeout(300);self.sock.connect(str(base/'run/storage.sock'))
    def request(path,value):
        conn=Unix('localhost',timeout=300)
        conn.request('POST',path,json.dumps(value),{'Authorization':'Bearer '+config['tokens']['ai-agent'],'X-TH-Application':'ai-agent','Content-Type':'application/json'})
        res=conn.getresponse();data=json.loads(res.read());conn.close()
        if res.status!=200:raise RuntimeError('AI recovery source unavailable')
        return data
    history=request('/v1/history',{})['history']
    for marker in reversed(history):
        packet=request('/v1/event',{'id':marker['id']})
        if packet.get('baseline'):
            baseline=packet['baseline']
            print(json.dumps({'commits':len(history),'head':history[-1]['id'],'baselineRelease':baseline.get('release'),'tables':len(baseline['tables']),'migrationVersions':baseline.get('migrations',{})}));break
    else:raise RuntimeError('No AI baseline exists; no empty replacement will be created')
