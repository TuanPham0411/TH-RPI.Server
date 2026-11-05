#!/usr/bin/env python3
"""TH-Server physical HDMI console. No HTTP listener or arbitrary command input."""
import argparse, curses, datetime, json, os, pathlib, pwd, re, shutil, socket, subprocess, threading, time, textwrap
import inventory
import monitor as inventory_monitor

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
    shared=inventory_monitor.load()
    discovered=shared['inventory'] if shared else inventory.collect()
    if shared and time.time()-shared['at']>30:
        discovered['errors']={**discovered['errors'],'monitor':'Last sample is older than 30 seconds'}
    return {'at':time.time(),'hostname':socket.gethostname(),'uptime':int(up),'temperature':round(float(temp)/1000,1) if temp else None,'memory':memory,'disk':{'total':disk.total,'free':disk.free},'ticks':ticks,'load':os.getloadavg(),'gateway':command(['ip','route','show','default']),'connections':command(['nmcli','-t','-f','DEVICE,STATE,CONNECTION','device','status']),'dns':[line for line in read('/etc/resolv.conf').splitlines() if line.startswith('nameserver')],'links':links,'peers':peers,'tailscale':tailscale.get('BackendState','Unavailable'),'inventory':discovered,'changes':shared['changes'] if shared else [],'inventory_at':shared['at'] if shared else time.time(),'usb':block_devices()}

class Monitor:
    def __init__(self):self.value=None;self.error=None;self.stop=threading.Event();self.refresh=threading.Event();self.events=[]
    def loop(self):
        previous=None
        while not self.stop.is_set():
            try:
                value=snapshot();ticks=value['ticks'];total=sum(ticks);idle=ticks[3]+(ticks[4] if len(ticks)>4 else 0)
                value['cpu']=round(100*(1-(idle-previous[1])/(total-previous[0])),1) if previous and total>previous[0] else None
                previous=(total,idle)
                self.events=(inventory.changes(self.value['inventory'] if self.value else None,value['inventory'])+self.events)[:100]
                value['changes']=value.get('changes') or self.events
                self.value=value;self.error=None
            except Exception as error:self.error=type(error).__name__
            self.refresh.wait(5);self.refresh.clear()

def lines_for(value,page):
    if not value:return ['Loading server status...']
    lines=[]
    if page==1:
        m=value['memory']
        cpu=f"{value['cpu']:.1f}%" if value['cpu'] is not None else 'sampling'
        lines=[f"CPU {cpu}   Temperature {value['temperature'] if value['temperature'] is not None else '-'} C   Load {' / '.join(str(round(n,2)) for n in value['load'])}",f"RAM {bytes_text(m.get('MemTotal',0)-m.get('MemAvailable',0))} used / {bytes_text(m.get('MemTotal',0))}   Available {bytes_text(m.get('MemAvailable',0))}",f"Swap {bytes_text(m.get('SwapTotal',0)-m.get('SwapFree',0))} used / {bytes_text(m.get('SwapTotal',0))}",f"SD free {bytes_text(value['disk']['free'])} / {bytes_text(value['disk']['total'])}   Uptime {datetime.timedelta(seconds=value['uptime'])}",'','APPLICATION HEALTH']
        discovered=value['inventory']
        lines += [f"  {row['name']}  {row['state']}  health={row['health']}  {row.get('project') or '-'}/{row.get('service') or '-'}" for row in discovered['containers']]
        if not discovered['containers']:lines+=['  No containers discovered.']
        lines += ['','Use [5] Applications / images and [6] all host services.','[7] lists changes detected by the monitor (or this session in standalone mode).']
        lines += ['','NETWORK']+[f"  {i['name']:<12} {i['state']:<10} {', '.join(i['addresses']) or 'No address'}" for i in value['links']]
    elif page==2:
        lines=['NETWORK INTERFACES']+[f"  {i['name']:<12} {i['state']:<10} {', '.join(i['addresses']) or 'No address'}" for i in value['links']]
        lines += ['','ACTIVE CONNECTIONS']+value['connections'].splitlines()+['',value['gateway']]+value['dns']
        lines += ['',f"TAILSCALE: {value['tailscale']}"]+[f"  {p['name']:<25} {'ONLINE' if p['online'] else 'OFFLINE':<9} {p['ip']}" for p in value['peers']]
        lines += ['','N: configure wired / Wi-Fi with NetworkManager (administrator password).','Network changes can interrupt SSH. HDMI remains available.']
    elif page==3:
        runners=[r for r in value['inventory']['services'] if r['id'].startswith('actions.runner.')]
        lines=['GITHUB ACTIONS RUNNERS']+[f"  {r['state'].upper():<12} {r['sub']:<12} {r['id']}" for r in runners]
        if not runners:lines+=['No installed runner service found.']
        lines+=['','Runner state is reported by systemd. GitHub job availability can differ.']
    elif page==4:
        lines=['USB EXPANSION / EXISTING FILESYSTEMS']
        for part in value['usb']:
            lines += [f"  {part['path']}  {bytes_text(part['size'])}  {part['fstype']}  {part.get('label') or ''}",f"    {', '.join(p for p in part.get('mountpoints') or [] if p) or 'Not mounted'}"]
        if not value['usb']:lines+=['No formatted USB partition detected.']
        lines+=['','U: mount existing USB partition. E: safely unmount a console-managed USB.','No format or database relocation is performed.','Active application / system mounts cannot be removed from this menu.']
    elif page==5:
        lines=['DOCKER APPLICATIONS (all running and stopped containers)']
        for row in value['inventory']['containers']:
            lines += [f"  {row['name']}  state={row['state']}  health={row['health']}",
                      f"    Compose: {row.get('project') or '-'} / {row.get('service') or '-'}",
                      f"    Image: {row['image']}  ID: {row['image_id']}"]
            for target,bindings in row['ports'].items():
                lines += [f"    {binding.get('HostIp') or '*'}:{binding['HostPort']} -> {target}" for binding in bindings or []]
        lines += ['','LOCAL IMAGES (including unused images)']
        for row in value['inventory']['images']:
            lines += [f"  {row['repository']}:{row['tag']}  {row['size']}",f"    {row['image_id']}  digest={row['digest']}"]
        lines += ['','not-configured means no Docker HEALTHCHECK; running alone does not prove health.',
                  'Native host applications appear under [6] with all systemd services.']
    elif page==6:
        lines=['HOST SERVICES (installed + loaded/transient; rediscovered every refresh)']
        for row in value['inventory']['services']:
            lines += [f"  {row['id']}  {row['state']}/{row['sub']}  {row['enabled']}  PID={row['pid']}",
                      f"    {row['description']}  load={row['load']}  result={row['result'] or '-'}"]
        lines += ['','Systemd active reports service state, not application HTTP readiness.',
                  'User-session services and applications outside systemd/Docker are not included.']
    elif page==7:
        lines=['RECENT INVENTORY CHANGES (monitor keeps latest 200 across restarts)']
        for row in value.get('changes',[]):
            lines += [f"  {row['at']}  {row['source']} {row['kind']}: {row['name']}",
                      '    '+', '.join(row['fields'])] if row['fields'] else [f"  {row['at']}  {row['source']} {row['kind']}: {row['name']}"]
        if not value.get('changes'):lines+=['No changes recorded yet. Polling does not record short-lived changes between samples.']
    errors=value['inventory']['errors']
    if errors:
        lines=['DISCOVERY UNAVAILABLE: '+', '.join(f'{key}: {error}' for key,error in errors.items()),
               'Missing data is unknown, not healthy or removed. Check local permissions/tools.','']+lines
    return lines

