#!/usr/bin/env python3
"""Resume a public Google Drive binary using independently checked HTTP ranges."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
import os
from pathlib import Path
import re
import time
import urllib.request


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('drive_id')
    p.add_argument('output', type=Path)
    p.add_argument('--jobs', type=int, choices=range(1, 9), default=6)
    args = p.parse_args()
    if not re.fullmatch(r'[a-zA-Z0-9_-]+', args.drive_id):
        raise ValueError('Invalid Drive id')
    url = 'https://drive.usercontent.google.com/download?id=' + args.drive_id + '&export=download&confirm=t'
    def fetch(start, end):
        for attempt in range(4):
            try:
                with urllib.request.urlopen(urllib.request.Request(url, headers={'Range': f'bytes={start}-{end}'}), timeout=90) as response:
                    if response.status != 206 or not response.headers.get('Content-Range', '').startswith(f'bytes {start}-{end}/'):
                        raise ValueError('Drive did not honor the byte range')
                    raw = response.read(end - start + 2)
                    if len(raw) != end - start + 1:
                        raise ValueError('Incomplete byte range')
                    return raw, response.headers['Content-Range']
            except Exception:
                if attempt == 3:
                    raise
                time.sleep(2 ** attempt)
    _, header = fetch(0, 0)
    length = int(header.split('/')[-1])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    pending = args.output.with_name(args.output.name + '.ranges')
    journal = args.output.with_name(args.output.name + '.ranges.json')
    chunk_size = 16 * 1024 * 1024
    record = {'driveId': args.drive_id, 'bytes': length, 'chunkSize': chunk_size, 'parts': {}}
    if journal.exists():
        previous = json.loads(journal.read_text())
        if all(previous[key] == record[key] for key in ['driveId', 'bytes', 'chunkSize']) and pending.exists():
            record = previous
    fd = os.open(pending, os.O_CREAT | os.O_RDWR, 0o600)
    os.ftruncate(fd, length)
    def worker(start):
        end = min(start + chunk_size, length) - 1
        old = record['parts'].get(str(start))
        if old and hashlib.sha256(os.pread(fd, end - start + 1, start)).hexdigest() == old:
            return start, old
        raw, _ = fetch(start, end)
        count = os.pwrite(fd, raw, start)
        if count != len(raw):
            raise IOError('Short write')
        return start, hashlib.sha256(raw).hexdigest()
    try:
        offsets = list(range(0, length, chunk_size))
        with ThreadPoolExecutor(max_workers=args.jobs) as pool:
            futures = [pool.submit(worker, start) for start in offsets]
            for i, future in enumerate(as_completed(futures), 1):
                start, digest = future.result()
                record['parts'][str(start)] = digest
                tmp = journal.with_suffix('.tmp')
                tmp.write_text(json.dumps(record))
                os.replace(tmp, journal)
                if i % 10 == 0 or i == len(offsets):
                    print(json.dumps({'completedParts': i, 'totalParts': len(offsets)}), flush=True)
        os.fsync(fd)
    finally:
        os.close(fd)
    with pending.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    os.replace(pending, args.output)
    print(json.dumps({'output': str(args.output), 'bytes': length, 'sha256': digest}), flush=True)


if __name__ == '__main__':
    main()
