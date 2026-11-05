import {createHash,randomUUID} from 'node:crypto';
import {createReadStream,createWriteStream} from 'node:fs';
import {lstat,mkdir,realpath,link,rm} from 'node:fs/promises';
import {resolve,dirname,sep} from 'node:path';
import {Readable,Transform} from 'node:stream';
import {pipeline} from 'node:stream/promises';

export function verifiedDownloader({root,fetchReference,onFile=()=>{}}){
 return async reference=>{
  if(typeof reference.path!=='string'||reference.path.includes('\0')||reference.path.split(/[\\/]/).some(part=>!part||part==='.'||part==='..')||reference.path.startsWith('/')||!Number.isSafeInteger(reference.size)||reference.size<0||!/^[a-f0-9]{64}$/.test(reference.sha256)||!/^[\w-]+$/.test(reference.id))throw Error('Invalid verified recovery reference');
  await mkdir(root,{recursive:true,mode:0o700});
  if((await lstat(root)).isSymbolicLink())throw Error('Recovery root cannot be a symlink');
  const base=await realpath(root),target=resolve(base,reference.path);
  if(!target.startsWith(base+sep))throw Error('Recovery path escaped destination');
  let current=base;
  for(const part of reference.path.split('/').slice(0,-1)){
   current=resolve(current,part);
   await mkdir(current,{mode:0o700}).catch(error=>{if(error.code!=='EEXIST')throw error;});
   const info=await lstat(current);
   if(info.isSymbolicLink()||!info.isDirectory())throw Error('Recovery parent is not a confined directory');
  }
  let existing;
  try{existing=await lstat(target);}catch(error){if(error.code!=='ENOENT')throw error;}
  if(existing){
   if(existing.isSymbolicLink()||!existing.isFile()||existing.size!==reference.size)throw Error('Existing recovery file differs; preserved for inspection');
   const digest=createHash('sha256');for await(const chunk of createReadStream(target))digest.update(chunk);
   const after=await lstat(target);
   if(digest.digest('hex')!==reference.sha256||after.ino!==existing.ino||after.size!==existing.size||after.mtimeMs!==existing.mtimeMs)throw Error('Existing recovery file checksum differs; preserved for inspection');
   onFile({reused:true});return target;
  }
  const temporary=target+'.'+randomUUID()+'.tmp';
  try{
   const response=await fetchReference(reference);
   if(!response.ok||!response.body)throw Error('Verified Drive media download failed');
   const digest=createHash('sha256');let size=0;
   await pipeline(Readable.fromWeb(response.body),new Transform({transform(chunk,encoding,callback){size+=chunk.length;digest.update(chunk);callback(size>reference.size?Error('Recovery media size differs'):null,chunk);}}),createWriteStream(temporary,{flags:'wx',mode:0o600}));
   if(size!==reference.size||digest.digest('hex')!==reference.sha256)throw Error('Recovery media checksum differs');
   await link(temporary,target);onFile({reused:false});return target;
  }finally{await rm(temporary,{force:true});}
 };
}

export async function cachedJournal(broker,{parallel=4,progress=()=>{}}={}){
 const {history}=await broker('/v1/history',{}),packets=new Map();
 if(!history.length)throw Error('No verified Portfolio history');
 for(let offset=0;offset<history.length;offset+=parallel){
  const selected=history.slice(offset,offset+parallel);
  const values=await Promise.all(selected.map(async marker=>{
   const packet=await broker('/v1/event',{id:marker.id});
   if(packet.id!==marker.id||packet.parent!==marker.parent||packet.application!=='portfolio')throw Error('Recovery packet identity differs');
   return packet;
  }));
  for(const packet of values)packets.set(packet.id,packet);
  if(offset%16===0||offset+selected.length===history.length)progress(Math.min(offset+selected.length,history.length),history.length);
 }
 return async(path,value)=>{
  if(path==='/v1/history')return {history};
  if(path==='/v1/event'&&packets.has(value.id))return packets.get(value.id);
  throw Error('Recovery journal is read-only');
 };
}
