-- Install as the database operator. Runtime roles receive EXECUTE only.
create schema if not exists th_storage;
revoke all on schema th_storage from public;
create table if not exists th_storage.head(id boolean primary key default true check(id),operation text,enforced boolean not null default false);
alter table th_storage.head add column if not exists enforced boolean not null default false;
insert into th_storage.head(id,operation) values(true,null) on conflict do nothing;
create table if not exists th_storage.events(
 id bigint generated always as identity primary key,
 transaction_id bigint not null default txid_current(),
 schema_name text not null,table_name text not null,operation text not null,
 old_row text,new_row text
);
create or replace function th_storage.capture() returns trigger
language plpgsql security definer set search_path=pg_catalog,th_storage as $$
begin
 if (select enforced from th_storage.head where id) and coalesce(current_setting('th_storage.authorized',true),'')<>'1' then
  raise exception 'Central Drive storage transaction required';
 end if;
 insert into th_storage.events(schema_name,table_name,operation,old_row,new_row)
 values(TG_TABLE_SCHEMA,TG_TABLE_NAME,TG_OP,
 case when TG_OP in ('UPDATE','DELETE') then row_to_json(OLD)::text end,
 case when TG_OP in ('INSERT','UPDATE') then row_to_json(NEW)::text end);
 return null;
end $$;
create or replace function public.th_storage_pending() returns json
language sql security definer set search_path=pg_catalog,th_storage as $$
 select json_build_object('parent',(select operation from th_storage.head where id),
 'events',coalesce((select json_agg(json_build_object('schema',schema_name,'table',table_name,'action',operation,'old',old_row,'new',new_row) order by id)
 from th_storage.events where transaction_id=txid_current()),'[]'::json))
$$;
create or replace function public.th_storage_ack(value text) returns void
language plpgsql security definer set search_path=pg_catalog,th_storage as $$
begin
 if value !~ '^[a-f0-9-]{36}$' then raise exception 'Invalid operation'; end if;
 update th_storage.head set operation=value where id;
 delete from th_storage.events where transaction_id=txid_current();
end $$;
create or replace function public.th_storage_lock() returns void
language plpgsql security definer set search_path=pg_catalog as $$ begin perform pg_advisory_xact_lock(74632199);perform set_config('th_storage.authorized','1',true);end $$;
create or replace function public.th_storage_head() returns text
language sql security definer set search_path=pg_catalog,th_storage as $$ select operation from th_storage.head where id $$;
do $$ declare t record; begin
 for t in select schemaname,tablename from pg_tables where schemaname in ('public','portfolio','mes_private')
 and tablename not in ('schema_migrations','portfolio_schema_migrations','ai_schema_migrations') loop
  execute format('drop trigger if exists th_capture_row on %I.%I',t.schemaname,t.tablename);
  execute format('create trigger th_capture_row after insert or update or delete on %I.%I for each row execute function th_storage.capture()',t.schemaname,t.tablename);
  execute format('drop trigger if exists th_capture_truncate on %I.%I',t.schemaname,t.tablename);
  execute format('create trigger th_capture_truncate after truncate on %I.%I for each statement execute function th_storage.capture()',t.schemaname,t.tablename);
 end loop;
end $$;
revoke execute on function public.th_storage_pending(),public.th_storage_ack(text),public.th_storage_lock(),public.th_storage_head() from public;
do $$ declare r text; begin
 foreach r in array array['th_api','th_ai_api'] loop
  if exists(select 1 from pg_roles where rolname=r) then
   execute format('grant execute on function public.th_storage_pending(),public.th_storage_ack(text),public.th_storage_lock(),public.th_storage_head() to %I',r);
  end if;
 end loop;
end $$;
