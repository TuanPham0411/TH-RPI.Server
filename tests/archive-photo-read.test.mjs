import test from 'node:test';
import assert from 'node:assert/strict';
import {readArchivedFile} from '../deploy/restore-archived-photos.mjs';

const sha256='a'.repeat(64),file={id:'old',key:'portfolio/files/aa/'+sha256,sha256};
test('legacy archive IDs fall back only to the authenticated content-addressed key',async()=>{
 const paths=[];
 const transport={request:async path=>{paths.push(path);if(path.includes('/old?'))throw Error('Drive request failed (404) [notFound]');return new Response('original bytes');},metadata:async key=>{assert.equal(key,file.key);return {file:{id:'copied'}};}};
 assert.equal(await (await readArchivedFile(transport,file)).text(),'original bytes');
 assert.deepEqual(paths,['files/old?alt=media','files/copied?alt=media']);
});
test('an absent legacy blob remains unavailable without choosing an unrelated file',async()=>{
 const transport={request:async()=>{throw Error('Drive request failed (404) [notFound]');},metadata:async()=>({file:null})};
 await assert.rejects(readArchivedFile(transport,file),/404/);
 let lookups=0;transport.metadata=async()=>{lookups++;return {file:{id:'unrelated'}};};
 await assert.rejects(readArchivedFile(transport,{...file,key:'server/files/'+sha256}),/404/);assert.equal(lookups,0);
});
test('permission and uncertain network errors do not switch archive identities',async()=>{
 let lookups=0;
 const transport={request:async()=>{throw Error('Drive request failed (403) [insufficientPermissions]');},metadata:async()=>{lookups++;}};
 await assert.rejects(readArchivedFile(transport,file),/403/);assert.equal(lookups,0);
});
