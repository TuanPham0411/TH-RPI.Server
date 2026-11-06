import test from 'node:test';
import assert from 'node:assert/strict';
import {ensureRoot} from '../storage/oauth.mjs';
test('isolated recovery selects its own root without changing the shared default',async()=>{
 for(const name of ['th-data','th-data-sika1-ai']){
  let query;
  const id=await ensureRoot(async()=>'token',async url=>{query=new URL(url).searchParams.get('q');return Response.json({files:[{id:'root-id'}]});},name);
  assert.equal(id,'root-id');assert.ok(query.includes(`name = '${name}'`));
 }
 await assert.rejects(ensureRoot(async()=>'token',()=>{throw Error('Must not request');},"bad' or name='th-data"),/Invalid Drive root/);
});
