#!/usr/bin/env python3
"""Polling inventory daemon, shared by SSH and HDMI; no network listener."""
import argparse
import json
import os
import pathlib
import signal
import threading
import time

import inventory
import telemetry

STATE_PATH = pathlib.Path('/var/lib/th-server-console/inventory.json')


def load(path=STATE_PATH):
    try:
        value = json.loads(path.read_text())
        discovered=value.get('inventory')
        if not isinstance(discovered, dict) or not isinstance(discovered.get('errors'), dict) or not isinstance(value.get('changes'), list):
            return None
        if not all(isinstance(discovered.get(source), list) for source in ('containers','images','services')):
            return None
        return value
    except (OSError, ValueError, AttributeError):
        return None


def sample(previous=None):
    current = inventory.collect()
    before = previous['inventory'] if previous else None
    known=set(previous.get('known_sources', [s for s in ('containers','images','services') if s not in before['errors']])) if previous else set()
    baseline={**before,'errors':{s:'No successful sample yet' for s in ('containers','images','services') if s not in known}} if before else None
    events = inventory.changes(baseline, current)
    known.update(s for s in ('containers','images','services') if s not in current['errors'])
    # Preserve last known rows on source outages, while explicitly marking them
    # stale. The diff never turns a discovery failure into mass removals.
    for source in current['errors']:
        if before:
            current[source] = before.get(source, [])
    return {'at': time.time(), 'inventory': current, 'known_sources':sorted(known),
            'changes': (events + (previous['changes'] if previous else []))[:200],
            'host': telemetry.sample(previous.get('host') if previous else None)}


def save(path, value):
    path.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    temporary = path.with_suffix('.new')
    # Do not follow a pre-existing symlink or expose a partially written document.
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o644)
    with os.fdopen(descriptor, 'w') as stream:
        json.dump(value, stream)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state', type=pathlib.Path, default=STATE_PATH)
    parser.add_argument('--interval', type=int, default=5)
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    if not 1 <= args.interval <= 300:
        parser.error('interval must be between 1 and 300 seconds')
    stop = threading.Event()
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, lambda *_: stop.set())
    previous = load(args.state)
    while not stop.is_set():
        started = time.monotonic()
        previous = sample(previous)
        save(args.state, previous)
        if args.once:
            return
        stop.wait(max(0, args.interval - (time.monotonic() - started)))


if __name__ == '__main__':
    main()
