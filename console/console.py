#!/usr/bin/env python3
"""TH-Server physical HDMI console. No HTTP listener or arbitrary command input."""
import argparse, concurrent.futures, curses, datetime, json, os, pathlib, re, shutil, socket, subprocess, threading, time, urllib.request
HOME = pathlib.Path('/home/thadmin/th-platform')
APPS = [('Portfolio',8080,'th.io.vn'),('AI.Agent',8081,'ai.th.io.vn'),('TH-Ticket',8082,'ticket.th.io.vn')]
SERVICES = ['docker','nginx','tailscaled','cloudflared']

def command(args, timeout=8):
    try:
        result=subprocess.run(args, text=True, capture_output=True, timeout=timeout, check=False)
        return result.stdout.strip() if result.returncode==0 else ''
    except (OSError, subprocess.TimeoutExpired): return ''

def json_command(args, fallback=None):
    try: return json.loads(command(args))
    except (ValueError, TypeError): return fallback

def read(path, default=''):
    try: return pathlib.Path(path).read_text().strip()
    except OSError: return default

def bytes_text(n):
    n=float(n)
    for unit in ['B','KiB','MiB','GiB','TiB']:
        if abs(n)<1024 or unit=='TiB': return f'{n:.1f} {unit}'
        n/=1024

def block_devices():
    tree=json_command(['lsblk','--json','--bytes','-o','NAME,PATH,TYPE,SIZE,FSTYPE,LABEL,MOUNTPOINTS,TRAN,RM,UUID'],{})
    found=[]
    def walk(items, usb=False):
        for item in items:
            external=usb or item.get('tran')=='usb'
            if external and item.get('type')=='part' and item.get('fstype'):
                found.append({k:item.get(k) for k in ['path','size','fstype','label','mountpoints','uuid']})
            walk(item.get('children',[]),external)
    walk(tree.get('blockdevices',[]));return found

def protected_mount(mountpoint):
    mount=pathlib.Path(mountpoint).resolve()
    if mount==pathlib.Path('/') or str(mount).startswith(('/boot','/var/lib/docker')): return True
    if not command(['docker','info','--format','{{.ServerVersion}}']): return True
    ids=command(['docker','ps','-q']).split()
    if ids:
        # Inspect one container at a time: newline-delimited JSON is not one array.
        for identifier in ids:
            mounts=json_command(['docker','inspect','--format','{{json .Mounts}}',identifier],None)
            if mounts is None:return True
            for item in mounts:
                source=pathlib.Path(item.get('Source','/')).resolve()
                if source==mount or mount in source.parents: return True
    return False

def usb_action(device, detach=False):
    parts={p['path']:p for p in block_devices()}
    if device not in parts or not re.fullmatch(r'/dev/[a-zA-Z0-9]+',device): raise ValueError('Choose an existing USB partition')
    part=parts[device];mounts=[m for m in part.get('mountpoints') or [] if m]
    if detach:
        if not mounts: raise ValueError('Partition is not mounted')
        for mount in mounts:
            if not mount.startswith('/mnt/th-usb-') or protected_mount(mount):
                raise ValueError('System, application or externally managed mount: refusing to unmount')
        subprocess.run(['sync'],check=True)
        for mount in mounts: subprocess.run(['umount',mount],check=True)
    else:
        if mounts: raise ValueError('Partition is already mounted')
        if part['fstype'] not in ['ext4','ext3','ext2','exfat','vfat','ntfs','ntfs3']: raise ValueError('Filesystem not supported by this menu')
        suffix=re.sub('[^a-zA-Z0-9-]','',part.get('uuid') or pathlib.Path(device).name)
        target=pathlib.Path('/mnt/th-usb-'+suffix)
        if target.is_symlink(): raise ValueError('Unsafe mountpoint')
        target.mkdir(mode=0o750,exist_ok=True)
        subprocess.run(['mount','-o','nosuid,nodev',device,str(target)],check=True)
        print('Mounted:',target,'\nExisting files were preserved. This does not relocate application databases.')

