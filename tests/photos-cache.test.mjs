import test from 'node:test';
import assert from 'node:assert/strict';
import {attachmentReferences} from '../storage/attachments.mjs';
test('Google Photos caches are excluded from Drive attachment uploads and restore inventories',()=>{
 const base={schema:'portfolio',table:'media'};
 assert.deepEqual(attachmentReferences({...base,new:JSON.stringify({id:'photo',ready:true,size:20,type:'image/jpeg',storage_source:'google-photos'})}),[]);
 assert.equal(attachmentReferences({...base,new:JSON.stringify({id:'upload',ready:true,size:20,type:'image/jpeg',storage_source:'drive'})})[0].path,'portfolio/upload');
 assert.equal(attachmentReferences({...base,new:JSON.stringify({id:'legacy',ready:true,size:20,type:'image/jpeg'})})[0].path,'portfolio/legacy');
});