def admin_command(action,*args):
    script=str(pathlib.Path(__file__).resolve())
    if os.geteuid()!=0:return ['sudo','--','/usr/bin/python3',script,action,*args]
    # HDMI runs as root, but administrative changes require an existing user's
    # own sudo authentication. No fixed hostname or account is assumed.
    user=os.environ.get('TH_CONSOLE_ADMIN_USER','')
    if not user or user=='root':raise ValueError('Configure an administrator user before changing network or USB from HDMI')
    pwd.getpwnam(user)
    return ['runuser','-u',user,'--','sudo','--','/usr/bin/python3',script,action,*args]


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
            subprocess.run(admin_command(action,*args),check=False)
            input('\nPress Enter to return to TH-Server...')
        except (ValueError,KeyError) as error:
            print(str(error));input('Press Enter to return...')
        finally:curses.reset_prog_mode();screen.clear();monitor.refresh.set()
    try:
        while True:
            screen.erase();height,width=screen.getmaxyx();v=monitor.value
            write(0,2,'TH-SERVER  /  LOCAL OPERATIONS',curses.color_pair(1)|curses.A_BOLD)
            write(1,2,f"{socket.gethostname()}   {datetime.datetime.now().strftime('%d/%m/%Y %H:%M:%S')}   Refresh 5s")
            write(3,2,'[1] Overview [2] Net [3] Runners [4] USB [5] Apps [6] Services [7] Changes',curses.A_BOLD)
            lines=[part for line in lines_for(v,page) for part in (textwrap.wrap(line,max(8,width-4),subsequent_indent='    ') or [''])]
            offset=max(0,min(offset,max(0,len(lines)-max(1,height-8))))
            for y,line in enumerate(lines[offset:offset+max(1,height-8)],5):
                color=4 if 'UNREACHABLE' in line or 'failed' in line or 'unhealthy' in line or 'state=dead' in line else 3 if 'inactive' in line or 'OFFLINE' in line else 2 if 'HEALTHY' in line or 'ONLINE' in line or ' active' in line else 0
                write(y,2,line,curses.color_pair(color))
            stale=time.time()-v['at'] if v else 0
            footer='R Refresh | Q Quit | N Network | U Mount | E Eject | L Login tty2 | Arrows Scroll'
            if monitor.error or stale>30:footer=f"STATUS STALE ({monitor.error or str(int(stale))+'s'}). R to retry."
            write(height-2,2,footer,curses.color_pair(3) if monitor.error or stale>30 else curses.A_DIM)
            screen.refresh();key=screen.getch()
            if key in map(ord,'1234567'):page=int(chr(key));offset=0
            elif key in [ord('q'),ord('Q')]:break
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
                if not re.fullmatch(r'/dev/tty[0-9]+',os.ttyname(0)):continue
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
    if not os.isatty(0):raise SystemExit('Use an HDMI/SSH terminal (ssh -t) or --snapshot')
    if re.fullmatch(r'/dev/tty[0-9]+',os.ttyname(0)):os.write(1,b'\x1b[9;0]')
    curses.wrapper(console)
if __name__=='__main__':main()