def health(app):
    name,port,host=app
    try:
        request=urllib.request.Request(f'http://127.0.0.1:{port}/api/'+('ready' if port==8081 else 'health'),headers={'Host':host})
        with urllib.request.urlopen(request,timeout=3) as response:
            value=json.load(response);good=response.status==200 and (value.get('ok') is True or value.get('status')=='ready')
        return name, 'HEALTHY' if good else 'NOT READY'
    except Exception: return name,'UNREACHABLE'

def snapshot():
    memory={}
    for line in read('/proc/meminfo').splitlines():
        key,value=line.split(':',1);memory[key]=int(value.strip().split()[0])*1024
    temp=read('/sys/class/thermal/thermal_zone0/temp');up=float(read('/proc/uptime','0').split()[0])
    disk=shutil.disk_usage('/');stat=read('/proc/stat').splitlines()[0].split()[1:];ticks=[int(n) for n in stat]
    addresses=json_command(['ip','-j','address'],[]) or []
    links=[{'name':i['ifname'],'state':i.get('operstate','UNKNOWN'),'addresses':[a['local'] for a in i.get('addr_info',[]) if a.get('scope')=='global']} for i in addresses if i.get('ifname')!='lo']
    tailscale=json_command(['tailscale','status','--json'],{}) or {}
    peers=[{'name':p.get('HostName','?'),'online':p.get('Online',False),'ip':(p.get('TailscaleIPs') or ['-'])[0]} for p in tailscale.get('Peer',{}).values()]
    units=command(['systemctl','list-units','--all','--no-legend','--plain','--type=service','actions.runner.*']).splitlines()
    runners=[]
    for line in units:
        cells=line.split()
        if len(cells)>=4:runners.append({'unit':cells[0],'active':cells[2],'sub':cells[3]})
    service_states={name:command(['systemctl','show','--property=ActiveState','--value',name+'.service']) or 'unknown' for name in SERVICES}
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool: apps=dict(pool.map(health,APPS))
    deployments={}
    for app in ['ai-agent','portfolio','ticket']:
        try:
            row=json.loads((HOME/'state'/f'{app}-deployment.json').read_text());deployments[app]={'tag':str(row.get('imageTag',''))[:12],'ready':row.get('ready',False)}
        except (OSError,ValueError): deployments[app]={'tag':'unknown','ready':False}
    return {'at':time.time(),'hostname':socket.gethostname(),'uptime':int(up),'temperature':round(float(temp)/1000,1) if temp else None,'memory':memory,'disk':{'total':disk.total,'free':disk.free},'ticks':ticks,'load':os.getloadavg(),'gateway':command(['ip','route','show','default']),'connections':command(['nmcli','-t','-f','DEVICE,STATE,CONNECTION','device','status']),'dns':[line for line in read('/etc/resolv.conf').splitlines() if line.startswith('nameserver')],'links':links,'peers':peers,'tailscale':tailscale.get('BackendState','Unavailable'),'runners':runners,'services':service_states,'apps':apps,'deployments':deployments,'usb':block_devices()}

class Monitor:
    def __init__(self):self.value=None;self.error=None;self.stop=threading.Event();self.refresh=threading.Event()
    def loop(self):
        previous=None
        while not self.stop.is_set():
            try:
                value=snapshot();ticks=value['ticks'];total=sum(ticks);idle=ticks[3]+(ticks[4] if len(ticks)>4 else 0)
                value['cpu']=round(100*(1-(idle-previous[1])/(total-previous[0])),1) if previous and total>previous[0] else None
                previous=(total,idle);self.value=value;self.error=None
            except Exception as error:self.error=type(error).__name__
            self.refresh.wait(5);self.refresh.clear()

