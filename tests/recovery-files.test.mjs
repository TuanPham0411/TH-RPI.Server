import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {mkdtemp,readFile,writeFile,rm,symlink,readdir} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {verifiedDownloader,cachedJournal} from '../deploy/recovery-files.mjs';
const bytes=Buffer.from('original recovery media'),ref={path:'portfolio/photo',id:'verified-drive-file',size:bytes.length,sha256:createHash('sha256').update(bytes).digest('hex')};

test('verified download is exclusive and an interrupted recovery reuses only matching files',async t=>{
 const root=await mkdtemp(join(tmpdir(),'recovery-'));t.after(()=>rm(root,{recursive:true,force:true}));let reads=0;const outcomes=[];
 const download=verifiedDownloader({root,fetchReference:async()=>{reads++;return new Response(bytes);},onFile:value=>outcomes.push(value)});
 await download(ref);await download(ref);
 assert.equal(reads,1);assert.deepEqual(outcomes,[{reused:false},{reused:true}]);assert.deepEqual(await readFile(join(root,ref.path)),bytes);
});

test('checksum mismatch never overwrites an existing recovery file',async t=>{
 const root=await mkdtemp(join(tmpdir(),'recovery-'));t.after(()=>rm(root,{recursive:true,force:true}));
 const download=verifiedDownloader({root,fetchReference:async()=>new Response(bytes)});await download(ref);
 await writeFile(join(root,ref.path),Buffer.alloc(bytes.length,120));
 await assert.rejects(download(ref),/checksum differs/);assert.deepEqual(await readFile(join(root,ref.path)),Buffer.alloc(bytes.length,120));
});

test('bad incoming bytes leave no published or temporary file',async t=>{
 const root=await mkdtemp(join(tmpdir(),'recovery-'));t.after(()=>rm(root,{recursive:true,force:true}));
 const download=verifiedDownloader({root,fetchReference:async()=>new Response(Buffer.alloc(bytes.length,120))});
 await assert.rejects(download(ref),/checksum differs/);assert.deepEqual(await readdir(join(root,'portfolio')),[]);
});

test('symlink parents and traversal are rejected without writing outside the root',async t=>{
 const root=await mkdtemp(join(tmpdir(),'recovery-')),outside=await mkdtemp(join(tmpdir(),'outside-'));t.after(()=>Promise.all([rm(root,{recursive:true,force:true}),rm(outside,{recursive:true,force:true})]));
 await symlink(outside,join(root,'portfolio'));const download=verifiedDownloader({root,fetchReference:async()=>new Response(bytes)});
 await assert.rejects(download(ref),/confined/);await assert.rejects(download({...ref,path:'../outside'}),/Invalid/);assert.deepEqual(await readdir(outside),[]);
});

test('prefetched journal remains ordered, bounded, and rejects writes or wrong identities',async()=>{
 const history=Array.from({length:9},(_,index)=>({id:String(index),parent:index?String(index-1):null}));let active=0,maximum=0,reads=0;
 const broker=async(path,value)=>{if(path==='/v1/history')return {history};reads++;active++;maximum=Math.max(maximum,active);await new Promise(r=>setTimeout(r,1));active--;const marker=history.find(row=>row.id===value.id);return {...marker,application:'portfolio',events:[],files:[]};};
 const cached=await cachedJournal(broker);assert.equal(maximum,4);assert.equal(reads,9);assert.deepEqual((await cached('/v1/history',{})).history,history);
 for(const marker of history)assert.equal((await cached('/v1/event',{id:marker.id})).id,marker.id);
 assert.equal(reads,9);await assert.rejects(cached('/v1/commit',{}),/read-only/);
 await assert.rejects(cachedJournal(async path=>path==='/v1/history'?{history:[{id:'one',parent:null}]}:{id:'other',parent:null,application:'portfolio'}),/identity differs/);
});
test('AI recovery journal accepts only the selected application identity',async()=>{
 const marker={id:'one',parent:null};
 const broker=async path=>path==='/v1/history'?{history:[marker]}:{...marker,application:'ai-agent',events:[],files:[]};
 const journal=await cachedJournal(broker,{application:'ai-agent'});
 assert.equal((await journal('/v1/event',{id:'one'})).application,'ai-agent');
 await assert.rejects(cachedJournal(broker),/identity differs/);
});
