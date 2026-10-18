#!/usr/bin/env python3
"""Atomically assign validated S3 animations, preserving all other event data."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
from urllib.parse import urlparse

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--database', type=Path, required=True)
parser.add_argument('--before-groups', type=Path, required=True)
parser.add_argument('--uploads', type=Path, required=True)
parser.add_argument('--report', type=Path, required=True)
parser.add_argument('--backup', type=Path)
parser.add_argument('--commit', action='store_true')
args = parser.parse_args()
if args.commit and not args.backup:
    parser.error('--commit requires a backup path')
before = json.loads(args.before_groups.read_text())['groups']
uploads = json.loads(args.uploads.read_text())['groups']
old = {g['number']: g for g in before}
new = {g['number']: g for g in uploads}
assert len(old) == len(before) and len(new) == len(uploads) and old.keys() == new.keys(), 'Group mapping mismatch'
for number, item in new.items():
    url = urlparse(item['url'])
    assert url.scheme == 'https' and url.netloc == 'storage.yandexcloud.net' and url.path.startswith('/posvyat2026/quiz/') and url.path.endswith('.webp') and not url.query and not url.fragment
    assert item['publicCheck']['status'] == 200 and item['publicCheck']['bytes'] == item['bytes']
conn = sqlite3.connect(args.database, timeout=30)
tables = [row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
assert all(name.replace('_', '').isalnum() for name in tables)
def snapshot(connection=conn):
    return {name: connection.execute(f'SELECT * FROM "{name}" ORDER BY rowid').fetchall() for name in tables}
def hashes(value):
    return {name: hashlib.sha256(json.dumps(rows, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest() for name, rows in value.items()}
conn.execute('BEGIN IMMEDIATE')
pending_report = None
try:
    start = snapshot()
    if args.backup:
        args.backup.parent.mkdir(parents=True, exist_ok=True)
        assert not args.backup.exists(), 'Backup already exists'
        # A separate reader can back up the committed state while this writer
        # holds the reservation. No other writer can slip between backup and import.
        with sqlite3.connect(args.database.resolve().as_uri() + '?mode=ro', uri=True) as reader:
            with sqlite3.connect(args.backup) as backup:
                reader.backup(backup)
                assert snapshot(backup) == start, 'Backup differs from import snapshot'
        args.backup.chmod(0o600)
    expected = {name: list(rows) for name, rows in start.items()}
    changed = []
    matched = set()
    for index, row in enumerate(start['records']):
        env, kind, record_id, raw = row
        if env != 'production' or kind != 'group':
            continue
        group = json.loads(raw)
        number = group['number']
        assert number in new and group['id'] == old[number]['id'], 'Production group mapping changed'
        matched.add(number)
        if group.get('mediaUrl', '') == new[number]['url']:
            continue
        assert group.get('mediaUrl', '') == old[number].get('mediaUrl', ''), f'Media edited since snapshot: {number}'
        group['mediaUrl'] = new[number]['url']
        updated = json.dumps(group, ensure_ascii=False, separators=(',', ':'))
        conn.execute('UPDATE records SET data=? WHERE environment=? AND kind=? AND id=?', (updated, env, kind, record_id))
        expected['records'][index] = (env, kind, record_id, updated)
        changed.append(number)
    assert matched == new.keys(), 'Missing production groups'
    for index, (env, raw) in enumerate(start['events']):
        if env == 'production':
            state = json.loads(raw)
            phase = state['phase']
            if changed:
                state['revision'] += 1
                updated = json.dumps(state, ensure_ascii=False, separators=(',', ':'))
                conn.execute('UPDATE events SET data=? WHERE environment=?', (updated, env))
                expected['events'][index] = (env, updated)
    end = snapshot()
    assert end == expected, 'Unexpected database changes; rolling back'
    report = {'committed': args.commit, 'changedGroups': sorted(changed), 'phase': phase, 'onlyChanges': ['production group.mediaUrl', 'production event.revision'], 'before': hashes(start), 'after': hashes(end)}
    with tempfile.NamedTemporaryFile(mode='w', dir=args.report.parent, prefix='.media-report-', delete=False) as pending:
        pending_report = Path(pending.name)
        pending.write(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
        pending.flush()
        os.fsync(pending.fileno())
    conn.execute('COMMIT' if args.commit else 'ROLLBACK')
    os.replace(pending_report, args.report)
    pending_report = None
    print(json.dumps({'committed': args.commit, 'changed': len(changed), 'phase': phase, 'allOtherDataPreserved': True}))
except BaseException:
    if conn.in_transaction:
        conn.execute('ROLLBACK')
    raise
finally:
    conn.close()
    if pending_report is not None:
        pending_report.unlink(missing_ok=True)