def lines_for(value,page):
    if not value:return ['Loading server status...']
    m=value['memory'];lines=[]
    if page==1:
        cpu=f"{value['cpu']:.1f}%" if value['cpu'] is not None else 'sampling'
        lines=[f"CPU {cpu}   Temperature {value['temperature'] if value['temperature'] is not None else '-'} C   Load {' / '.join(str(round(n,2)) for n in value['load'])}",f"RAM {bytes_text(m.get('MemTotal',0)-m.get('MemAvailable',0))} used / {bytes_text(m.get('MemTotal',0))}   Available {bytes_text(m.get('MemAvailable',0))}",f"Swap {bytes_text(m.get('SwapTotal',0)-m.get('SwapFree',0))} used / {bytes_text(m.get('SwapTotal',0))}",f"SD free {bytes_text(value['disk']['free'])} / {bytes_text(value['disk']['total'])}   Uptime {datetime.timedelta(seconds=value['uptime'])}",'','APPLICATION HEALTH']
        lines += [f"  {name:<15} {status:<14} {host}" for name,port,host in APPS for status in [value['apps'][name]]]
        lines += ['','HOST SERVICES']+[f'  {name:<16} {state}' for name,state in value['services'].items()]
        lines += ['','DEPLOYED IMAGES']+[f"  {app:<15} {row['tag']}  ready={row['ready']}" for app,row in value['deployments'].items()]
        lines += ['','NETWORK']+[f"  {i['name']:<12} {i['state']:<10} {', '.join(i['addresses']) or 'No address'}" for i in value['links']]
    elif page==2:
        lines=['NETWORK INTERFACES']+[f"  {i['name']:<12} {i['state']:<10} {', '.join(i['addresses']) or 'No address'}" for i in value['links']]
        lines += ['','ACTIVE CONNECTIONS']+value['connections'].splitlines()+['',value['gateway']]+value['dns']
        lines += ['',f"TAILSCALE: {value['tailscale']}"]+[f"  {p['name']:<25} {'ONLINE' if p['online'] else 'OFFLINE':<9} {p['ip']}" for p in value['peers']]
        lines += ['','N: configure wired / Wi-Fi with NetworkManager (administrator password).','Network changes can interrupt SSH. HDMI remains available.']
    elif page==3:
        lines=['GITHUB ACTIONS RUNNERS']+[f"  {r['active'].upper():<12} {r['sub']:<12} {r['unit'].removeprefix('actions.runner.').removesuffix('.service')}" for r in value['runners']]
        if not value['runners']:lines+=['No installed runner service found.']
        lines+=['','Runner state is reported by systemd. GitHub job availability can differ.']
    else:
        lines=['USB EXPANSION / EXISTING FILESYSTEMS']
        for part in value['usb']:
            lines += [f"  {part['path']}  {bytes_text(part['size'])}  {part['fstype']}  {part.get('label') or ''}",f"    {', '.join(p for p in part.get('mountpoints') or [] if p) or 'Not mounted'}"]
        if not value['usb']:lines+=['No formatted USB partition detected.']
        lines+=['','U: mount existing USB partition. E: safely unmount a console-managed USB.','No format or database relocation is performed.','Active application / system mounts cannot be removed from this menu.']
    return lines

