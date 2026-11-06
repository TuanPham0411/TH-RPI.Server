import test from 'node:test';
import assert from 'node:assert/strict';
import {prepareMigrationDates} from '../deploy/prepare-ai-recovery.mjs';
const baseline={tables:[{schema:'public',table:'ai_workflows'}],migrations:{ai_schema_migrations:[JSON.stringify({version:1,applied_at:'2026-08-01T00:00:00Z'})]}};
function database({version=1,nonempty=false}={}){const calls=[];return {calls,query:async(sql,args)=>{calls.push({sql,args});if(sql.startsWith('SELECT version'))return {rows:[{version}]};if(sql.startsWith('SELECT 1'))return {rowCount:nonempty?1:0};return {rows:[]};}};}
test('empty AI recovery retains original migration date while preserving schema version',async()=>{
 const db=database();await prepareMigrationDates(db,baseline);
 assert.deepEqual(db.calls.find(call=>call.sql.startsWith('UPDATE')).args,[1,'2026-08-01T00:00:00Z']);
 assert.equal(db.calls.at(-1).sql,'COMMIT');
});
test('mismatched versions and nonempty recovery databases are retained unchanged',async()=>{
 for(const options of [{version:2},{nonempty:true}]){
  const db=database(options);await assert.rejects(prepareMigrationDates(db,baseline));
  assert.equal(db.calls.some(call=>call.sql.startsWith('UPDATE')),false);
 }
});
