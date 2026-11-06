// Preserve recorded migration dates only in a verified empty recovery database.
import pg from 'pg';
import {readFile} from 'node:fs/promises';
export async function prepareMigrationDates(db,baseline){
 const recorded=baseline.migrations?.ai_schema_migrations?.map(row=>JSON.parse(row));
 if(!recorded?.length||recorded.some(row=>Object.keys(row).sort().join(',')!=='applied_at,version'||!Number.isInteger(row.version)||!Number.isFinite(Date.parse(row.applied_at))))throw Error('Invalid AI migration manifest');
 const current=(await db.query('SELECT version FROM ai_schema_migrations ORDER BY version')).rows;
 if(JSON.stringify(current.map(row=>row.version))!==JSON.stringify(recorded.map(row=>row.version).sort((a,b)=>a-b)))throw Error('Recovery migration versions differ');
 if(!Array.isArray(baseline.tables))throw Error('Missing table inventory');
 await db.query('BEGIN');
 try{
  for(const table of baseline.tables){
   if(table.schema!=='public'||!/^[a-z_][a-z_0-9]*$/.test(table.table)||table.table==='ai_schema_migrations')throw Error('Invalid AI recovery table');
   if((await db.query(`SELECT 1 FROM public."${table.table}" LIMIT 1`)).rowCount)throw Error('Recovery database must be empty');
  }
  for(const row of recorded)await db.query('UPDATE ai_schema_migrations SET applied_at=$2::timestamptz WHERE version=$1',[row.version,row.applied_at]);
  await db.query('COMMIT');
 }catch(error){await db.query('ROLLBACK');throw error;}
}
if(process.argv[1]?.endsWith('/prepare-ai-recovery.mjs')){
 const manifest=JSON.parse(await readFile('/recovery/manifest.json','utf8'));
 const db=new pg.Client({connectionString:process.env.DATABASE_URL});await db.connect();
 try{await prepareMigrationDates(db,manifest.baseline);console.log('Original AI migration versions and dates verified');}finally{await db.end();}
}
