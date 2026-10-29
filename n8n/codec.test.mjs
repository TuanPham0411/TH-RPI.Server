import test from 'node:test';import assert from 'node:assert/strict';
import {mkdtemp,writeFile,readFile,rm} from 'node:fs/promises';import {tmpdir} from 'node:os';import {join} from 'node:path';
import {sealFile,openFile,recoveryKey,digestFile} from './codec.mjs';
test('encrypted JSON backup preserves arbitrary binary bytes and rejects tampering or wrong context',async()=>{
 const dir=await mkdtemp(join(tmpdir(),'n8n-codec-'));try{
  const input=Buffer.alloc(200003);for(let i=0;i<input.length;i++)input[i]=i%251;
  const source=join(dir,'source'),sealed=join(dir,'sealed.json'),output=join(dir,'output');await writeFile(source,input);
  const key=recoveryKey('test-only-client-secret-not-a-credential');await sealFile(source,sealed,key,'n8n:test');
  const packet=JSON.parse(await readFile(sealed));assert.equal(packet.sealed,'aes-256-gcm');assert.equal(typeof packet.body,'string');
  await openFile(sealed,output,key,'n8n:test');assert.deepEqual(await readFile(output),input);
  await assert.rejects(()=>openFile(sealed,output,key,'wrong'));
  packet.tag=Buffer.alloc(16).toString('base64');await writeFile(sealed,JSON.stringify(packet));await assert.rejects(()=>openFile(sealed,output,key,'n8n:test'));
  assert.equal((await digestFile(source)).size,input.length);
 }finally{await rm(dir,{recursive:true,force:true});}
});
