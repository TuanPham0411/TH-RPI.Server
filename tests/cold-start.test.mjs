import test from 'node:test';
import assert from 'node:assert/strict';
import {randomUUID} from 'node:crypto';
import {GoogleDriveTransport} from '../storage/google-drive.mjs';
import {Coordinator} from '../storage/coordinator.mjs';
import {replayHistory} from '../storage/replay.mjs';

test('listed Drive descriptors avoid repeated lookups but content is still fetched',async()=>{
 const drive=new GoogleDriveTransport({rootId:'root',accessToken:async()=> 'fixture'});
 drive.resolve=async()=> 'folder';drive.children=async()=>[{id:'one',name:'a.json',size:'23',mimeType:'application/json'}];
 let lookups=0,reads=0;drive.metadata=async()=>{lookups++;return {file:null};};
 drive.request=async path=>{reads++;assert.equal(path,'files/one?alt=media');return new Response('verified bytes');};
 await drive.list('portfolio/events/');assert.equal(drive.estimatedSize('portfolio/events/a.json'),23);
 assert.equal((await drive.get('portfolio/events/a.json')).toString(),'verified bytes');
 assert.equal((await drive.get('portfolio/events/a.json')).toString(),'verified bytes');
 assert.equal(lookups,0);assert.equal(reads,2);
 drive.children=async()=>[];await drive.list('portfolio/events/');
 assert.equal(await drive.get('portfolio/events/a.json'),null);assert.equal(lookups,1);
});

test('duplicate Drive log names fail closed',async()=>{
 const drive=new GoogleDriveTransport({rootId:'root',accessToken:async()=> 'fixture'});
 drive.resolve=async()=> 'folder';drive.children=async()=>[{id:'one',name:'a.json'},{id:'two',name:'a.json'}];
 await assert.rejects(drive.list('portfolio/commits/'),/Duplicate Drive object/);
});

test('immutable upload comparison uses fresh metadata instead of an old listed descriptor',async()=>{
 const drive=new GoogleDriveTransport({rootId:'root',accessToken:async()=> 'fixture'});
 drive.readIndex.set('portfolio/events/a.json',{id:'old'});
 drive.metadata=async()=>({file:{id:'current'}});
 drive.request=async path=>{assert.equal(path,'files/current?alt=media');return new Response('changed');};
 await assert.rejects(drive.put('portfolio/events/a.json',Buffer.from('original'),'application/json'),/Immutable Drive object conflict/);
});

test('history reads are bounded to four concurrent requests and still ordered by parents',async()=>{
 const ids=Array.from({length:17},()=>randomUUID()),data=new Map(ids.map((id,index)=>[`portfolio/commits/${id}.json`,JSON.stringify({schemaVersion:1,id,parent:ids[index-1]||null})]));
 let active=0,maximum=0;
 const coordinator=new Coordinator({wrappingSecret:'fixture-private-key-for-cold-start-tests',transport:{list:async()=>[...data.keys()].reverse(),get:async key=>{active++;maximum=Math.max(maximum,active);await new Promise(r=>setTimeout(r,2));active--;return data.get(key);}}});
 assert.deepEqual((await coordinator.history('portfolio')).map(row=>row.id),ids);assert.equal(maximum,4);
});

test('startup replay bounds memory/concurrency, applies in order, and checkpoints once',async()=>{
 const history=Array.from({length:10},(_,id)=>({id:String(id)})),applied=[],checkpoints=[];let active=0,maximum=0;
 await replayHistory({application:'portfolio',history,transport:{list:async()=>[],estimatedSize:()=>3*1024*1024},coordinator:{event:async(app,marker)=>{active++;maximum=Math.max(maximum,active);await new Promise(r=>setTimeout(r,1));active--;return marker;}},apply:async packet=>applied.push(packet.id),checkpoint:async marker=>checkpoints.push(marker.id)});
 assert.deepEqual(applied,history.map(row=>row.id));assert.deepEqual(checkpoints,['9']);assert.equal(maximum,2);
});

test('corrupt startup event prevents checkpoint and ready state',async()=>{
 let checkpointed=false;
 await assert.rejects(replayHistory({application:'portfolio',history:[{id:'broken'}],transport:{list:async()=>[]},coordinator:{event:async()=>{throw Error('Missing or corrupt event');}},apply:async()=>{},checkpoint:async()=>{checkpointed=true;}}),/corrupt/);
 assert.equal(checkpointed,false);
});
