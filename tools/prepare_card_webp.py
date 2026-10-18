#!/usr/bin/env python3
"""Create lightweight animated WebP cards directly from the rendered MP4 montages."""
import argparse
import html
import json
import os
from pathlib import Path
import tempfile
from urllib.parse import quote

from prepare_group_gifs import ffmpeg, sha256, scaled_dimensions, write_json


def webp_info(path):
    data = path.read_bytes()
    if data[:4] != b'RIFF' or data[8:12] != b'WEBP' or len(data) != int.from_bytes(data[4:8], 'little') + 8:
        raise ValueError('Invalid WebP container')
    width = height = loop = None
    delays = []
    pos = 12
    while pos < len(data):
        name = data[pos:pos + 4]
        length = int.from_bytes(data[pos + 4:pos + 8], 'little')
        chunk = data[pos + 8:pos + 8 + length]
        if len(chunk) != length:
            raise ValueError('Truncated WebP chunk')
        if name == b'VP8X':
            width = int.from_bytes(chunk[4:7], 'little') + 1
            height = int.from_bytes(chunk[7:10], 'little') + 1
        elif name == b'ANIM':
            loop = int.from_bytes(chunk[4:6], 'little')
        elif name == b'ANMF':
            delays.append(int.from_bytes(chunk[12:15], 'little'))
        pos += 8 + length + (length % 2)
    if not width or not height or not delays or loop != 0:
        raise ValueError('Expected an infinitely looping animation')
    return {'width': width, 'height': height, 'frames': len(delays), 'duration_ms': sum(delays), 'delays_ms': sorted(set(delays)), 'loop': loop, 'bytes': len(data), 'decoded_pixels': width * height * len(delays)}


def prepare(record_path):
    record = json.loads(record_path.read_text())
    if record.get('status') != 'ok':
        raise ValueError('Montage render is not complete')
    source = Path(record['mp4']['path'])
    if sha256(source) != record['mp4']['sha256']:
        raise ValueError('Montage changed after rendering')
    target = source.with_suffix('.webp')
    sidecar = source.with_suffix('.webp.json')
    signature = {'version': 1, 'mp4_sha256': record['mp4']['sha256'], 'quality': 70, 'max_bytes': 6 * 1024 * 1024}
    if target.exists() and sidecar.exists():
        previous = json.loads(sidecar.read_text())
        if previous.get('settings') == signature and previous['sha256'] == sha256(target):
            webp_info(target)
            return previous
    frames = record['mp4']['frames']
    width, height = scaled_dimensions(record['mp4']['width'], record['mp4']['height'], 640, 80_000_000 / frames)
    with tempfile.TemporaryDirectory(dir=target.parent, prefix='.webp-') as work:
        pending = Path(work) / 'card.webp'
        for quality in [70, 60, 50]:
            ffmpeg('-i', str(source), '-vf', f'scale={width}:{height}:flags=lanczos,setsar=1',
                   '-an', '-c:v', 'libwebp_anim', '-quality', str(quality), '-compression_level', '4',
                   '-loop', '0', '-fps_mode', 'passthrough', str(pending))
            info = webp_info(pending)
            if abs(info['duration_ms'] - record['requested_duration_seconds'] * 1000) > 80:
                raise ValueError('WebP duration differs from requested excerpts')
            if info['frames'] > 600 or info['decoded_pixels'] > 80_000_000:
                raise ValueError('WebP exceeds server animation budget')
            if info['bytes'] <= signature['max_bytes']:
                break
        else:
            raise ValueError('Cannot encode WebP within 6 MiB; reduce montage resolution')
        result = {'number': record['number'], 'id': record['id'], 'path': str(target.resolve()), 'sha256': sha256(pending),
                  'settings': signature, 'quality': quality, 'nominal_fps': record['renderer']['clip_fps'],
                  'segments': record['segments'], **info}
        os.replace(pending, target)
        write_json(sidecar, result)
        return result


def write_gallery(directory, results):
    write_json(directory / 'webp-manifest.json', {'groups': results})
    cards=[]
    for item in results:
        stem=Path(item['path']).stem
        cards.append(f'<article><h2>{html.escape(item["number"])}</h2><img loading="lazy" src="{quote(stem)}.webp" alt="{html.escape(item["number"])}"><p>{item["duration_ms"]/1000:g} с · {item["nominal_fps"]:g} кадров/с · WebP {item["bytes"]/1048576:.2f} МБ</p><a href="{quote(stem)}.gif">Открыть GIF</a> · <a href="{quote(stem)}.mp4">Склеенное видео</a></article>')
    (directory/'index.html').write_text('<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Анимации групп</title><style>body{background:#111214;color:#eee;font:16px system-ui;margin:24px}main{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:20px}article{background:#202226;border-radius:16px;padding:18px}img{width:100%;aspect-ratio:16/9;object-fit:contain;background:#08090a}h2{font-size:20px;margin-top:0}a{color:#b7cef5}p{color:#aaa}</style><h1>Анимации групп</h1><main>'+''.join(cards)+'</main></html>')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('rendered_directory', type=Path)
    args = parser.parse_args()
    results = []
    for path in sorted(args.rendered_directory.glob('*--*.json')):
        if path.name.endswith('.webp.json'):
            continue
        result = prepare(path)
        results.append(result)
        print(json.dumps({k: result[k] for k in ['number', 'bytes', 'frames', 'duration_ms', 'width', 'height']}, ensure_ascii=False), flush=True)
    write_gallery(args.rendered_directory, results)


if __name__ == '__main__':
    main()
