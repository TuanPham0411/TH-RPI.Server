// Rebuild local caches in journal order, checkpointing only after verified replay.
// Content checksums/decryption remain enforced by Coordinator.event.
export async function replayHistory({application,history,coordinator,transport,apply,checkpoint,progress=()=>{}}){
 if(!history.length)return;
 await transport.list(`${application}/events/`);
 let batch=[],bytes=0,completed=0;
 const flush=async()=>{
  if(!batch.length)return;
  const selected=batch;batch=[];bytes=0;
  const packets=await Promise.all(selected.map(marker=>coordinator.event(application,marker)));
  for(let i=0;i<packets.length;i++)await apply(packets[i],selected[i]);
  completed+=packets.length;
  if(completed%100<selected.length||completed===history.length)progress(completed);
 };
 for(const marker of history){
  const size=transport.estimatedSize?.(`${application}/events/${marker.id}.json`)||8*1024*1024;
  if(batch.length>=4||bytes+size>8*1024*1024)await flush();
  batch.push(marker);bytes+=size;
 }
 await flush();
 await checkpoint(history.at(-1));
}
