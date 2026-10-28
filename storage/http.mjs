import http from 'node:http';
import {timingSafeEqual} from 'node:crypto';
import {Readable} from 'node:stream';
import {pipeline} from 'node:stream/promises';
const equal=(a,b)=>typeof a==='string'&&typeof b==='string'&&Buffer.byteLength(a)===Buffer.byteLength(b)&&timingSafeEqual(Buffer.from(a),Buffer.from(b));
export function storageServer({coordinator,tokens,photos,ownerAuth,status}){
 return http.createServer(async(req,res)=>{
  res.setHeader('Content-Type','application/json');res.setHeader('Cache-Control','no-store');
  const app=req.headers['x-th-application'];
  const respond=(status,value)=>{res.statusCode=status;res.end(JSON.stringify(value));};
  if(!Object.hasOwn(tokens,app||'')||!equal(req.headers.authorization,`Bearer ${tokens[app]}`))return respond(403,{error:'Storage authentication required'});
  const photosOperation=req.url.startsWith('/v1/photos/');
  const authOperation=req.url.startsWith('/v1/auth/');
  if(req.method!=='POST'||!photosOperation&&!authOperation&&!['/v1/check','/v1/commit','/v1/history','/v1/event','/v1/file','/v1/resolve-file','/v1/status'].includes(req.url))return respond(404,{error:'Unknown storage operation'});
  if(authOperation&&(app!=='ai-agent'||!ownerAuth))return respond(403,{error:'Owner authentication unavailable for this application'});
  if(photosOperation&&(app!=='portfolio'||!photos))return respond(403,{error:'Google Photos belongs to Portfolio only'});
  try{
   const chunks=[];let size=0;for await(const chunk of req){size+=chunk.length;if(size>16*1024*1024){respond(413,{error:'Storage batch too large'});return;}chunks.push(chunk);}
   const body=JSON.parse(Buffer.concat(chunks));
   if(authOperation){
    const action=req.url.slice('/v1/auth/'.length);
    if(action==='start')return respond(200,ownerAuth.start(body.binding));
    if(action==='callback')return respond(200,await ownerAuth.callback(body));
    if(action==='session')return respond(200,ownerAuth.session(body.session));
    if(action==='logout')return respond(200,await ownerAuth.logout(body.session));
    return respond(404,{error:'Unknown authentication operation'});
   }
   if(req.url==='/v1/status')return respond(200,status?await status(app):{ready:true});
   if(photosOperation){
    const action=req.url.slice('/v1/photos/'.length);
    if(action==='status')return respond(200,{connected:!!photos.credentials,owner:photos.oauth.expectedEmail});
    if(action==='start')return respond(200,photos.start(body.binding));
    if(action==='callback')return respond(200,await photos.callback(body));
    if(action==='session')return respond(200,await photos.create(body.binding));
    if(action==='poll')return respond(200,await photos.poll(body));
    if(action==='items')return respond(200,await photos.items(body));
    if(action==='download'){
     const {response,item}=await photos.download(body);res.statusCode=200;res.setHeader('Content-Type',item.type);
     const length=response.headers.get('content-length');if(length)res.setHeader('Content-Length',length);
     await pipeline(Readable.fromWeb(response.body),res);return;
    }
    return respond(404,{error:'Unknown Photos operation'});
   }
   if(req.url==='/v1/check')return respond(200,await coordinator.check(app,body.parent));
   if(req.url==='/v1/commit')return respond(200,await coordinator.commit(app,body));
   if(req.url==='/v1/history')return respond(200,{history:await coordinator.history(app)});
   if(req.url==='/v1/resolve-file'){
    const history=await coordinator.history(app),position=history.findIndex(marker=>marker.id===body.head);
    if(position<0)return respond(409,{error:'Local projection has no recoverable Drive head'});
    for(const marker of history.slice(0,position+1).reverse()){
     const packet=await coordinator.event(app,marker);const file=packet.files.find(value=>value.path===body.path);
     if(file)return respond(200,file);
    }
    return respond(404,{error:'File has no committed Drive mapping'});
   }
   if(req.url==='/v1/file'){
    const history=await coordinator.history(app);let file;
    for(const marker of history.reverse()){
     const packet=await coordinator.event(app,marker);file=packet.files.find(f=>f.path===body.path&&f.sha256===body.sha256);
     if(file)break;
    }
    if(!file)return respond(404,{error:'Committed file unavailable'});
    const response=await coordinator.transport.request(`files/${encodeURIComponent(file.id)}?alt=media`);
    res.statusCode=200;res.setHeader('Content-Type','application/octet-stream');res.setHeader('Content-Length',String(file.size));
    await pipeline(Readable.fromWeb(response.body),res);return;
   }
   const history=await coordinator.history(app),marker=history.find(x=>x.id===body.id);if(!marker)return respond(404,{error:'Commit unavailable'});
   return respond(200,await coordinator.event(app,marker));
  }catch(error){console.error('Storage operation failed',{application:app,operation:req.url,error:error.message});if(res.headersSent)res.destroy();else respond(503,{error:photosOperation?'Google Photos is not connected, the selection expired, or Picker API is unavailable. Connect Photos and select again.':'Drive storage unavailable or projection differs; no local commit acknowledged'});}
 });
}
