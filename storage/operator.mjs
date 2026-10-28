import {randomUUID} from 'node:crypto';
import {brokerRequest,driveFirstPool} from './client.mjs';
import {attachmentReferences} from './attachments.mjs';
const quote=value=>'"'+value.replaceAll('"','""')+'"';
const qualified=(schema,table)=>quote(schema)+'.'+quote(table);
const migrationTables=['schema_migrations','portfolio_schema_migrations','ai_schema_migrations'];
const allowed=table=>['public','portfolio','mes_private'].includes(table.schema)&&!migrationTables.includes(table.table);
async function migrations(db){
 const result={};
 for(const name of migrationTables){const {rows:[exists]}=await db.query('SELECT to_regclass($1) AS name',['public.'+name]);if(exists.name)result[name]=(await db.query(`SELECT row_to_json(t)::text AS data FROM public.${quote(name)} t ORDER BY row_to_json(t)::text COLLATE "C"`)).rows.map(row=>row.data);}
 return result;
}

export async function baseline(db,broker){
 await db.query('BEGIN');
 try{
  await db.query('SELECT public.th_storage_lock()');
  // Operator must stop all application/background writers before invoking this.
  const {history}=await broker('/v1/history',{});const parent=history.at(-1)?.id??null;
  const {rows:tables}=await db.query("select schemaname as schema,tablename as table from pg_tables where schemaname in ('public','portfolio','mes_private') and tablename not in ('schema_migrations','portfolio_schema_migrations','ai_schema_migrations') order by schemaname,tablename");
  const events=[];
  for(const table of tables){
   const rows=await db.query(`SELECT row_to_json(t)::text AS data FROM ${qualified(table.schema,table.table)} t ORDER BY row_to_json(t)::text COLLATE "C"`);
   for(const row of rows.rows)events.push({...table,action:'BASELINE',old:null,new:row.data});
  }
  const {rows:sequences}=await db.query("select schemaname as schema,sequencename as name,last_value from pg_sequences where schemaname in ('public','portfolio','mes_private') order by schemaname,sequencename");
  const metadata={tables,sequences,migrations:await migrations(db),release:process.env.TH_RELEASE||null};
  // Empty databases still have a durable baseline and explicit table inventory.
  events.push({schema:'server',table:'manifest',action:'BASELINE',old:null,new:JSON.stringify({kind:'database-baseline',...metadata})});
  const id=randomUUID();const result=await broker('/v1/commit',{schemaVersion:1,id,parent,events,baseline:metadata});
  if(result.id!==id||result.committed!==true)throw Error('Baseline was not acknowledged');
  await db.query('SELECT public.th_storage_ack($1)',[id]);
  await db.query('UPDATE th_storage.head SET enforced=true WHERE id');
  await db.query('DELETE FROM th_storage.events');
  await db.query('COMMIT');return {id,tables:tables.length,records:events.length-1};
 }catch(error){await db.query('ROLLBACK');throw error;}
}

