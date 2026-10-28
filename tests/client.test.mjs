import test from 'node:test';
import assert from 'node:assert/strict';
import {driveFirstPool} from '../storage/client.mjs';

function fixture({failDrive=false,failLocal=false}={}){
 const log=[];let events=[];let committed=false;
 const db={async query(sql,args){log.push(sql);if(sql==='SELECT public.th_storage_head() AS head')return {rows:[{head:null}]};if(sql==='SELECT public.th_storage_pending() AS pending')return {rows:[{pending:{parent:null,events}}]};if(sql==='COMMIT'){if(failLocal)throw Error('Local failure');committed=true;}if(sql.startsWith('INSERT'))events=[{schema:'public',table:'records',action:'INSERT',old:null,new:'{"value":1}'}];return {rows:[],rowCount:1};},release(){log.push('release');}};
 const pool=driveFirstPool({connect:async()=>db},{checkHead:async()=>log.push('check-head'),commit:async value=>{log.push('DRIVE');if(failDrive)throw Error('Drive unavailable');return {id:value.id,committed:true};}});
 return {pool,log,isCommitted:()=>committed};
}
test('Drive acknowledgement precedes PostgreSQL COMMIT',async()=>{const f=fixture();await f.pool.query('INSERT INTO records VALUES(1)');assert.ok(f.log.indexOf('DRIVE')<f.log.indexOf('COMMIT'));assert.ok(f.isCommitted());});
test('Drive failure rolls back and never acknowledges local success',async()=>{const f=fixture({failDrive:true});await assert.rejects(f.pool.query('INSERT INTO records VALUES(1)'),/Drive/);assert.ok(!f.isCommitted());assert.ok(f.log.includes('ROLLBACK'));});
test('existing explicit transactions send one batch',async()=>{const f=fixture();const db=await f.pool.connect();await db.query('begin');await db.query('INSERT INTO records VALUES(1)');assert.ok(!f.log.includes('DRIVE'));await db.query('commit');db.release();assert.equal(f.log.filter(x=>x==='DRIVE').length,1);});
test('read-only queries do not create Drive commits',async()=>{const f=fixture();await f.pool.query('SELECT 1');assert.ok(!f.log.includes('DRIVE'));});
test('local failure after Drive acknowledgement surfaces an error',async()=>{const f=fixture({failLocal:true});await assert.rejects(f.pool.query('INSERT INTO records VALUES(1)'),/Local failure/);assert.ok(f.log.includes('DRIVE'));assert.ok(!f.isCommitted());});
test('embedded transaction control cannot bypass the gate',async()=>{const f=fixture();await assert.rejects(f.pool.query('INSERT INTO records VALUES(1); COMMIT'),/explicit/);assert.ok(!f.log.includes('DRIVE'));});