def console(screen):
    curses.curs_set(0);screen.timeout(500);curses.start_color();curses.use_default_colors()
    for number,color in [(1,curses.COLOR_CYAN),(2,curses.COLOR_GREEN),(3,curses.COLOR_YELLOW),(4,curses.COLOR_RED)]:curses.init_pair(number,color,-1)
    monitor=Monitor();threading.Thread(target=monitor.loop,daemon=True).start();page=1;offset=0
    def write(y,x,text,style=0):
        height,width=screen.getmaxyx()
        if 0<=y<height and x<width:
            try:screen.addnstr(y,x,str(text).replace('\n',' '),max(0,width-x-1),style)
            except curses.error:pass
    def ask(prompt):
        screen.erase();write(1,2,prompt);write(3,2,'Type YES to continue, or press Enter to cancel: ');screen.refresh();screen.timeout(-1);curses.echo();curses.curs_set(1)
        try:return screen.getstr(4,2,80).decode(errors='replace')
        finally:curses.noecho();curses.curs_set(0);screen.timeout(500)
    def privileged(action,*args):
        curses.def_prog_mode();curses.endwin()
        try:
            subprocess.run(['runuser','-u','thadmin','--','sudo','--','/usr/bin/python3','/opt/th-server-console/current/console.py',action,*args],check=False)
            input('\nPress Enter to return to TH-Server...')
        finally:curses.reset_prog_mode();screen.clear();monitor.refresh.set()
    try:
        while True:
            screen.erase();height,width=screen.getmaxyx();v=monitor.value
            write(0,2,'TH-SERVER  /  LOCAL OPERATIONS',curses.color_pair(1)|curses.A_BOLD)
            write(1,2,f"{socket.gethostname()}   {datetime.datetime.now().strftime('%d/%m/%Y %H:%M:%S')}   Refresh 5s")
            write(3,2,'[1] Overview   [2] Network   [3] Runners   [4] USB',curses.A_BOLD)
            lines=lines_for(v,page)
            offset=max(0,min(offset,max(0,len(lines)-max(1,height-8))))
            for y,line in enumerate(lines[offset:offset+max(1,height-8)],5):
                color=4 if 'UNREACHABLE' in line or 'failed' in line else 3 if 'inactive' in line or 'OFFLINE' in line else 2 if 'HEALTHY' in line or 'ONLINE' in line or ' active' in line else 0
                write(y,2,line,curses.color_pair(color))
            stale=time.time()-v['at'] if v else 0
            footer='R Refresh | N Network | U Mount | E Eject | L Login tty2 | Arrows Scroll'
            if monitor.error or stale>30:footer=f"STATUS STALE ({monitor.error or str(int(stale))+'s'}). R to retry."
            write(height-2,2,footer,curses.color_pair(3) if monitor.error or stale>30 else curses.A_DIM)
            screen.refresh();key=screen.getch()
            if key in map(ord,'1234'):page=int(chr(key));offset=0
            elif key in [curses.KEY_DOWN,ord('j')]:offset+=1
            elif key in [curses.KEY_UP,ord('k')]:offset=max(0,offset-1)
            elif key in [ord('r'),ord('R')]:monitor.refresh.set()
            elif key in [ord('n'),ord('N')]:
                if ask('Open network configuration? This may interrupt remote connections.')=='YES':privileged('--network')
            elif key in [ord('u'),ord('U'),ord('e'),ord('E')]:
                parts=block_devices();screen.erase()
                for i,p in enumerate(parts):write(i+1,2,f"{i+1}: {p['path']} {bytes_text(p['size'])} {p['fstype']}")
                write(len(parts)+3,2,'Choose partition number (0 cancels):');screen.refresh();screen.timeout(-1);curses.echo()
                try:choice=screen.getstr(len(parts)+4,2,10).decode()
                finally:curses.noecho();screen.timeout(500)
                if choice.isdigit() and 0<int(choice)<=len(parts):
                    device=parts[int(choice)-1]['path'];detach=key in [ord('e'),ord('E')]
                    if ask(('Unmount' if detach else 'Mount')+' '+device+'? Existing files are preserved.')=='YES':privileged('--unmount' if detach else '--mount',device)
            elif key in [ord('l'),ord('L')]:
                subprocess.run(['systemctl','start','getty@tty2.service'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL);subprocess.run(['chvt','2'],check=False)
    finally:monitor.stop.set();monitor.refresh.set()

def main():
    parser=argparse.ArgumentParser();group=parser.add_mutually_exclusive_group();group.add_argument('--snapshot',action='store_true');group.add_argument('--network',action='store_true');group.add_argument('--mount');group.add_argument('--unmount');args=parser.parse_args()
    if args.snapshot:print(json.dumps(snapshot()));return
    if args.network or args.mount or args.unmount:
        if os.geteuid()!=0:raise SystemExit('Administrator authentication is required')
        if args.network:
            if not shutil.which('nmtui'):raise SystemExit('NetworkManager nmtui is not installed. Install network-manager on the Pi.')
            subprocess.run(['nmtui'],check=True)
        else:usb_action(args.mount or args.unmount,bool(args.unmount))
        return
    if not os.isatty(0):raise SystemExit('Connect a physical terminal or use --snapshot')
    curses.wrapper(console)
if __name__=='__main__':main()
