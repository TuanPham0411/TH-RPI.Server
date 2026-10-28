import http from 'node:http';
import {randomUUID} from 'node:crypto';
import {createHash} from 'node:crypto';
import {createWriteStream,existsSync} from 'node:fs';
import {rename,rm,mkdir,stat} from 'node:fs/promises';
import {dirname,resolve,sep} from 'node:path';
import {Transform} from 'node:stream';
import {pipeline} from 'node:stream/promises';

export async function downloadFile(config,reference,root){
 if(typeof reference.path!=='string'||reference.path.split(/[\\/]/).some(x=>!x||x==='.'||x==='..')||reference.path.startsWith('/')||!/^[a-f0-9]{64}$/.test(reference.sha256))throw Error('Invalid recovery file');
 const base=resolve(root),path=resolve(base,reference.path);if(!path.startsWith(base+sep))throw Error('Invalid recovery destination');
 try{await stat(path);throw Error('Recovery destination already exists');}catch(error){if(error.code!=='ENOENT')throw error;}
 await mkdir(dirname(path),{recursive:true,mode:0o700});const temporary=path+'.'+randomUUID()+'.tmp';
 try{
  const body=Buffer.from(JSON.stringify({path:reference.path,sha256:reference.sha256}));
  const response=await new Promise((resolveResponse,reject)=>{
   const request=http.request({socketPath:config.socketPath,path:'/v1/file',method:'POST',headers:{Authorization:`Bearer ${config.token}`,'X-TH-Application':config.application,'Content-Type':'application/json','Content-Length':body.length}},res=>{if(res.statusCode!==200){res.resume();reject(Error('Drive recovery download failed'));}else resolveResponse(res);});request.setTimeout(300000,()=>request.destroy(Error('Recovery download timed out')));request.on('error',reject);request.end(body);
  });
  const digest=createHash('sha256');let size=0;
  await pipeline(response,new Transform({transform(chunk,encoding,callback){size+=chunk.length;digest.update(chunk);callback(size>reference.size?Error('Recovery file size differs'):null,chunk);}}),createWriteStream(temporary,{flags:'wx',mode:0o600}));
  if(size!==reference.size||digest.digest('hex')!==reference.sha256)throw Error('Recovery file checksum differs');
  await rename(temporary,path);return path;
 }finally{await rm(temporary,{force:true});}
}

export function brokerRequest({socketPath,token,application},path,value) {
 return new Promise((resolve,reject)=>{
  const bytes=Buffer.from(JSON.stringify(value));
  const req=http.request({socketPath,path,method:'POST',headers:{'Content-Type':'application/json','Content-Length':bytes.length,Authorization:`Bearer ${token}`,'X-TH-Application':application}},res=>{
   let data='';res.on('data',chunk=>{data+=chunk;if(data.length>4*1024*1024)res.destroy(Error('Broker response too large'));});
   res.on('error',reject);res.on('end',()=>{try{const result=JSON.parse(data);if(res.statusCode!==200)throw Object.assign(Error(result.error||'Drive operation failed'),{status:503,expose:true,code:'storage-unavailable'});resolve(result);}catch(error){reject(error);}});
  });
  req.setTimeout(300000,()=>req.destroy(Error('Drive commit outcome unknown; reconciliation required')));
  req.on('error',()=>reject(Object.assign(Error('TH-Server storage is unavailable. Your save was not acknowledged; retry after recovery.'),{status:503,expose:true,code:'storage-unavailable'})));req.end(bytes);
 });
}

export function brokerStream({socketPath,token,application},path,value){
 return new Promise((resolveResponse,reject)=>{
  const body=Buffer.from(JSON.stringify(value));
  const request=http.request({socketPath,path,method:'POST',headers:{Authorization:`Bearer ${token}`,'X-TH-Application':application,'Content-Type':'application/json','Content-Length':body.length}},response=>{
   if(response.statusCode!==200){response.resume();reject(Error('Google Photos selection is unavailable. Connect Photos and choose the image again.'));}else resolveResponse(response);
  });request.setTimeout(60000,()=>request.destroy(Error('Google Photos download timed out')));request.on('error',reject);request.end(body);
 });
}

