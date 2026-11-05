import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,writeFile,readFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {tokenSupplier} from '../storage/oauth.mjs';
import {GoogleDriveTransport} from '../storage/google-drive.mjs';
import {Photos} from '../storage/photos.mjs';

const oauth={clientId:'fixture-client',clientSecret:'fixture-secret',expectedEmail:'owner@example.test'};
const json=(value,status=200)=>new Response(JSON.stringify(value),{status});
async function fixture(t){
 const root=await mkdtemp(join(tmpdir(),'drive-token-'));t.after(()=>rm(root,{recursive:true,force:true}));
 const path=join(root,'grant.json');await writeFile(path,JSON.stringify({refreshToken:'drive-refresh',email:oauth.expectedEmail}),{mode:0o600});
 let refreshes=0;
 const supply=tokenSupplier({...oauth,credentialsPath:path,fetchImpl:async url=>url.includes('/token')?json({access_token:'drive-'+(++refreshes),expires_in:3600}):json({email:oauth.expectedEmail,email_verified:true})});
 return {supply,path,count:()=>refreshes};
}

test('Photos consent followed by a rejected cached Drive token recovers before saving Photos credentials',async t=>{
 const f=await fixture(t);assert.equal(await f.supply(),'drive-1');let rejected=false,saved;
 const transport=new GoogleDriveTransport({rootId:'root',accessToken:f.supply,fetchImpl:async(url,init)=>rejected&&init.headers.Authorization==='Bearer drive-1'?json({error:{errors:[{reason:'insufficientPermissions'}]}},403):json({id:'verified'})});
 const photos=new Photos({oauth,fetchImpl:async url=>{
  if(url.includes('/token')){rejected=true;return json({access_token:'photos-access',refresh_token:'photos-refresh'});}
  return json({email:oauth.expectedEmail,email_verified:true});
 },saveCredentials:async credentials=>{await transport.request('files/verified');saved=credentials;}});
 const binding='a'.repeat(64),start=photos.start(binding);
 assert.equal(new URL(start.url).searchParams.get('include_granted_scopes'),'true');
 assert.deepEqual(await photos.callback({binding,state:start.state,code:'fixture-code'}),{connected:true});
 assert.equal(f.count(),2);assert.equal(saved.refreshToken,'photos-refresh');
 assert.equal(JSON.parse(await readFile(f.path,'utf8')).refreshToken,'drive-refresh');
 assert.equal(await f.supply(),'drive-2');
});

test('concurrent rejected requests share one refresh and stale failures cannot evict the new token',async t=>{
 const f=await fixture(t);await f.supply();
 const transport=new GoogleDriveTransport({rootId:'root',accessToken:f.supply,fetchImpl:async(url,init)=>init.headers.Authorization==='Bearer drive-1'?json({error:{errors:[{reason:'insufficientPermissions'}]}},403):json({id:'verified'})});
 await Promise.all(Array.from({length:12},()=>transport.request('files/verified')));
 assert.equal(f.count(),2);f.supply.invalidate('drive-1');assert.equal(await f.supply(),'drive-2');assert.equal(f.count(),2);
});

test('an expired or revoked access token is refreshed once on HTTP 401',async t=>{
 const f=await fixture(t);let calls=0;
 const transport=new GoogleDriveTransport({rootId:'root',accessToken:f.supply,fetchImpl:async()=>++calls===1?json({error:{}},401):json({id:'verified'})});
 await transport.request('files/verified');assert.equal(calls,2);assert.equal(f.count(),2);
});

test('persistent permission denial stays unacknowledged after one fresh-token retry',async t=>{
 const f=await fixture(t);let calls=0;
 const transport=new GoogleDriveTransport({rootId:'root',accessToken:f.supply,fetchImpl:async()=>{calls++;return json({error:{errors:[{reason:'insufficientPermissions'}]}},403);}});
 await assert.rejects(transport.request('files/verified'),/403.*insufficientPermissions.*not acknowledged/);
 assert.equal(calls,2);assert.equal(f.count(),2);
});

test('quota failures are classified without token refresh or provider-message disclosure',async t=>{
 const f=await fixture(t);let calls=0;
 const transport=new GoogleDriveTransport({rootId:'root',accessToken:f.supply,fetchImpl:async()=>{calls++;return json({error:{message:'private-provider-detail',errors:[{reason:'userRateLimitExceeded'}]}},403);}});
 await assert.rejects(transport.request('files/verified'),error=>/userRateLimitExceeded/.test(error.message)&&!error.message.includes('private-provider-detail'));
 assert.equal(calls,1);assert.equal(f.count(),1);
});

test('transient server errors and uncertain network writes are not blindly replayed',async t=>{
 const f=await fixture(t);let calls=0;
 const transport=new GoogleDriveTransport({rootId:'root',accessToken:f.supply,fetchImpl:async()=>{calls++;return json({error:{}},503);}});
 await assert.rejects(transport.request('files',{method:'POST',body:'{}'},true),/503/);assert.equal(calls,1);
 transport.fetch=async()=>{calls++;throw Error('connection lost');};
 await assert.rejects(transport.request('files',{method:'POST',body:'{}'},true),/connection lost/);assert.equal(calls,2);
});
