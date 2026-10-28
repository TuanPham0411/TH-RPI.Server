import http from 'node:http';
import {timingSafeEqual} from 'node:crypto';
import {Readable} from 'node:stream';
import {pipeline} from 'node:stream/promises';
const equal=(a,b)=>typeof a==='string'&&typeof b==='string'&&Buffer.byteLength(a)===Buffer.byteLength(b)&&timingSafeEqual(Buffer.from(a),Buffer.from(b));
export function storageServer({coordinator,tokens}){
 return http.createServer(async(req,res)=>{
  res.setHeader('Content-Type','application/json');res.setHeader('Cache-Control','no-store');
  const app=req.headers['x-th-application'];
  const respond=(status,value)=>{res.statusCode=status;res.end(JSON.stringify(value));};
  if(!Object.hasOwn(tokens,app||'')||!equal(req.headers.authorization,`Bearer ${tokens[app]}`))return respond(403,{error:'Storage authentication required'});
  if(req.method!=='POST'||!['/v1/check','/v1/commit','/v1/history','/v1/event','/v1/file'].includes(req.url))return respond(404,{error:'Unknown storage operation'});
  try{
   const chunks=[];let size=0;for await(const chunk of req){size+=chunk.length;if(size>16*1024*1024){respond(413,{error:'Storage batch too large'});return;}chunks.push(chunk);}
   const body=JSON.parse(Buffer.concat(chunks));
   if(req.url==='/v1/check')return respond(200,await coordinator.check(app,body.parent));
   if(req.url==='/v1/commit')return respond(200,await coordinator.commit(app,body));
   if(req.url==='/v1/history')return respond(200,{history:await coordinator.history(app)});
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
  }catch(error){console.error('Storage operation failed',{application:app,operation:req.url,error:error.message});if(res.headersSent)res.destroy();else respond(503,{error:'Drive storage unavailable or projection differs; no local commit acknowledged'});}
 });
}
