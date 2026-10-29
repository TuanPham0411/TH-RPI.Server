import {readFile,writeFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {tokenSupplier,ensureRoot} from './storage/oauth.mjs';
import {GoogleDriveTransport} from './storage/google-drive.mjs';
import {Coordinator} from './storage/coordinator.mjs';
const stage='/state/ticket-transition/';
const config=JSON.parse(await readFile('/config/runtime.json','utf8'));
const token=tokenSupplier({...config.oauth,credentialsPath:'/config/drive-credentials.json'});
const rootId=await ensureRoot(token);
const transport=new GoogleDriveTransport({rootId,accessToken:token});
const source=new Coordinator({transport,wrappingSecret:config.oauth.clientSecret});
const digest=bytes=>createHash('sha256').update(bytes).digest('hex');
const action=process.argv.at(-1);
async function save(path,value){await writeFile(stage+path,JSON.stringify(value),{mode:0o600});}
async function fileBytes(id){return Buffer.from(await (await transport.request('files/'+id+'?alt=media')).arrayBuffer());}
async function replace(id,bytes){
 await transport.request('files/'+id+'?uploadType=media',{method:'PATCH',headers:{'Content-Type':'application/json'},body:bytes},true);
 if(!(await fileBytes(id)).equals(bytes))throw Error('Drive transition readback differs');
}
if(action==='export'){
 const names=Object.keys(config.tokens).filter(x=>!['server','ai-agent','portfolio','ticket'].includes(x));
 if(names.length!==1)throw Error('Expected one source Ticket namespace');
 const application=names[0],folder=await transport.children(rootId,application);
 if(folder.length!==1||folder[0].mimeType!=='application/vnd.google-apps.folder')throw Error('Source folder is ambiguous');
 const collision=await transport.children(rootId,'ticket');if(collision.length)throw Error('Target Ticket folder already exists');
 const history=await source.history(application),packets=[];
 for(const marker of history){
  const eventKey=application+'/events/'+marker.id+'.json',markerKey=application+'/commits/'+marker.id+'.json';
  const raw=await transport.get(eventKey),rawMarker=await transport.get(markerKey),packet=await source.event(application,marker);
  const protectedPacket=JSON.parse(raw);
  packets.push({marker,packet,raw:raw.toString('base64'),rawMarker:rawMarker.toString('base64'),sealed:protectedPacket.events.map(e=>({old:typeof e.old==='object'&&e.old!==null,new:typeof e.new==='object'&&e.new!==null})),eventId:(await transport.metadata(eventKey)).file.id,markerId:(await transport.metadata(markerKey)).file.id});
 }
 await save('drive-source.json',{application,folderId:folder[0].id,rootId,packets});
 console.log(JSON.stringify({exported:true,operations:packets.length}));
}else if(action==='apply'){
 const before=JSON.parse(await readFile(stage+'drive-source.json','utf8'));
 const target=JSON.parse(await readFile(stage+'drive-target.json','utf8'));
 if(before.packets.length!==target.packets.length||before.folderId!==target.folderId)throw Error('Transition inventory differs');
 await save('drive-changing.json',{started:true});
 for(let i=0;i<target.packets.length;i++){
  const original=before.packets[i],entry=target.packets[i],packet=entry.packet;
  if(original.marker.id!==packet.id||packet.application!=='ticket'||packet.parent!==original.marker.parent)throw Error('Transition identity differs');
  const protectedEvents=packet.events.map((event,index)=>({...event,...Object.fromEntries(['old','new'].map(field=>[field,original.sealed[index][field]&&event[field]!==null?source.seal(event[field],'ticket:'+packet.id+':'+index+':'+field):event[field]]))}));
  const bytes=Buffer.from(JSON.stringify({...packet,events:protectedEvents}));
  const marker=Buffer.from(JSON.stringify({...original.marker,sha256:digest(bytes)}));
  await replace(original.eventId,bytes);await replace(original.markerId,marker);
  console.log(JSON.stringify({rewritten:i+1,total:target.packets.length}));
 }
 const response=await transport.request('files/'+before.folderId+'?fields=id,name',{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:'ticket'})});
 const folder=await response.json();if(folder.id!==before.folderId||folder.name!=='ticket')throw Error('Drive folder rename differs');
 class TargetCoordinator extends Coordinator{checkApp(app){if(app!=='ticket')throw Error('Invalid transition target');}}
 const verify=new TargetCoordinator({transport:new GoogleDriveTransport({rootId,accessToken:token}),wrappingSecret:config.oauth.clientSecret});
 const history=await verify.history('ticket');if(history.length!==target.packets.length)throw Error('Rewritten history length differs');
 for(let i=0;i<history.length;i++){
  const packet=await verify.event('ticket',history[i]);
  if(JSON.stringify(packet.events)!==JSON.stringify(target.packets[i].packet.events))throw Error('Rewritten row events differ');
 }
 await save('drive-verified.json',{verified:true,head:history.at(-1)?.id,folderId:folder.id});
 console.log(JSON.stringify({driveRenamed:true,historyVerified:true,operations:history.length}));
}else if(action==='rollback'){
 const before=JSON.parse(await readFile(stage+'drive-source.json','utf8'));
 for(const entry of before.packets){await replace(entry.eventId,Buffer.from(entry.raw,'base64'));await replace(entry.markerId,Buffer.from(entry.rawMarker,'base64'));}
 await transport.request('files/'+before.folderId,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:before.application})});
 console.log(JSON.stringify({rollbackVerified:true}));
}else throw Error('Choose export, apply or rollback');
