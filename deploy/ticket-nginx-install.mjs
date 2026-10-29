import fs from 'node:fs';
import {spawnSync} from 'node:child_process';
const available='/host/etc/nginx/sites-available/th-ticket',enabled='/host/etc/nginx/sites-enabled/th-ticket';
const exists=path=>{try{fs.lstatSync(path);return true;}catch(error){if(error.code==='ENOENT')return false;throw error;}};
const check=()=>{const result=spawnSync('chroot',['/host','/usr/sbin/nginx','-t'],{encoding:'utf8'});if(result.status!==0)throw Error('Native Nginx configuration validation failed: '+String(result.stderr).slice(0,800));};
const reload=()=>{const pid=Number(fs.readFileSync('/host/run/nginx.pid','utf8').trim());if(!Number.isSafeInteger(pid)||pid<2||fs.readFileSync('/host/proc/'+pid+'/comm','utf8').trim()!=='nginx')throw Error('Nginx master identity differs');process.kill(pid,'SIGHUP');};
const rollback=()=>{const previous=JSON.parse(fs.readFileSync('/backup/previous.json','utf8'));if(exists(enabled))fs.unlinkSync(enabled);if(previous.available===null){if(exists(available))fs.unlinkSync(available);}else fs.writeFileSync(available,Buffer.from(previous.available,'base64'),{mode:0o644});if(previous.link!==null)fs.symlinkSync(previous.link,enabled);check();reload();};
if(process.argv[2]==='rollback'){rollback();console.log(JSON.stringify({rolledBack:true}));}
else{
 const candidate=fs.readFileSync('/control/ticket-nginx.conf');
 if(!candidate.toString().includes('server_name ticket.th.io.vn;')||!candidate.toString().includes('proxy_pass http://127.0.0.1:8082;'))throw Error('Unexpected Ticket origin configuration');
 const previous={available:exists(available)?fs.readFileSync(available).toString('base64'):null,link:exists(enabled)?fs.readlinkSync(enabled):null};
 fs.writeFileSync('/backup/previous.json',JSON.stringify(previous),{mode:0o600});
 try{fs.writeFileSync(available+'.new',candidate,{mode:0o644});fs.renameSync(available+'.new',available);if(exists(enabled))fs.unlinkSync(enabled);fs.symlinkSync('/etc/nginx/sites-available/th-ticket',enabled);check();reload();console.log(JSON.stringify({nginxConfigValidated:true,hostname:'ticket.th.io.vn',reloaded:true}));}
 catch(error){rollback();throw error;}
}
