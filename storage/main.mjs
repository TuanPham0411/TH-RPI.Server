import {readFile,mkdir,chmod,unlink,writeFile,rename} from 'node:fs/promises';
import {tokenSupplier,ensureRoot} from './oauth.mjs';
import {GoogleDriveTransport} from './google-drive.mjs';
import {attachmentWriter,attachmentReferences} from './attachments.mjs';
import {Coordinator} from './coordinator.mjs';
import {storageServer} from './http.mjs';
import {Photos} from './photos.mjs';
import {randomUUID} from 'node:crypto';
const config=JSON.parse(await readFile(process.env.TH_STORAGE_CONFIG||'/config/runtime.json','utf8'));
const token=tokenSupplier({...config.oauth,credentialsPath:'/config/drive-credentials.json'});
const rootId=await ensureRoot(token);
const transport=new GoogleDriveTransport({rootId,accessToken:token});
await mkdir('/state',{recursive:true,mode:0o700});
const indices=new Map();
async function updateIndex({application,events,files,marker}){
 const index=indices.get(application)||{};
 for(const event of events)if(event.action==='DELETE')for(const reference of attachmentReferences({...event,new:event.old}))delete index[reference.path];
 for(const file of files)index[file.path]=file;
 indices.set(application,index);
 await writeFile(`/state/${application}.tmp`,JSON.stringify({operation:marker.id,files:index}),{mode:0o600});
 await rename(`/state/${application}.tmp`,`/state/${application}.json`);
}
const coordinator=new Coordinator({transport,wrappingSecret:config.oauth.clientSecret,attachment:attachmentWriter(transport,{'ai-agent':'/source/ai','portfolio':'/source/portfolio/files','mes-ticket':'/source/mes/files'}),onCommitted:updateIndex});
let photosCredentials=null;
for(const app of Object.keys(config.tokens)){
 await coordinator.head(app);
 for(const marker of await coordinator.history(app)){
  const packet=await coordinator.event(app,marker);await updateIndex({application:app,events:packet.events,files:packet.files,marker});
  if(app==='server')for(const event of packet.events)if(event.table==='credentials'&&event.new){const value=JSON.parse(event.new);if(value.kind==='photos-oauth')photosCredentials=value;}
 }
}
const photos=new Photos({oauth:config.oauth,credentials:photosCredentials,saveCredentials:async credentials=>{
 const id=randomUUID();await coordinator.commit('server',{schemaVersion:1,id,parent:await coordinator.head('server'),events:[{schema:'server',table:'credentials',action:'INSERT',old:null,new:JSON.stringify(credentials)}]});
}});
await writeFile('/state/drive-root.tmp',JSON.stringify({folderId:rootId,owner:config.oauth.expectedEmail,format:1}),{mode:0o600});await rename('/state/drive-root.tmp','/state/drive-root.json');
const socket='/run/th-storage/storage.sock';await mkdir('/run/th-storage',{recursive:true,mode:0o700});
await unlink(socket).catch(error=>{if(error.code!=='ENOENT')throw error;});
const server=storageServer({coordinator,tokens:config.tokens,photos,status:async app=>({configured:true,connected:true,email:config.oauth.expectedEmail,folderId:rootId,provider:'drive-first',cloudFiles:Object.keys(indices.get(app)||{}).length,service:'th-server-storage'})});
server.requestTimeout=360000;
server.listen(socket,async()=>{await chmod(socket,0o600);console.log('TH storage ready',{folderId:rootId,applications:Object.keys(config.tokens)});});
for(const signal of ['SIGINT','SIGTERM'])process.on(signal,()=>server.close(()=>process.exit(0)));