// Every database transaction is staged locally, acknowledged by Drive, and only
// then committed. Existing SQL BEGIN/COMMIT blocks keep their transaction boundary.
// Keep the unwrapped operator pool for migrations/restore; never use it in routes.
export function driveFirstPool(pool,{commit,checkHead}={}) {
 if(typeof commit!=='function'||typeof checkHead!=='function')throw Error('Broker callbacks required');
 async function connection(){
  const db=await pool.connect();let active=false,readOnly=false,broken=false,finished=false;
  async function begin(read=false){
   await db.query(read?'BEGIN READ ONLY':'BEGIN');active=true;readOnly=read;
   try{if(!readOnly)await db.query('SELECT public.th_storage_lock()');}
   catch(error){await db.query('ROLLBACK');active=false;throw error;}
  }
  async function complete(){
   if(!active)throw Error('No active transaction');
   try{
    if(readOnly){await db.query('COMMIT');active=false;readOnly=false;return;}
    const {rows:[row]}=await db.query('SELECT public.th_storage_pending() AS pending');
    if(row.pending.events.length){
     await checkHead(row.pending.parent);
     const id=randomUUID();const result=await commit({schemaVersion:1,id,...row.pending});
     if(result?.id!==id||result?.committed!==true)throw Error('Invalid Drive acknowledgement');
     await db.query('SELECT public.th_storage_ack($1)',[id]);
    }
    await db.query('COMMIT');active=false;
   }catch(error){broken=true;await db.query('ROLLBACK').catch(()=>{});active=false;throw error;}
  }
  return {
   async query(sql,params){
    if(finished||broken)throw Error('Database transaction unavailable');
    const command=(typeof sql==='string'?sql:sql.text).trim().replace(/;$/,'').toUpperCase();
    if(command==='BEGIN'||command==='START TRANSACTION'){if(active)throw Error('Nested transaction not supported');await begin();return {rows:[],rowCount:0};}
    if(command==='COMMIT'){await complete();return {rows:[],rowCount:0};}
    if(command==='ROLLBACK'){await db.query('ROLLBACK');active=false;return {rows:[],rowCount:0};}
    // Disallow hidden transaction control in batched statements. Repository
    // multi-statement housekeeping queries contain only DELETE statements.
    if(/(?:^|;)\s*(?:BEGIN|COMMIT|END|START\s+TRANSACTION|ROLLBACK)\b/i.test(typeof sql==='string'?sql:sql.text))throw Error('Use explicit transaction calls');
    const implicit=!active;
    const text=typeof sql==='string'?sql:sql.text;
    const simpleRead=/^\s*SELECT\b/i.test(text)&&!/[;]\s*\S/.test(text)&&!/\bpublic\.(?:"[^"]+"|\w+)\s*\(/i.test(text)&&!/\bFOR\s+(?:UPDATE|SHARE|NO\s+KEY\s+UPDATE|KEY\s+SHARE)\b/i.test(text);
    // PostgreSQL itself enforces READ ONLY. Unexpected side-effecting functions
    // fail instead of bypassing capture; explicit/public function calls are gated.
    if(implicit)await begin(simpleRead);
    try{const result=await db.query(sql,params);if(implicit)await complete();return result;}
    catch(error){if(implicit){await db.query('ROLLBACK').catch(()=>{});active=false;}throw error;}
   },
   release(){if(finished)return;finished=true;if(active){broken=true;void db.query('ROLLBACK').finally(()=>db.release(true));}else db.release(broken);}
  };
 }
 return new Proxy(pool,{get(target,key){
  if(key==='connect')return connection;
  if(key==='query')return async(sql,params)=>{const db=await connection();try{return await db.query(sql,params);}finally{db.release();}};
  const value=Reflect.get(target,key,target);return typeof value==='function'?value.bind(target):value;
 }});
}

export function managedPool(pool,application,env=process.env){
 if(env.TH_STORAGE_MODE!=='drive-first')return pool;
 if(!env.TH_STORAGE_TOKEN||!env.TH_STORAGE_SOCKET)throw Error('Central storage connection is required');
 const config={socketPath:env.TH_STORAGE_SOCKET,token:env.TH_STORAGE_TOKEN,application};
 return driveFirstPool(pool,{
  checkHead:parent=>{
   if(existsSync(resolve(dirname(config.socketPath),'maintenance'))||existsSync(resolve(dirname(config.socketPath),'maintenance-'+application)))throw Error('Storage maintenance in progress');
   return brokerRequest(config,'/v1/check',{parent});
  },
  commit:value=>brokerRequest(config,'/v1/commit',value)
 });
}