export async function restore(db,broker,{confirmEmpty=false,restoreFile}={}){
 if(!confirmEmpty)throw Error('Restore is allowed only into a dedicated, empty recovery database');
 const {history}=await broker('/v1/history',{});if(!history.length)throw Error('No Drive history');
 const packets=[];let start=-1;
 for(const marker of history){const packet=await broker('/v1/event',{id:marker.id});packets.push(packet);if(packet.baseline)start=packets.length-1;}
 if(start<0)throw Error('No baseline in Drive history');
 const base=packets[start].baseline;
 if(!Array.isArray(base.tables)||base.tables.some(table=>!allowed(table)))throw Error('Invalid baseline inventory');
 if(JSON.stringify(base.migrations||{})!==JSON.stringify(await migrations(db)))throw Error('Recovery schema migrations differ; use the baseline application release');
 await db.query('BEGIN');
 try{
  await db.query('SELECT public.th_storage_lock()');
  for(const table of base.tables){const result=await db.query(`SELECT 1 FROM ${qualified(table.schema,table.table)} LIMIT 1`);if(result.rowCount)throw Error('Recovery table is not empty');}
  await db.query("SET LOCAL session_replication_role='replica'");
  for(const packet of packets.slice(start))for(const event of packet.events){
   if(event.schema==='server'&&event.table==='manifest')continue;
   if(!allowed(event)||!base.tables.some(t=>t.schema===event.schema&&t.table===event.table))throw Error('Event references an unknown recovery table');
   const table=qualified(event.schema,event.table);
   if(event.action==='TRUNCATE'){await db.query(`TRUNCATE ${table}`);continue;}
   if(event.old!==null){const removed=await db.query(`DELETE FROM ${table} t WHERE to_jsonb(t)=$1::jsonb`,[event.old]);if(removed.rowCount!==1)throw Error('Recovery before-image differs');}
   if(event.new!==null)await db.query(`INSERT INTO ${table} OVERRIDING SYSTEM VALUE SELECT * FROM json_populate_record(NULL::${table},$1::json)`,[event.new]);
  }
  // Restore sequence positions from both the baseline and the actual records.
  for(const sequence of base.sequences||[]){if(!['public','portfolio','mes_private'].includes(sequence.schema))throw Error('Invalid sequence');if(sequence.last_value!==null)await db.query('SELECT setval($1::regclass,$2::bigint,true)',[qualified(sequence.schema,sequence.name),sequence.last_value]);}
  const owned=await db.query(`select ns.nspname as schema,t.relname as table,a.attname as column,pg_get_serial_sequence(format('%I.%I',ns.nspname,t.relname),a.attname) as sequence from pg_class t join pg_namespace ns on ns.oid=t.relnamespace join pg_attribute a on a.attrelid=t.oid and a.attnum>0 and not a.attisdropped where ns.nspname in ('public','portfolio','mes_private') and t.relkind='r'`);
  for(const row of owned.rows)if(row.sequence){const {rows:[maximum]}=await db.query(`SELECT max(${quote(row.column)})::text AS value FROM ${qualified(row.schema,row.table)}`);if(maximum.value!==null)await db.query('SELECT setval($1::regclass,greatest((SELECT last_value FROM '+row.sequence+'),$2::bigint),true)',[row.sequence,maximum.value]);}
  await verifyForeignKeys(db);
  const references=new Map();
  for(const packet of packets.slice(start))for(const file of packet.files)references.set(file.path,file);
  for(const table of base.tables){
   const rows=await db.query(`SELECT row_to_json(t)::text AS data FROM ${qualified(table.schema,table.table)} t`);
   for(const row of rows.rows)for(const ref of attachmentReferences({...table,new:row.data})){
    const file=references.get(ref.path);if(!file||file.size!==ref.size)throw Error('Missing recovery media mapping');
    if(!restoreFile)throw Error('A media recovery destination is required');await restoreFile(file);
   }
  }
  await db.query("SET LOCAL session_replication_role='origin'");
  await db.query('SELECT public.th_storage_ack($1)',[history.at(-1).id]);
  await db.query('UPDATE th_storage.head SET enforced=true WHERE id');
  await db.query('DELETE FROM th_storage.events');
  await db.query('COMMIT');return {id:history.at(-1).id,operations:packets.length-start};
 }catch(error){await db.query('ROLLBACK');throw error;}
}

async function verifyForeignKeys(db){
 const {rows}=await db.query(`select c.conrelid::regclass::text as child,c.confrelid::regclass::text as parent,c.confmatchtype as match,
 array(select attname::text from pg_attribute where attrelid=c.conrelid and attnum=any(c.conkey) order by array_position(c.conkey,attnum)) as ck,
 array(select attname::text from pg_attribute where attrelid=c.confrelid and attnum=any(c.confkey) order by array_position(c.confkey,attnum)) as pk
 from pg_constraint c join pg_namespace n on n.oid=c.connamespace where c.contype='f' and n.nspname in ('public','portfolio','mes_private')`);
 for(const row of rows){
  const all=row.ck.map(key=>`c.${quote(key)} IS NOT NULL`).join(' AND ');
  const join=row.ck.map((key,i)=>`c.${quote(key)}=p.${quote(row.pk[i])}`).join(' AND ');
  const missing=`((${all}) AND NOT EXISTS(SELECT 1 FROM ${row.parent} p WHERE ${join}))`;
  const partial=row.match==='f'?` OR ((${row.ck.map(key=>`c.${quote(key)} IS NULL`).join(' OR ')}) AND (${row.ck.map(key=>`c.${quote(key)} IS NOT NULL`).join(' OR ')}))`:'';
  if((await db.query(`SELECT 1 FROM ${row.child} c WHERE ${missing}${partial} LIMIT 1`)).rowCount)throw Error('Recovery foreign key validation failed');
 }
}

export function operatorBroker(application,env=process.env){return (path,value)=>brokerRequest({application,socketPath:env.TH_STORAGE_SOCKET,token:env.TH_STORAGE_TOKEN},path,value);}

export async function verifyWriteThrough(pool,broker){
 let operation;
 const guarded=driveFirstPool(pool,{checkHead:parent=>broker('/v1/check',{parent}),commit:async input=>{operation=input.id;return broker('/v1/commit',input);}});
 const db=await guarded.connect(),id=randomUUID();
 try{await db.query('BEGIN');await db.query('INSERT INTO public.th_storage_probe(id,value) VALUES($1,$2)',[id,{purpose:'drive-first-verification'}]);await db.query('DELETE FROM public.th_storage_probe WHERE id=$1',[id]);await db.query('COMMIT');}
 catch(error){await db.query('ROLLBACK').catch(()=>{});throw error;}finally{db.release();}
 const packet=await broker('/v1/event',{id:operation});
 if(packet.events.length!==2||packet.events[0].table!=='th_storage_probe'||packet.events[1].action!=='DELETE')throw Error('Write-through verification differs');
 return {driveFirstVerified:true,operation};
}
