import {createReadStream} from 'node:fs';
import {lstat,realpath} from 'node:fs/promises';
import {resolve,sep} from 'node:path';
import {createHash} from 'node:crypto';

export function attachmentReferences(event){
 if(!event.new)return [];
 const row=JSON.parse(event.new);
 if(event.schema==='portfolio'&&event.table==='media'&&row.ready)return [{path:`portfolio/${row.id}`,size:Number(row.size),type:row.type}];
 if(event.schema==='public'&&event.table==='files')return [{path:row.path,size:Number(row.size),type:row.type}];
 if(event.schema==='public'&&event.table==='mes_media'&&row.ready)return [{path:`media/${row.id}`,size:Number(row.size),type:row.type}];
 if(event.schema==='public'&&event.table==='mes_uploads'&&!row.complete&&Number(row.offset_bytes)>0)return [{path:`.uploads/${row.id}.part`,size:Number(row.offset_bytes),type:'application/octet-stream'}];
 return [];
}
export async function confinedFile(root,relative){
 if(typeof relative!=='string'||relative.includes('\0')||relative.split(/[\\/]/).some(x=>!x||x==='.'||x==='..')||relative.startsWith('/'))throw Error('Invalid media path');
 const base=await realpath(root);let path=base;
 for(const part of relative.split('/')){path=resolve(path,part);if((await lstat(path)).isSymbolicLink())throw Error('Media symlink forbidden');}
 path=await realpath(path);if(!path.startsWith(base+sep))throw Error('Media path escaped root');
 if(!(await lstat(path)).isFile())throw Error('Media is not a regular file');return path;
}
export function attachmentWriter(transport,roots){
 return async(app,event)=>{
  const refs=attachmentReferences(event);if(!refs.length)return null;
  const ref=refs[0],path=await confinedFile(roots[app],ref.path);
  const before=await lstat(path);if(before.size!==ref.size)throw Error('Media size differs from transaction');
  const sha=createHash('sha256'),md5=createHash('md5');
  for await(const chunk of createReadStream(path)){sha.update(chunk);md5.update(chunk);}
  const after=await lstat(path);if(after.size!==before.size||after.mtimeMs!==before.mtimeMs||after.ino!==before.ino)throw Error('Media changed during verification');
  const sha256=sha.digest('hex'),checksum=md5.digest('hex');
  const result=await transport.putLocalFile(`${app}/files/${sha256.slice(0,2)}/${sha256}`,path,{...ref,sha256,md5:checksum});
  const final=await lstat(path);if(final.size!==before.size||final.mtimeMs!==before.mtimeMs)throw Error('Media changed during upload');
  return {...result,path:ref.path};
 };
}
