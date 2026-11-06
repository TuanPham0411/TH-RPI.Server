import pg from 'pg';
import {readFile,writeFile} from 'node:fs/promises';
import {pathToFileURL} from 'node:url';
import {restore} from '../storage/operator.mjs';
import {tokenSupplier,ensureRoot} from '../storage/oauth.mjs';
import {GoogleDriveTransport} from '../storage/google-drive.mjs';
import {Coordinator} from '../storage/coordinator.mjs';
import {cachedJournal,verifiedDownloader} from './recovery-files.mjs';

async function main(){
 const config=JSON.parse(await readFile('/config/runtime.json','utf8'));
 const accessToken=tokenSupplier({...config.oauth,credentialsPath:'/config/drive-credentials.json'});
 const transport=new GoogleDriveTransport({rootId:await ensureRoot(accessToken,fetch,config.driveRootName),accessToken});
 const coordinator=new Coordinator({transport,wrappingSecret:config.oauth.clientSecret});
 const history=await coordinator.history('ai-agent');
 await transport.list('ai-agent/events/');
 const markers=new Map(history.map(marker=>[marker.id,marker]));
 const journal=await cachedJournal(async(path,value)=>{
  if(path==='/v1/history')return {history};
  if(path==='/v1/event'&&markers.has(value.id))return coordinator.event('ai-agent',markers.get(value.id));
  throw Error('Recovery journal is read-only');
 },{application:'ai-agent',progress:(completed,total)=>console.log(JSON.stringify({phase:'verified-journal',completed,total}))});
 let reusedMedia=0,downloadedMedia=0;
 const restoreFile=verifiedDownloader({root:process.env.FILES_ROOT||'/var/lib/th-ai/files',
  fetchReference:async reference=>fetch('https://www.googleapis.com/drive/v3/files/'+encodeURIComponent(reference.id)+'?alt=media',{headers:{Authorization:'Bearer '+await accessToken()},signal:AbortSignal.timeout(300000)}),
  onFile:({reused})=>{if(reused)reusedMedia++;else downloadedMedia++;console.log(JSON.stringify({phase:'verified-media',reusedMedia,downloadedMedia}));}});
 const db=new pg.Client({connectionString:process.env.DATABASE_URL,options:'-c th_storage.authorized=1'});
 await db.connect();
 try{
  const result=await restore(db,journal,{confirmEmpty:true,restoreFile});
  console.log(JSON.stringify({restored:true,...result,reusedMedia,downloadedMedia}));
 }finally{await db.end();}
}

if(process.argv[1]&&import.meta.url===pathToFileURL(process.argv[1]).href){
 main().catch(async error=>{
  await writeFile('/var/lib/th-ai/files/.ai-recovery-error.json',JSON.stringify({message:error.message,code:error.code||null}),{mode:0o600}).catch(()=>{});
  console.error('AI recovery operator failed; data and private diagnostic retained');process.exitCode=1;
 });
}
