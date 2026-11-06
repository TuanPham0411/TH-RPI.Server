import test from 'node:test';
import assert from 'node:assert/strict';
import {OwnerAuth} from '../storage/owner-auth.mjs';
const oauth={clientId:'client',clientSecret:'secret',expectedEmail:'owner@example.com'},binding='a'.repeat(64);
function setup({email='owner@example.com',verified=true,save=async()=>{},now=()=>1000}={}){let count=0;return new OwnerAuth({oauth,save,now,fetchImpl:async()=>new Response(JSON.stringify(++count%2?{access_token:'access'}:{email,email_verified:verified}),{status:200})});}
function parameters(auth){const url=new URL(auth.start(binding).url);assert.equal(url.searchParams.get('redirect_uri'),'https://ai.th.io.vn/api/oauth/callback');assert.equal(url.searchParams.get('scope'),'openid email');return {binding,state:url.searchParams.get('state'),code:'code'};}
test('owner OAuth rejects wrong browser binding and replay',async()=>{const auth=setup(),args=parameters(auth);await assert.rejects(auth.callback({...args,binding:'b'.repeat(64)}),/state/);await assert.rejects(auth.callback(args),/state/);});
test('owner identity must be verified and match configured owner',async()=>{for(const options of [{email:'other@example.com'},{verified:false}]){const auth=setup(options);await assert.rejects(auth.callback(parameters(auth)),/Owner/);}});
test('owner session is unavailable when Drive persistence fails',async()=>{const auth=setup({save:async()=>{throw Error('Drive unavailable');}});await assert.rejects(auth.callback(parameters(auth)),/Drive/);assert.equal(auth.sessions.size,0);});
test('hashed sessions recover, expire and revoke durably',async()=>{const records=[];let now=1000;const auth=setup({save:async r=>records.push(r),now:()=>now});const result=await auth.callback(parameters(auth));assert.equal(auth.session(result.session).authenticated,true);assert.equal(JSON.stringify(records).includes(result.session),false);const restored=new OwnerAuth({oauth,records,save:async r=>records.push(r),now:()=>now});assert.equal(restored.session(result.session).authenticated,true);await restored.logout(result.session);assert.equal(restored.session(result.session).authenticated,false);assert.equal(new OwnerAuth({oauth,records,save:async()=>{},now:()=>now}).session(result.session).authenticated,false);now=result.expires+1;assert.equal(auth.session(result.session).authenticated,false);});
test('SIKA1 callback uses the configured origin in consent and token exchange',async()=>{
 let callback;let count=0;
 const auth=new OwnerAuth({oauth,origin:'https://thienha.io.vn',save:async()=>{},fetchImpl:async(url,options)=>{
  if(++count===1){callback=options.body.get('redirect_uri');return Response.json({access_token:'access'});}
  return Response.json({email:oauth.expectedEmail,email_verified:true});
 }});
 const url=new URL(auth.start(binding).url);
 assert.equal(url.searchParams.get('redirect_uri'),'https://thienha.io.vn/api/oauth/callback');
 await auth.callback({state:url.searchParams.get('state'),code:'code',binding});
 assert.equal(callback,'https://thienha.io.vn/api/oauth/callback');
 for(const origin of ['http://thienha.io.vn','https://user@thienha.io.vn','https://thienha.io.vn/path'])assert.throws(()=>new OwnerAuth({oauth,origin,save:async()=>{}}));
});
