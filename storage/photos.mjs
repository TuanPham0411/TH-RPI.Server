import {randomBytes,createHash} from 'node:crypto';

const scope='openid email https://www.googleapis.com/auth/photospicker.mediaitems.readonly';
const redirect='https://th.io.vn/api/portfolio/content/photos/callback';
export function photoDownloadURL(value){
 const url=new URL(value);
 if(url.protocol!=='https:'||url.username||url.password||url.port||!url.hostname.endsWith('.googleusercontent.com'))throw Error('Invalid Google Photos media host');
 return value+'=d';
}
export class Photos {
 constructor({oauth,saveCredentials,credentials=null,fetchImpl=fetch}){this.oauth=oauth;this.saveCredentials=saveCredentials;this.credentials=credentials;this.fetch=fetchImpl;this.pending=new Map();this.sessions=new Map();this.access=null;}
 async json(response){if(!response.ok)throw Error(`Google Photos request failed (${response.status}); enable Picker API and authorize the owner`);return response.json();}
 start(binding){
  if(typeof binding!=='string'||!/^[a-f0-9]{64}$/.test(binding))throw Error('Owner session binding required');
  for(const [id,value] of this.pending)if(value.expires<Date.now())this.pending.delete(id);
  const state=randomBytes(32).toString('hex'),verifier=randomBytes(32).toString('hex');
  this.pending.set(state,{binding,verifier,expires:Date.now()+600000});
  return {state,url:'https://accounts.google.com/o/oauth2/v2/auth?'+new URLSearchParams({client_id:this.oauth.clientId,redirect_uri:redirect,response_type:'code',scope,access_type:'offline',prompt:'consent',state,code_challenge:createHash('sha256').update(verifier).digest('base64url'),code_challenge_method:'S256'})};
 }
 async callback({binding,state,code}){
  const pending=this.pending.get(state);this.pending.delete(state);
  if(!pending||pending.expires<Date.now()||pending.binding!==binding||typeof code!=='string'||code.length>4096)throw Error('Invalid Photos OAuth state');
  const value=await this.json(await this.fetch('https://oauth2.googleapis.com/token',{method:'POST',body:new URLSearchParams({client_id:this.oauth.clientId,client_secret:this.oauth.clientSecret,code,code_verifier:pending.verifier,grant_type:'authorization_code',redirect_uri:redirect}),signal:AbortSignal.timeout(30000)}));
  if(!value.refresh_token||!value.access_token)throw Error('Photos offline authorization missing');
  const identity=await this.json(await this.fetch('https://openidconnect.googleapis.com/v1/userinfo',{headers:{Authorization:`Bearer ${value.access_token}`},signal:AbortSignal.timeout(20000)}));
  if(identity.email_verified!==true||identity.email?.toLowerCase()!==this.oauth.expectedEmail.toLowerCase())throw Error('Use the configured owner Google account');
  const credentials={kind:'photos-oauth',refreshToken:value.refresh_token,email:identity.email};
  await this.saveCredentials(credentials);this.credentials=credentials;this.access={token:value.access_token,expires:Date.now()+Number(value.expires_in||3600)*1000};
  return {connected:true};
 }
 async token(){
  if(this.access&&this.access.expires>Date.now()+60000)return this.access.token;
  if(!this.credentials?.refreshToken)throw Error('Connect Google Photos first');
  const result=await this.json(await this.fetch('https://oauth2.googleapis.com/token',{method:'POST',body:new URLSearchParams({client_id:this.oauth.clientId,client_secret:this.oauth.clientSecret,refresh_token:this.credentials.refreshToken,grant_type:'refresh_token'}),signal:AbortSignal.timeout(30000)}));
  if(!result.access_token)throw Error('Photos refresh failed');this.access={token:result.access_token,expires:Date.now()+Number(result.expires_in||3600)*1000};return this.access.token;
 }
 async api(path,options={}){return this.json(await this.fetch('https://photospicker.googleapis.com/v1/'+path,{...options,headers:{Authorization:'Bearer '+await this.token(),'Content-Type':'application/json'},signal:AbortSignal.timeout(30000)}));}
 owned(id,binding){const session=this.sessions.get(id);if(!session||session.binding!==binding||session.expires<Date.now())throw Error('Photos selection session expired');return session;}
 async create(binding){
  for(const [id,value] of this.sessions)if(value.expires<Date.now())this.sessions.delete(id);
  if(this.sessions.size>=20)throw Error('Too many active Photos selection sessions');
  const value=await this.api('sessions',{method:'POST'});const uri=new URL(value.pickerUri);
  if(uri.protocol!=='https:'||uri.hostname!=='photos.google.com'||!value.id)throw Error('Invalid Photos picker response');
  this.sessions.set(value.id,{binding,expires:Date.now()+3600000,items:new Map()});return {id:value.id,pickerUri:uri.href};
 }
 async poll({id,binding}){this.owned(id,binding);const value=await this.api('sessions/'+encodeURIComponent(id));return {ready:value.mediaItemsSet===true,pollAfterMs:Math.max(2000,Math.min(10000,Number.parseFloat(value.pollingConfig?.pollInterval||'2s')*1000||2000))};}
 async items({id,binding}){
  const session=this.owned(id,binding);const rows=[];let page;
  do{
   const query=new URLSearchParams({sessionId:id,pageSize:'100',...(page?{pageToken:page}:{})});
   const value=await this.api('mediaItems?'+query);
   for(const item of value.mediaItems||[]){const media=item.mediaFile;if(item.type!=='PHOTO'||!media?.mimeType?.startsWith('image/'))continue;photoDownloadURL(media.baseUrl);session.items.set(item.id,{id:item.id,name:String(media.filename||'Google Photos image').slice(0,180),type:media.mimeType,baseUrl:media.baseUrl});rows.push({id:item.id,name:media.filename,type:media.mimeType});}
   page=value.nextPageToken;if(rows.length>1000)throw Error('Select at most 1000 images at a time');
  }while(page);
  return {items:rows};
 }
 async download({id,itemId,binding}){
  const session=this.owned(id,binding);const item=session.items.get(itemId);if(!item)throw Error('Choose this image in the Photos picker first');
  let url=photoDownloadURL(item.baseUrl),response;
  for(let n=0;n<4;n++){
   response=await this.fetch(url,{headers:{Authorization:'Bearer '+await this.token()},redirect:'manual',signal:AbortSignal.timeout(30000)});
   if(response.status>=300&&response.status<400){const target=new URL(response.headers.get('location'),url);photoDownloadURL(target.href);url=target.href;continue;}break;
  }
  if(!response.ok)throw Error('Selected Google Photos image unavailable');
  return {response,item};
 }
}
