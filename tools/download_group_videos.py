#!/usr/bin/env python3
"""Download explicitly mapped public Drive videos; never infer a group from a partial name."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
from pathlib import Path
import subprocess
import time

import gdown


def probe(path):
    result = subprocess.run(['ffprobe', '-v', 'error', '-show_format', '-show_streams', '-of', 'json', str(path)], capture_output=True, text=True, check=True, timeout=90)
    data = json.loads(result.stdout)
    videos = [stream for stream in data['streams'] if stream['codec_type'] == 'video']
    if not videos or float(data['format']['duration']) <= 0:
        raise ValueError('Downloaded file has no usable video stream')
    return data


def download(entry, manifest_dir):
    path = Path(entry['input'])
    if not path.is_absolute():
        path = manifest_dir / path
    path.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    if not path.exists():
        pending = path.with_name(path.name + '.download')
        for attempt in range(4):
            try:
                result = gdown.download(id=entry['driveId'], output=str(pending), quiet=True, use_cookies=False, resume=True)
                if not result:
                    raise RuntimeError('Google Drive did not return a downloadable file')
                break
            except Exception:
                if attempt == 3:
                    raise
                time.sleep(2 ** attempt)
        metadata = probe(pending)
        pending.replace(path)
    else:
        metadata = probe(path)
    with path.open('rb') as source:
        digest = hashlib.file_digest(source, 'sha256').hexdigest()
    video = next(stream for stream in metadata['streams'] if stream['codec_type'] == 'video')
    return {'number': entry['number'], 'driveId': entry['driveId'], 'input': str(path.resolve()), 'status': 'downloaded', 'bytes': path.stat().st_size, 'sha256': digest, 'seconds': round(time.monotonic() - started, 2), 'duration': float(metadata['format']['duration']), 'width': video['width'], 'height': video['height'], 'codec': video['codec_name']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('manifest', type=Path)
    parser.add_argument('--jobs', type=int, choices=range(1, 5), default=3)
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text())
    entries = manifest['groups']
    assert len({entry['number'] for entry in entries}) == len(entries), 'Duplicate group numbers'
    output = args.manifest.parent / 'downloads.json'
    results = {}
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        pending = {pool.submit(download, entry, args.manifest.resolve().parent): entry for entry in entries}
        for future in as_completed(pending):
            entry = pending[future]
            try:
                result = future.result()
            except Exception as error:
                result = {'number': entry['number'], 'status': 'error', 'error': str(error)}
            results[entry['number']] = result
            output.write_text(json.dumps({'groups': [results.get(e['number'], {'number': e['number'], 'status': 'pending'}) for e in entries]}, ensure_ascii=False, indent=2) + '\n')
            print(json.dumps(result, ensure_ascii=False), flush=True)
    return 1 if any(result['status'] != 'downloaded' for result in results.values()) else 0


if __name__ == '__main__':
    raise SystemExit(main())
