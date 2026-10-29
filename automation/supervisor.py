#!/usr/bin/env python3
"""TH-Server timer driver and bounded, network-isolated Function executor."""
import http.server,socketserver,socket,os,pathlib,json,subprocess,tempfile,selectors,time,uuid,threading,hmac,urllib.request,sys,re
HOME=pathlib.Path.home()/'th-platform';RUN=HOME/'run';ENV=HOME/'apps/ai-agent.env'
WORKER=r'''const fs=require('node:fs'),vm=require('node:vm');(async()=>{try{const p=JSON.parse(fs.readFileSync('/input.json','utf8'));const output=await vm.runInNewContext('(async function(input){"use strict";'+p.code+'\n})(input)',{input:p.input,console:Object.freeze({log(){},warn(){},error(){}})},{timeout:1000});const text=JSON.stringify(output??null);if(text.length>180000)throw Error('Function output exceeds 180 KB.');process.stdout.write(JSON.stringify({output:JSON.parse(text)}));}catch(e){process.stdout.write(JSON.stringify({error:String(e.message).slice(0,1000)}));process.exitCode=1;}})();'''
slot=threading.BoundedSemaphore(1)
def cleanup():
 result=subprocess.run(['docker','ps','-aq','--filter','label=th.automation.function=1'],capture_output=True,text=True,timeout=20,check=True)
 ids=[value for value in result.stdout.split() if re.fullmatch('[a-f0-9]{12,64}',value)]
 if ids:subprocess.run(['docker','rm','-f',*ids],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=20,check=True)
def token():
 values=dict(line.split('=',1) for line in ENV.read_text().splitlines() if '=' in line and not line.startswith('#'))
 return values['TH_STORAGE_TOKEN'].strip().strip('"')
def execute(body):
 if not isinstance(body,dict) or not isinstance(body.get('code'),str) or len(body['code'])>24000 or len(json.dumps(body.get('input')))>200000:raise ValueError('Function code or input is too large.')
 state=json.loads((HOME/'state/ai-agent-deployment.json').read_text());sha=state['imageTag']
 if len(sha)!=40 or any(c not in '0123456789abcdef' for c in sha):raise ValueError('No immutable Function runtime image is deployed.')
 name='th-function-'+uuid.uuid4().hex
 with tempfile.TemporaryDirectory(prefix='th-function-') as folder:
  source=pathlib.Path(folder)/'input.json';source.write_text(json.dumps(body));source.chmod(0o444)
  command=['docker','run','--rm','--pull','never','--name',name,'--label','th.automation.function=1','--network','none','--read-only','--user','65534:65534','--cap-drop','ALL','--security-opt','no-new-privileges:true','--pids-limit','16','--ulimit','cpu=8:8','--memory','64m','--memory-swap','64m','--cpus','0.5','--mount',f'type=bind,src={source},dst=/input.json,readonly','--entrypoint','node','ghcr.io/tuanpham0411/th-ai-agent-api:'+sha,'--max-old-space-size=32','-e',WORKER]
  process=subprocess.Popen(command,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
  selector=selectors.DefaultSelector();selector.register(process.stdout,selectors.EVENT_READ,'out');selector.register(process.stderr,selectors.EVENT_READ,'err');out=bytearray();errors=bytearray();deadline=time.monotonic()+12
  try:
   while selector.get_map():
    if time.monotonic()>deadline:raise ValueError('Function exceeded 12 seconds including container startup.')
    for key,_ in selector.select(timeout=.2):
     chunk=os.read(key.fileobj.fileno(),8192)
     if not chunk:selector.unregister(key.fileobj);continue
     target=out if key.data=='out' else errors;target.extend(chunk)
     if len(out)>200000 or len(errors)>30000:raise ValueError('Function produced too much output.')
   process.wait(timeout=2)
   try:result=json.loads(out)
   except Exception:raise ValueError('Function did not return valid JSON.')
   if process.returncode or 'error' in result:raise ValueError(result.get('error','Function không thành công.'))
   return result
  finally:
   selector.close()
   subprocess.run(['docker','rm','-f',name],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=15)
   if process.poll() is None:process.kill()
   process.wait(timeout=5)
class Handler(http.server.BaseHTTPRequestHandler):
 def log_message(self,*args):pass
 def answer(self,status,value):
  data=json.dumps(value).encode();self.send_response(status);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
 def do_POST(self):
  if self.path!='/function' or not hmac.compare_digest(self.headers.get('Authorization',''),'Bearer '+token()):return self.answer(403,{'error':'Function authentication required'})
  length=int(self.headers.get('Content-Length','0'))
  if not 0<length<=250000:return self.answer(413,{'error':'Function request is too large.'})
  if not slot.acquire(blocking=False):return self.answer(409,{'error':'Another Function is already running.'})
  try:
   self.connection.settimeout(5);body=json.loads(self.rfile.read(length));self.answer(200,execute(body))
  except Exception as error:
   try:self.answer(400,{'error':str(error)[:1000]})
   except (BrokenPipeError,ConnectionResetError):pass
  finally:slot.release()
class Server(socketserver.ThreadingMixIn,http.server.HTTPServer):
 address_family=socket.AF_UNIX;daemon_threads=True
 def server_bind(self):socketserver.TCPServer.server_bind(self);self.server_name='th-automation';self.server_port=0
def ticks():
 opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
 while True:
  try:
   request=urllib.request.Request('http://127.0.0.1:3100/api/internal/timers/tick',data=b'{}',headers={'Content-Type':'application/json','Authorization':'Bearer '+token()},method='POST')
   with opener.open(request,timeout=20) as response:response.read(16000)
   (HOME/'state/automation-heartbeat.json').write_text(json.dumps({'active':True,'lastTick':time.time(),'protocol':1}))
  except Exception:print('Timer tick unavailable; no automatic replay of interrupted writes.',flush=True)
  time.sleep(60)
if __name__=='__main__':
 if '--cleanup' in sys.argv:cleanup();raise SystemExit(0)
 cleanup()
 RUN.mkdir(exist_ok=True);path=RUN/'automation.sock';path.unlink(missing_ok=True)
 server=Server(str(path),Handler);os.chmod(path,0o660)
 threading.Thread(target=ticks,daemon=True).start();print('TH-Server automation service ready',flush=True);server.serve_forever()
