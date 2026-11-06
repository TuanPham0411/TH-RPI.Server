// Copy an immutable AI journal into an independent root; never modify the source.
import {readFile,writeFile} from 'node:fs/promises';
import {tokenSupplier,ensureRoot} from './oauth.mjs';
import {GoogleDriveTransport} from './google-drive.mjs';
import {Coordinator} from './coordinator.mjs';
const config=JSON.parse(await readFile('/config/runtime.json','utf8'));
if(config.driveRootName!=='th-data-sika1-ai'||!config.recoverySourceRoot)throw Error('Explicit isolated recovery required');
const token=tokenSupplier({...config.oauth,credentialsPath:'/config/drive-credentials.json'});
const rootId=await ensureRoot(token,fetch,config.driveRootName);
if(rootId===config.recoverySourceRoot)throw Error('Recovery root must differ');
const source=new GoogleDriveTransport({rootId:config.recoverySourceRoot,accessToken:token});
const target=new GoogleDriveTransport({rootId,accessToken:token});
const coordinator=new Coordinator({transport:source,wrappingSecret:config.oauth.clientSecret});
console.log(JSON.stringify({phase:'source-inventory',commits:(await source.list('ai-agent/commits/')).length}));
const history=await coordinator.history('ai-agent');if(!history.length)throw Error('No AI history; refusing empty replacement');
await source.list('ai-agent/events/');
const existing=new Set(await target.list('ai-agent/commits/'));await target.list('ai-agent/events/');
if([...existing].some(key=>!source.readIndex.has(key)))throw Error('Recovery destination already has divergent writes');
const parents={};for(const part of ['events','commits'])parents[part]=await target.resolve(['ai-agent',part],true);
let baseline=null,completed=0;
for(let offset=0;offset<history.length;offset+=4){
 await Promise.all(history.slice(offset,offset+4).map(async marker=>{
  const packet=await coordinator.event('ai-agent',marker);
  if(packet.baseline&&(!baseline||history.indexOf(marker)>baseline.position))baseline={position:history.indexOf(marker),release:packet.baseline.release,tables:packet.baseline.tables,migrations:packet.baseline.migrations};
  for(const part of ['events','commits']){
   const key=`ai-agent/${part}/${marker.id}.json`,file=source.readIndex.get(key);if(!file)throw Error('Missing source object');
   let copied=target.readIndex.get(key);
   if(!copied){copied=await (await target.request(`files/${file.id}/copy?fields=id,name,mimeType,size,md5Checksum`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:file.name,parents:[parents[part]]})})).json();target.readIndex.set(key,copied);}
   if(Number(copied.size)!==Number(file.size)||!file.md5Checksum||copied.md5Checksum!==file.md5Checksum)throw Error('Recovery copy verification failed');
  }
  completed++;
 }));
 console.log(JSON.stringify({verifiedCommits:completed,total:history.length}));
}
if(!baseline)throw Error('No recoverable baseline');
const after=await coordinator.history('ai-agent');if(after.at(-1).id!==history.at(-1).id)throw Error('Source changed during recovery; retry before deployment');
const verified=await new Coordinator({transport:target,wrappingSecret:config.oauth.clientSecret}).history('ai-agent');
if(verified.length!==history.length||verified.at(-1).id!==history.at(-1).id)throw Error('Recovery chain differs');
const result={format:1,rootId,commits:verified.length,head:verified.at(-1).id,baseline,sourceRetained:true};
await writeFile('/state/ai-clone.json',JSON.stringify(result),{mode:0o600});console.log(JSON.stringify(result));
