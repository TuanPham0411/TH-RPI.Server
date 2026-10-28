import {randomBytes,createHash} from 'node:crypto';
const digest=value=>createHash('sha256').update(value).digest('hex');
const redirect='https://ai.th.io.vn/api/oauth/callback';
export class OwnerAuth {
 constructor({oauth,save,records=[],fetchImpl=fetch,now=()=>Date.now()}){this.oauth=oauth;this.save=save;this.fetch=fetchImpl;this.now=now;this.pending=new Map();this.sessions=new Map();for(const row of records)this.apply(row);}
 apply(row){if(row.kind==='owner-session'&&row.application==='ai-agent')this.sessions.set(row.hash,row);if(row.kind==='owner-revocation')this.sessions.delete(row.hash);}
 start(binding){
  if(typeof binding!=='string'||!/^[a-f0-9]{64}$/.test(binding))throw Error('Browser binding required');
  for(const [key,value]of this.pending)if(value.expires<this.now())this.pending.delete(key);
  if(this.pending.size>=100)throw Error('Too many sign-in attempts');
  const state=randomBytes(32).toString('hex'),verifier=randomBytes(32).toString('base64url');
  this.pending.set(state,{binding,verifier,expires:this.now()+600000});
  return {url:'https://accounts.google.com/o/oauth2/v2/auth?'+new URLSearchParams({client_id:this.oauth.clientId,redirect_uri:redirect,response_type:'code',scope:'openid email',state,code_challenge:digestBuffer(verifier),code_challenge_method:'S256'})};
 }
 async callback({state,code,binding}){
  const pending=this.pending.get(state);this.pending.delete(state);
  if(!pending||pending.binding!==binding||pending.expires<this.now()||typeof code!=='string'||code.length>4096)throw Error('Invalid sign-in state');
  const response=await this.fetch('https://oauth2.googleapis.com/token',{method:'POST',body:new URLSearchParams({client_id:this.oauth.clientId,client_secret:this.oauth.clientSecret,redirect_uri:redirect,grant_type:'authorization_code',code,code_verifier:pending.verifier}),signal:AbortSignal.timeout(30000)});
  if(!response.ok)throw Error('Google sign-in failed');const token=await response.json();
  if(!token.access_token)throw Error('Google access token missing');
  const identityResponse=await this.fetch('https://openidconnect.googleapis.com/v1/userinfo',{headers:{Authorization:'Bearer '+token.access_token},signal:AbortSignal.timeout(20000)});
  if(!identityResponse.ok)throw Error('Google identity unavailable');const identity=await identityResponse.json();
  if(identity.email_verified!==true||identity.email?.toLowerCase()!==this.oauth.expectedEmail.toLowerCase())throw Error('Owner account required');
  const session=randomBytes(32).toString('base64url'),record={kind:'owner-session',application:'ai-agent',hash:digest(session),email:identity.email,expires:this.now()+7*86400000};
  await this.save(record);this.apply(record);return {session,email:record.email,expires:record.expires};
 }
 session(token){if(typeof token!=='string'||token.length>128)return {authenticated:false};const row=this.sessions.get(digest(token));return row&&row.expires>this.now()?{authenticated:true,email:row.email,expires:row.expires}:{authenticated:false};}
 async logout(token){if(!this.session(token).authenticated)return {authenticated:false};const row={kind:'owner-revocation',application:'ai-agent',hash:digest(token)};await this.save(row);this.apply(row);return {authenticated:false};}
}
function digestBuffer(value){return createHash('sha256').update(value).digest('base64url');}
