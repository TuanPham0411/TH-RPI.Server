import {readFile,lstat} from 'node:fs/promises';
import {readFileSync} from 'node:fs';
import {tokenSupplier} from '../storage/oauth.mjs';
import {GoogleDriveTransport} from '../storage/google-drive.mjs';
import {Coordinator} from '../storage/coordinator.mjs';
import {verifiedDownloader} from './recovery-files.mjs';

let phase='initialization';
try{
 const input=JSON.parse(readFileSync(0,'utf8'));
 const config=JSON.parse(await readFile('/config/runtime.json','utf8'));
 const root=JSON.parse(await readFile('/state/drive-root.json','utf8'));
 const token=tokenSupplier({...config.oauth,credentialsPath:'/config/drive-credentials.json'});
 const transport=new GoogleDriveTransport({rootId:root.folderId,accessToken:token});
 const coordinator=new Coordinator({transport,wrappingSecret:config.oauth.clientSecret});
 phase='verify-history';const history=await coordinator.history('portfolio');
 const position=history.findIndex(marker=>marker.id===input.head);
 if(position<0)throw Error('Database head is absent from verified Drive history');
 await transport.list('portfolio/events/');
 const wanted=new Map(input.photos.map(row=>['portfolio/'+row.id,row]));
 const references=new Map();
 phase='verify-events';
 for(let offset=0;offset<=position;offset+=4){
  const packets=await Promise.all(history.slice(offset,Math.min(offset+4,position+1)).map(marker=>coordinator.event('portfolio',marker)));
  for(const packet of packets)for(const file of packet.files||[]){
   const row=wanted.get(file.path);
   if(row&&Number(row.size)===file.size&&row.type===file.type)references.set(file.path,file);
  }
 }
 let restored=0,reused=0;
 phase='restore-verified-files';
 console.log(JSON.stringify({archiveMatches:references.size}));
 const download=verifiedDownloader({root:'/cache/files',fetchReference:file=>transport.request('files/'+encodeURIComponent(file.id)+'?alt=media'),onFile:result=>{if(result.reused)reused++;else restored++;}});
 for(const file of references.values())await download(file);
 let missing=0;
 for(const row of input.photos){try{const info=await lstat('/cache/files/portfolio/'+row.id);if(!info.isFile()||info.size!==Number(row.size))missing++;}catch(error){if(error.code==='ENOENT')missing++;else throw error;}}
 console.log(JSON.stringify({photosRecords:input.photos.length,verifiedArchiveMatches:references.size,restoredFromDrive:restored,alreadyPresent:reused,missingPhotosCaches:missing}));
}catch(error){
 const known=['Invalid verified recovery reference','Existing recovery file','Recovery media checksum differs','Recovery media size differs','Missing or corrupt event','Invalid event identity','Drive OAuth refresh failed','Authenticated Drive owner differs','Database head is absent'];
 const provider=String(error.message).match(/Drive request failed \((\d+)\) \[([a-zA-Z,]+)\]/);
 console.error(JSON.stringify({archiveRecoveryFailed:true,phase,code:error.code||null,reason:known.find(value=>String(error.message).includes(value))||null,providerStatus:provider?.[1]||null,providerReason:provider?.[2]||null}));
 process.exitCode=1;
}
