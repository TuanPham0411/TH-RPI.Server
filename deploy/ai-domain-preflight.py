#!/usr/bin/env python3
"""Read-only deployment facts. No credential values or process arguments are printed."""
import json
import pathlib
import re
import socket
from urllib.parse import urlsplit


def environment(path):
    if not path.is_file():return {'exists':False,'setNames':[]}
    keys=[]
    for line in path.read_text().splitlines():
        key,sep,value=line.partition('=')
        if sep and re.fullmatch('[A-Z_][A-Z0-9_]*',key.strip()) and value.strip().strip('\"\x27'):
            keys.append(key.strip())
    return {'exists':True,'setNames':sorted(keys)}


def inspect(root=pathlib.Path('/host')):
    result={'hostname':(root/'etc/hostname').read_text().strip(),'environments':[],'configurations':[],'tunnelConfigs':[]}
    for user in ('thadmin','sika1'):
        base=root/'home'/user
        for path in (base/'apps/ai-agent/.env',base/'th-platform/apps/ai-agent.env'):
            result['environments'].append({'path':'/'+str(path.relative_to(root)),**environment(path)})
        for name in ('runtime.json','drive-credentials.json','ai-studio/firebase-service-account.ai-agent-67316.json'):
            path=base/'th-platform/config'/name
            row={'user':user,'file':name,'exists':path.is_file()}
            if path.is_file():
                try:
                    value=json.loads(path.read_text())
                    if name=='runtime.json':
                        oauth=value.get('oauth',{});row.update(oauthConfigured=bool(oauth.get('clientId') and oauth.get('clientSecret')),applicationTokens=sorted(value.get('tokens',{})),aiPublicOrigin=value.get('origins',{}).get('ai-agent'))
                    elif name=='drive-credentials.json':row['offlineAuthorizationConfigured']=bool(value.get('refreshToken'))
                    else:row.update(projectId=value.get('project_id'),serviceAccountConfigured=bool(value.get('private_key') and value.get('client_email')))
                except (ValueError,OSError):row['validJson']=False
            result['configurations'].append(row)
    for relative in ('etc/cloudflared/config.yml','etc/cloudflared/config.yaml','root/.cloudflared/config.yml','home/sika1/.cloudflared/config.yml','home/thadmin/.cloudflared/config.yml'):
        path=root/relative
        if not path.is_file():continue
        routes=[];hostname=None
        for line in path.read_text().splitlines():
            match=re.match(r'\s*-?\s*hostname:\s*[\"\x27]?([A-Za-z0-9.*_-]+)',line)
            if match:hostname=match.group(1)
            match=re.match(r'\s*service:\s*[\"\x27]?(https?://[^\s\"\x27]+)',line)
            if match:
                url=urlsplit(match.group(1))
                if url.hostname in ('localhost','127.0.0.1','::1') and not url.username and not url.password:
                    routes.append({'hostname':hostname,'service':f'{url.scheme}://{url.netloc}'})
                hostname=None
        result['tunnelConfigs'].append({'path':'/'+relative,'routes':routes})
    return result


if __name__=='__main__':print(json.dumps(inspect(),ensure_ascii=False))
