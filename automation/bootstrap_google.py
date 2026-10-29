#!/usr/bin/env python3
"""Reuse only the existing OAuth client; never copy another application's tokens."""
import pathlib,json,urllib.request
def main():
 home=pathlib.Path.home()/'th-platform';config=json.loads((home/'config/runtime.json').read_text())
 values=dict(line.split('=',1) for line in (home/'apps/ai-agent.env').read_text().splitlines() if '=' in line and not line.startswith('#'))
 body=json.dumps({'clientId':config['oauth']['clientId'],'clientSecret':config['oauth']['clientSecret']}).encode()
 request=urllib.request.Request('http://127.0.0.1:3100/api/internal/bootstrap-google',data=body,headers={'Content-Type':'application/json','Authorization':'Bearer '+values['TH_STORAGE_TOKEN'].strip().strip('"')},method='POST')
 with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request,timeout=120) as response:
  result=json.loads(response.read(10000));assert result.get('configured') and result.get('durable')
 print(json.dumps({'googleOAuthClientReused':True,'providerTokensCopied':False}))
if __name__=='__main__':main()
