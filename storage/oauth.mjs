import {readFile} from 'node:fs/promises';

export function tokenSupplier({credentialsPath,clientId,clientSecret,expectedEmail,fetchImpl=fetch}){
 let cached;
 return async()=>{
  if(cached&&cached.expires>Date.now()+60000)return cached.token;
  const credentials=JSON.parse(await readFile(credentialsPath,'utf8'));
  if(!credentials.refreshToken||credentials.email?.toLowerCase()!==expectedEmail.toLowerCase())throw Error('Drive owner or offline grant differs');
  const response=await fetchImpl('https://oauth2.googleapis.com/token',{method:'POST',body:new URLSearchParams({client_id:clientId,client_secret:clientSecret,refresh_token:credentials.refreshToken,grant_type:'refresh_token'}),signal:AbortSignal.timeout(30000)});
  if(!response.ok)throw Error(`Drive OAuth refresh failed (${response.status})`);
  const value=await response.json();if(!value.access_token)throw Error('Drive access token missing');
  const identity=await fetchImpl('https://openidconnect.googleapis.com/v1/userinfo',{headers:{Authorization:`Bearer ${value.access_token}`},signal:AbortSignal.timeout(20000)});
  const user=await identity.json();if(!identity.ok||user.email_verified!==true||user.email?.toLowerCase()!==expectedEmail.toLowerCase())throw Error('Authenticated Drive owner differs');
  cached={token:value.access_token,expires:Date.now()+Number(value.expires_in||3600)*1000};return cached.token;
 };
}

export async function ensureRoot(accessToken,fetchImpl=fetch){
 const token=await accessToken();
 const q=new URLSearchParams({q:"'root' in parents and trashed = false and name = 'th-data' and mimeType = 'application/vnd.google-apps.folder'",fields:'files(id,name),nextPageToken',pageSize:'1000'});
 const response=await fetchImpl(`https://www.googleapis.com/drive/v3/files?${q}`,{headers:{Authorization:`Bearer ${token}`},signal:AbortSignal.timeout(30000)});
 if(!response.ok)throw Error('Cannot locate th-data');const list=await response.json();
 if(list.nextPageToken||list.files.length>1)throw Error('Ambiguous th-data folders; choose one before proceeding');
 if(list.files.length)return list.files[0].id;
 const created=await fetchImpl('https://www.googleapis.com/drive/v3/files?fields=id',{method:'POST',headers:{Authorization:`Bearer ${token}`,'Content-Type':'application/json'},body:JSON.stringify({name:'th-data',mimeType:'application/vnd.google-apps.folder',parents:['root'],appProperties:{thStorage:'v1'}}),signal:AbortSignal.timeout(30000)});
 if(!created.ok)throw Error('Cannot create th-data');return (await created.json()).id;
}
