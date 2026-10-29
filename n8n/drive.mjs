import {readFile,writeFile,rm} from 'node:fs/promises';
import {createWriteStream} from 'node:fs';
import {Readable} from 'node:stream';import {pipeline} from 'node:stream/promises';
import {tokenSupplier} from '/app/storage/oauth.mjs';
import {GoogleDriveTransport} from '/app/storage/google-drive.mjs';
import {recoveryKey,sealFile,openFile,digestFile} from '/backup/codec.mjs';
const input=JSON.parse(await readFile('/backup/input.json','utf8'));
const config=JSON.parse(await readFile('/config/runtime.json','utf8'));
const drive=new GoogleDriveTransport({rootId:input.rootId,accessToken:tokenSupplier({...config.oauth,credentialsPath:'/config/drive-credentials.json'})});
const key=recoveryKey(config.oauth.clientSecret);
if(input.action==='upload'){
 const files=[];
 for(const source of ['database.sql.gz','workflows.json','configuration.json','volume.tar.gz']){
  const name=source.replace(/\.(sql\.gz|tar\.gz|json)$/,'')+'.json';
  const context='th-n8n:'+input.id+':'+name;
  const sealed='/backup/'+name;
  await sealFile('/backup/'+source,sealed,key,context);
  const digest=await digestFile(sealed),path=`ai-agent/n8n/backups/${input.id}/${name}`;
  const stored=await drive.putLocalFile(path,sealed,{...digest,type:'application/json'});
  files.push({...stored,context,source,name});
 }
 const manifest={format:1,id:input.id,createdAt:input.createdAt,n8nVersion:input.n8nVersion,files,tableCounts:input.tableCounts,encrypted:true,binaryStorage:'postgresql'};
 const bytes=Buffer.from(JSON.stringify(manifest));const path=`ai-agent/n8n/backups/${input.id}/manifest.json`;
 await drive.put(path,bytes,'application/json');
 const readback=await drive.get(path);if(!readback?.equals(bytes))throw Error('n8n manifest read-back mismatch');
 await writeFile('/backup/manifest.json',bytes,{mode:0o600});
 console.log(JSON.stringify({n8nBackup:input.id,verified:true,files:files.length}));
}else if(input.action==='verify'){
 const manifest=JSON.parse((await drive.get(`ai-agent/n8n/backups/${input.id}/manifest.json`)).toString());
 for(const file of manifest.files){
  const response=await drive.request(`files/${encodeURIComponent(file.id)}?alt=media`);
  const path='/backup/remote-'+file.name;
  await pipeline(Readable.fromWeb(response.body),createWriteStream(path,{mode:0o600}));
  const digest=await digestFile(path);if(digest.sha256!==file.sha256||digest.size!==file.size)throw Error('Drive n8n checksum differs');
  await openFile(path,'/backup/restored-'+file.source,key,file.context);
 }
 await writeFile('/backup/remote-manifest.json',JSON.stringify(manifest),{mode:0o600});
 console.log(JSON.stringify({n8nBackup:input.id,downloadVerified:true,decryptionVerified:true}));
}else throw Error('Unsupported backup operation');
