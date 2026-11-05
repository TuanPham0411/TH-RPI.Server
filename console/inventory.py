"""Read-only local Docker/systemd discovery. Never inspect container environment."""
import datetime
import json
import subprocess


class Unavailable(RuntimeError):
    pass


def run(args):
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise Unavailable(type(error).__name__) from error
    if result.returncode:
        # stderr can contain host configuration; report only the command and exit code.
        raise Unavailable(f'{args[0]} exited {result.returncode}')
    return result.stdout.strip()


def json_lines(text):
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def containers():
    ids = run(['docker', 'ps', '-aq', '--no-trunc']).split()
    rows = []
    # The template deliberately excludes Config.Env, commands and arbitrary labels.
    template = ('{"id":{{json .Id}},"name":{{json .Name}},'
                '"image":{{json .Config.Image}},"image_id":{{json .Image}},'
                '"state":{{json .State.Status}},"health":{{with index .State "Health"}}{{json .Status}}{{else}}"not-configured"{{end}},'
                '"project":{{with .Config.Labels}}{{json (index . "com.docker.compose.project")}}{{else}}null{{end}},'
                '"service":{{with .Config.Labels}}{{json (index . "com.docker.compose.service")}}{{else}}null{{end}},'
                '"ports":{{json .NetworkSettings.Ports}}}')
    for offset in range(0, len(ids), 50):
        # A container may disappear between ps and inspect. Treat this sample as
        # unavailable rather than incorrectly reporting every container removed.
        rows.extend(json_lines(run(['docker', 'inspect', '--type=container', '--format', template, *ids[offset:offset + 50]])))
    for row in rows:
        row['name'] = row['name'].lstrip('/')
        row['ports'] = row.get('ports') or {}
    return sorted(rows, key=lambda row: row['id'])


def images():
    rows = json_lines(run(['docker', 'image', 'ls', '--no-trunc', '--digests', '--format', '{{json .}}']))
    found = {}
    for row in rows:
        key = '|'.join(row.get(field, '') for field in ('ID', 'Repository', 'Tag', 'Digest'))
        found[key] = {'id': key, 'image_id': row['ID'], 'repository': row['Repository'],
                      'tag': row['Tag'], 'digest': row['Digest'], 'size': row['Size']}
    return sorted(found.values(), key=lambda row: row['id'])


def services():
    # Include disabled, masked and inactive installed units as well as transient
    # loaded units. No application names or unit allowlist are needed.
    installed = run(['systemctl', 'list-unit-files', '--type=service', '--no-legend', '--no-pager', '--plain'])
    loaded = run(['systemctl', 'list-units', '--all', '--type=service', '--no-legend', '--no-pager', '--plain'])
    installed_states={}
    for line in installed.splitlines():
        cells=line.split()
        if len(cells)>=2:installed_states[cells[0]]=cells[1]
    names = set()
    for line in (installed + '\n' + loaded).splitlines():
        cells = line.lstrip('● ').split()
        if cells and cells[0].endswith('.service'):
            names.add(cells[0])
    rows = [{'id':name,'description':'Service template (instances shown separately)',
             'load':'template','state':'not-instantiated','sub':'-',
             'enabled':installed_states.get(name,''),'pid':0,'result':''}
            for name in sorted(names) if name.endswith('@.service')]
    names={name for name in names if not name.endswith('@.service')}
    properties = 'Id,Description,LoadState,ActiveState,SubState,UnitFileState,MainPID,Result'
    ordered = sorted(names)
    for offset in range(0, len(ordered), 100):
        text = run(['systemctl', 'show', '--no-pager', '--property=' + properties, '--', *ordered[offset:offset + 100]])
        for block in text.split('\n\n'):
            fields = dict(line.split('=', 1) for line in block.splitlines() if '=' in line)
            if fields.get('Id'):
                rows.append({'id': fields['Id'], 'description': fields.get('Description', ''),
                             'load': fields.get('LoadState', 'unknown'), 'state': fields.get('ActiveState', 'unknown'),
                             'sub': fields.get('SubState', 'unknown'), 'enabled': fields.get('UnitFileState', ''),
                             'pid': int(fields.get('MainPID') or 0), 'result': fields.get('Result', '')})
    return sorted({row['id']:row for row in rows}.values(), key=lambda row: row['id'])


def collect():
    result = {'errors': {}}
    for name, reader in [('containers', containers), ('images', images), ('services', services)]:
        try:
            result[name] = reader()
        except (Unavailable, ValueError, KeyError, TypeError) as error:
            result[name] = []
            result['errors'][name] = str(error) if isinstance(error, Unavailable) else 'Invalid discovery response'
    return result


def changes(previous, current):
    """Diff stable fields only. Failed samples never establish removals."""
    events = []
    if previous is None:
        return events
    for source in ('containers', 'images', 'services'):
        if source in current['errors'] or source in previous['errors']:
            continue
        before = {row['id']: row for row in previous[source]}
        after = {row['id']: row for row in current[source]}
        for key in sorted(before.keys() | after.keys()):
            old, new = before.get(key), after.get(key)
            kind = 'added' if old is None else 'removed' if new is None else 'updated'
            if old == new:
                continue
            row = new or old
            fields = sorted(k for k in set(old or {}) | set(new or {}) if (old or {}).get(k) != (new or {}).get(k))
            events.append({'at': datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds'),
                           'source': source, 'kind': kind, 'name': row.get('name', row.get('repository', key)),
                           'fields': fields if kind == 'updated' else []})
    return events
