import test from 'node:test';
import assert from 'node:assert/strict';
import {Photos,photoDownloadURL} from '../storage/photos.mjs';
const binding='a'.repeat(64);
test('Photos authorization is scoped to Picker and bound to the owner session',async()=>{
 const photos=new Photos({oauth:{clientId:'client',clientSecret:'secret',expectedEmail:'owner@example.com'},saveCredentials:async()=>{}});
 const value=photos.start(binding),url=new URL(value.url);assert.equal(url.searchParams.get('redirect_uri'),'https://th.io.vn/api/portfolio/content/photos/callback');assert.ok(url.searchParams.get('scope').includes('photospicker.mediaitems.readonly'));assert.equal(url.searchParams.get('code_challenge_method'),'S256');await assert.rejects(photos.callback({binding:'b'.repeat(64),state:value.state,code:'code'}),/state/);
});
test('Google Photos refuses arbitrary download hosts',()=>{
 assert.equal(photoDownloadURL('https://lh3.googleusercontent.com/selected'),'https://lh3.googleusercontent.com/selected=d');
 for(const value of ['http://lh3.googleusercontent.com/a','https://googleusercontent.com.attacker.test/a','https://127.0.0.1/a','https://user:pass@lh3.googleusercontent.com/a'])assert.throws(()=>photoDownloadURL(value));
});
test('Photos tokens remain unavailable until encrypted credential save succeeds',async()=>{
 let call=0;
 const photos=new Photos({oauth:{clientId:'client',clientSecret:'secret',expectedEmail:'owner@example.com'},saveCredentials:async()=>{throw Error('Drive unavailable');},fetchImpl:async()=>new Response(JSON.stringify(++call===1?{refresh_token:'refresh',access_token:'access'}:{email_verified:true,email:'owner@example.com'}),{status:200})});
 const start=photos.start(binding);await assert.rejects(photos.callback({binding,state:start.state,code:'code'}),/Drive/);assert.equal(photos.credentials,null);
});
