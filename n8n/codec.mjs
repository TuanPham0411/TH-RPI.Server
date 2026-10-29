import {createCipheriv,createDecipheriv,randomBytes,createHash,hkdfSync} from 'node:crypto';
import {createReadStream,createWriteStream} from 'node:fs';
import {readFile,rm} from 'node:fs/promises';
import {pipeline} from 'node:stream/promises';
import {once} from 'node:events';
export const recoveryKey=secret=>Buffer.from(hkdfSync('sha256',secret,'th-data-storage-v1','private-database-fields',32));
export async function sealFile(source,target,key,context){
 const iv=randomBytes(12),cipher=createCipheriv('aes-256-gcm',key,iv);cipher.setAAD(Buffer.from(context));
 const encrypted=target+'.cipher';
 try{
  await pipeline(createReadStream(source),cipher,createWriteStream(encrypted,{mode:0o600}));
  const stream=createWriteStream(target,{mode:0o600});
  const put=async bytes=>{if(!stream.write(bytes))await once(stream,'drain');};
  await put(JSON.stringify({schemaVersion:1,sealed:'aes-256-gcm',context,iv:iv.toString('base64'),tag:cipher.getAuthTag().toString('base64')}).slice(0,-1)+',"body":"');
  let carry=Buffer.alloc(0);
  for await(const chunk of createReadStream(encrypted)){
   const bytes=Buffer.concat([carry,chunk]),length=bytes.length-bytes.length%3;
   await put(bytes.subarray(0,length).toString('base64'));carry=bytes.subarray(length);
  }
  if(carry.length)await put(carry.toString('base64'));
  stream.end('"}\n');await once(stream,'finish');
 }finally{await rm(encrypted,{force:true});}
}
export async function openFile(source,target,key,context){
 const value=JSON.parse(await readFile(source,'utf8'));
 if(value.schemaVersion!==1||value.sealed!=='aes-256-gcm'||value.context!==context)throw Error('Unexpected encrypted backup');
 const cipher=createDecipheriv('aes-256-gcm',key,Buffer.from(value.iv,'base64'));cipher.setAAD(Buffer.from(context));cipher.setAuthTag(Buffer.from(value.tag,'base64'));
 const plain=Buffer.concat([cipher.update(Buffer.from(value.body,'base64')),cipher.final()]);
 const {writeFile}=await import('node:fs/promises');await writeFile(target,plain,{mode:0o600});
}
export async function digestFile(path){
 const sha=createHash('sha256'),md5=createHash('md5');let size=0;
 for await(const chunk of createReadStream(path)){size+=chunk.length;sha.update(chunk);md5.update(chunk);}
 return {size,sha256:sha.digest('hex'),md5:md5.digest('hex')};
}
