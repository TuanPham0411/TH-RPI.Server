import {createHash,createCipheriv,createDecipheriv,randomBytes,hkdfSync} from 'node:crypto';
const hash=bytes=>createHash('sha256').update(bytes).digest('hex');
const encode=value=>Buffer.from(JSON.stringify(value));
const validId=value=>typeof value==='string'&&/^[a-f0-9-]{36}$/.test(value);
const apps=new Set(['ai-agent','portfolio','ticket','server']);
const privateTable=/user|session|token|credential|grant|account/;
const privateField=/"(?:password|password_hash|secret|refreshToken|accessToken|smtp_url)"\s*:/i;

export class Coordinator {
 constructor({transport,wrappingSecret,recoveryKey,attachment,onCommitted}){
  if(!recoveryKey&&(!wrappingSecret||wrappingSecret.length<16))throw Error('Recovery wrapping secret required');
  this.transport=transport;this.attachment=attachment;this.onCommitted=onCommitted;
  this.key=recoveryKey?Buffer.from(recoveryKey,'hex'):Buffer.from(hkdfSync('sha256',wrappingSecret,'th-data-storage-v1','private-database-fields',32));
  if(this.key.length!==32)throw Error('Invalid recovery key');
  this.heads=new Map();this.queues=new Map();
 }
 checkApp(app){if(!apps.has(app))throw Error('Unknown application');}
 seal(text,context){
  const iv=randomBytes(12);const cipher=createCipheriv('aes-256-gcm',this.key,iv);cipher.setAAD(Buffer.from(context));
  return {sealed:'aes-256-gcm',iv:iv.toString('base64'),tag:null,body:Buffer.concat([cipher.update(text,'utf8'),cipher.final()]).toString('base64'),...{tag:cipher.getAuthTag().toString('base64')}};
 }
 unseal(value,context){
  if(typeof value==='string'||value===null)return value;
  if(value?.sealed!=='aes-256-gcm')throw Error('Invalid encrypted row');
  const decipher=createDecipheriv('aes-256-gcm',this.key,Buffer.from(value.iv,'base64'));decipher.setAAD(Buffer.from(context));decipher.setAuthTag(Buffer.from(value.tag,'base64'));
  return Buffer.concat([decipher.update(Buffer.from(value.body,'base64')),decipher.final()]).toString('utf8');
 }
 async history(app){
  this.checkApp(app);const prefix=`${app}/commits/`;const keys=await this.transport.list(prefix);const markers=[];
  for(const key of keys){const raw=await this.transport.get(key);if(!raw)throw Error('Commit disappeared');const marker=JSON.parse(raw);
   if(marker.schemaVersion!==1||!validId(marker.id)||marker.parent!==null&&!validId(marker.parent)||key!==`${prefix}${marker.id}.json`)throw Error('Invalid commit marker');markers.push(marker);
  }
  const ordered=[];let parent=null;
  while(true){const next=markers.filter(marker=>marker.parent===parent);if(next.length>1)throw Error('Multiple Drive writers detected');if(!next.length)break;ordered.push(next[0]);parent=next[0].id;if(ordered.length>markers.length)throw Error('Invalid history');}
  if(ordered.length!==markers.length)throw Error('Missing parent commit');
  return ordered;
 }
 async head(app){this.checkApp(app);if(!this.heads.has(app)){const history=await this.history(app);this.heads.set(app,history.at(-1)?.id??null);}return this.heads.get(app);}
 async check(app,parent){if(await this.head(app)!==parent)throw Error('Drive and PostgreSQL differ; recovery required');return {ready:true};}
 async event(app,marker){
  const bytes=await this.transport.get(`${app}/events/${marker.id}.json`);
  if(!bytes||hash(bytes)!==marker.sha256)throw Error('Missing or corrupt event');
  const packet=JSON.parse(bytes);if(packet.id!==marker.id||packet.application!==app||packet.parent!==marker.parent)throw Error('Invalid event identity');
  const events=packet.events.map((event,index)=>({...event,
   old:this.unseal(event.old,`${app}:${marker.id}:${index}:old`),
   new:this.unseal(event.new,`${app}:${marker.id}:${index}:new`)}));
  return {...packet,events};
 }
 commit(app,input){
  this.checkApp(app);
  // Queueing is also required for non-PostgreSQL server/configuration writers.
  const previous=this.queues.get(app)||Promise.resolve();
  const work=previous.then(()=>this.write(app,input));this.queues.set(app,work.catch(()=>{}));return work;
 }
 async write(app,input){
  if(input.schemaVersion!==1||!validId(input.id)||input.parent!==null&&!validId(input.parent)||!Array.isArray(input.events)||!input.events.length)throw Error('Invalid commit');
  const events=input.events.map(event=>{
   if(!['INSERT','UPDATE','DELETE','TRUNCATE','BASELINE'].includes(event.action)||!['public','portfolio','ticket_private','server'].includes(event.schema)||!/^[a-zA-Z_][a-zA-Z0-9_]{0,62}$/.test(event.table))throw Error('Invalid database event');
   for(const key of ['old','new'])if(event[key]!==null){if(typeof event[key]!=='string')throw Error('Raw JSON rows required');const row=JSON.parse(event[key]);if(!row||typeof row!=='object'||Array.isArray(row))throw Error('Invalid row');}
   return {schema:event.schema,table:event.table,action:event.action,old:event.old,new:event.new};
  });
  const requestHash=hash(encode({...input,events}));
  const receiptKey=`${app}/commits/${input.id}.json`;
  const existing=await this.transport.get(receiptKey);
  if(existing){const marker=JSON.parse(existing);if(marker.requestHash!==requestHash)throw Error('Operation id reused');await this.event(app,marker);return {id:input.id,committed:true};}
  await this.check(app,input.parent);
  const eventKey=`${app}/events/${input.id}.json`;let bytes=await this.transport.get(eventKey);
  if(bytes){const previous=JSON.parse(bytes);if(previous.requestHash!==requestHash)throw Error('Incomplete operation differs');}
  else {
   const files=[];
   if(this.attachment)for(const event of events){const reference=await this.attachment(app,event);if(reference)files.push(reference);}
   const protectedEvents=events.map((event,index)=>({...event,...Object.fromEntries(['old','new'].map(field=>[field,event[field]!==null&&(privateTable.test(event.table)||privateField.test(event[field]))?this.seal(event[field],`${app}:${input.id}:${index}:${field}`):event[field]]))}));
   bytes=encode({schemaVersion:1,application:app,id:input.id,parent:input.parent,requestHash,createdAt:new Date().toISOString(),baseline:input.baseline||null,events:protectedEvents,files});
   await this.transport.put(eventKey,bytes,'application/json');
  }
  const readback=await this.transport.get(eventKey);if(!readback||hash(readback)!==hash(bytes))throw Error('Event verification failed');
  const marker={schemaVersion:1,id:input.id,parent:input.parent,requestHash,sha256:hash(bytes)};
  const markerBytes=encode(marker);await this.transport.put(receiptKey,markerBytes,'application/json');
  const confirmation=await this.transport.get(receiptKey);if(!confirmation?.equals(markerBytes))throw Error('Commit acknowledgement unavailable; retry same id');
  this.heads.set(app,input.id);
  if(this.onCommitted)try{await this.onCommitted({application:app,events,files:JSON.parse(bytes).files,marker});}catch{console.error('Local mapping cache unavailable; Drive commit remains authoritative',{application:app});}
  return {id:input.id,committed:true};
 }
}
