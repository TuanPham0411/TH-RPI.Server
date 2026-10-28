import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,writeFile,symlink,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import http from 'node:http';
import {confinedFile,attachmentWriter} from '../storage/attachments.mjs';
import {storageServer} from '../storage/http.mjs';

test('media streaming adapter rejects symlinks and mismatched sizes',async()=>{
 const root=await mkdtemp(join(tmpdir(),'th-media-'));try{
  await writeFile(join(root,'image'),'abc');await symlink(join(root,'image'),join(root,'link'));
  await assert.rejects(confinedFile(root,'../image'));await assert.rejects(confinedFile(root,'link'));
  let uploads=0;const writer=attachmentWriter({async putLocalFile(key,path,meta){uploads++;return {id:'drive-id',key,...meta};}},{portfolio:root});
  const event={schema:'public',table:'files',new:'{"path":"image","size":3,"type":"image/jpeg"}'};
  const file=await writer('portfolio',event);assert.equal(file.size,3);assert.equal(file.sha256,'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad');assert.equal(uploads,1);
  await assert.rejects(writer('portfolio',{...event,new:'{"path":"image","size":4,"type":"image/jpeg"}'}),/size/);
 }finally{await rm(root,{recursive:true,force:true});}
});
test('Unix broker rejects missing credentials and cross-application tokens',async()=>{
 const root=await mkdtemp(join(tmpdir(),'th-socket-'));const socket=join(root,'storage.sock');
 const server=storageServer({coordinator:{check:async()=>({ready:true})},tokens:{portfolio:'portfolio-only','mes-ticket':'mes-only'}});
 await new Promise(resolve=>server.listen(socket,resolve));
 const request=(application,token)=>new Promise((resolve,reject)=>{const body='{"parent":null}';const req=http.request({socketPath:socket,path:'/v1/check',method:'POST',headers:{'X-TH-Application':application,Authorization:`Bearer ${token}`,'Content-Length':Buffer.byteLength(body)}},res=>{res.resume();res.on('end',()=>resolve(res.statusCode));});req.on('error',reject);req.end(body);});
 try{assert.equal(await request('portfolio','mes-only'),403);assert.equal(await request('portfolio','portfolio-only'),200);assert.equal(await request('server','portfolio-only'),403);}finally{await new Promise(resolve=>server.close(resolve));await rm(root,{recursive:true,force:true});}
});
