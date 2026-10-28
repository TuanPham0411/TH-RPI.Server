import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import pg from 'pg';
import {Coordinator} from '../storage/coordinator.mjs';
import {driveFirstPool} from '../storage/client.mjs';
import {baseline,restore} from '../storage/operator.mjs';

class Memory {data=new Map();async get(key){return this.data.has(key)?Buffer.from(this.data.get(key)):null;}async put(key,bytes){if(this.data.has(key)&&!this.data.get(key).equals(bytes))throw Error('Immutable conflict');this.data.set(key,Buffer.from(bytes));}async list(prefix){return [...this.data.keys()].filter(x=>x.startsWith(prefix));}}
test('PostgreSQL capture, Drive-first commit, rollback and empty-database restore',{skip:!process.env.TEST_DATABASE_URL},async()=>{
 const url=new URL(process.env.TEST_DATABASE_URL);if(!url.pathname.endsWith('_test'))throw Error('Dedicated _test database required');
 const admin=new pg.Client({connectionString:new URL('/postgres',url).href});await admin.connect();
 const restoreName=url.pathname.slice(1)+'_recovery_test';
 await admin.query(`CREATE DATABASE "${restoreName}"`);
 const source=new pg.Pool({connectionString:url.href});const recovery=new pg.Pool({connectionString:new URL('/'+restoreName,url).href});
 const capture=await readFile(new URL('../storage/capture.sql',import.meta.url),'utf8');
 const schema=`CREATE SCHEMA mes_private;CREATE TABLE public.records(id bigint generated always as identity primary key,value jsonb not null);CREATE TABLE mes_private.users(id text primary key,password text not null,record bigint references public.records(id));`;
 const remote=new Memory(),coordinator=new Coordinator({transport:remote,wrappingSecret:'isolated-test-secret-for-integration'});
 const broker=async(path,value)=>{if(path==='/v1/history')return {history:await coordinator.history('mes-ticket')};if(path==='/v1/event')return coordinator.event('mes-ticket',(await coordinator.history('mes-ticket')).find(x=>x.id===value.id));if(path==='/v1/commit')return coordinator.commit('mes-ticket',value);throw Error('Unknown path');};
 try{
  await source.query(schema);await recovery.query(schema);await source.query(capture);await recovery.query(capture);
  await source.query("INSERT INTO public.records(value) VALUES('{\"large\":9223372036854775807}')");
  await source.query("INSERT INTO mes_private.users VALUES('owner','bcrypt-hash',1)");
  const sourceClient=await source.connect();try{await baseline(sourceClient,broker);}finally{sourceClient.release();}
  await assert.rejects(source.query("INSERT INTO public.records(value) VALUES('{}')"),/Central Drive/);
  const guarded=driveFirstPool(source,{checkHead:parent=>coordinator.check('mes-ticket',parent),commit:value=>coordinator.commit('mes-ticket',value)});
  await guarded.query("UPDATE public.records SET value='{\"large\":9223372036854775806}' WHERE id=1");
  const transaction=await guarded.connect();await transaction.query('BEGIN');await transaction.query("INSERT INTO public.records(value) VALUES('{\"new\":true}')");await transaction.query("INSERT INTO mes_private.users VALUES('other','other-hash',currval(pg_get_serial_sequence('public.records','id')))");await transaction.query('COMMIT');transaction.release();
  const bad=driveFirstPool(source,{checkHead:parent=>coordinator.check('mes-ticket',parent),commit:async()=>{throw Error('Drive offline');}});
  await assert.rejects(bad.query("DELETE FROM mes_private.users WHERE id='owner'"),/offline/);
  assert.equal((await source.query("SELECT count(*) FROM mes_private.users WHERE id='owner'")).rows[0].count,'1');
  const client=await recovery.connect();try{await restore(client,broker,{confirmEmpty:true});}finally{client.release();}
  for(const table of ['public.records','mes_private.users'])assert.deepEqual((await source.query(`SELECT row_to_json(t)::text AS row FROM ${table} t ORDER BY row_to_json(t)::text`)).rows,(await recovery.query(`SELECT row_to_json(t)::text AS row FROM ${table} t ORDER BY row_to_json(t)::text`)).rows);
  assert.equal((await source.query('SELECT public.th_storage_head() AS head')).rows[0].head,(await recovery.query('SELECT public.th_storage_head() AS head')).rows[0].head);
  assert.ok(![...remote.data.values()].some(bytes=>bytes.toString().includes('bcrypt-hash')));
  const restored=driveFirstPool(recovery,{checkHead:parent=>coordinator.check('mes-ticket',parent),commit:value=>coordinator.commit('mes-ticket',value)});
  const row=await restored.query("INSERT INTO public.records(value) VALUES('{}') RETURNING id");assert.ok(Number(row.rows[0].id)>2);
 }finally{await source.end();await recovery.end();await admin.query(`DROP DATABASE "${restoreName}" WITH (FORCE)`);await admin.end();}
});
