import { readFileSync, writeFileSync, renameSync, existsSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { createHash } from 'node:crypto';
import { pathToFileURL } from 'node:url';
import { createRequire } from 'node:module';

const [manifestPath, reportPath] = process.argv.slice(2);
if (!manifestPath || !reportPath) throw new Error('Usage: upload-batch.mjs manifest.json report.json');
const app = process.env.QUIZ_APP_DIR || '/opt/initiation-quiz';
const sharp = createRequire(resolve(app, 'package.json'))('sharp');
const { storageSettings, uploadToStorage } = await import(pathToFileURL(resolve(app, 'server/storage.mjs')));
const { db } = await import(pathToFileURL(resolve(app, 'server/store.mjs')));
const manifest = JSON.parse(readFileSync(manifestPath));
const config = storageSettings();
if (!config.enabled || config.bucket !== 'posvyat2026') throw new Error('Expected enabled event storage');
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
const report = existsSync(reportPath) ? JSON.parse(readFileSync(reportPath)) : { bucket: config.bucket, groups: [] };
if (report.bucket !== config.bucket) throw new Error('Storage changed');
if (new Set(manifest.groups.map(g => g.number)).size !== manifest.groups.length) throw new Error('Duplicate groups');
const publicBase = `https://storage.yandexcloud.net/${config.bucket}/quiz/`;
const save = () => {
  writeFileSync(`${reportPath}.tmp`, JSON.stringify(report, null, 2) + '\n', { mode: 0o600 });
  renameSync(`${reportPath}.tmp`, reportPath);
};
async function verify(url, expectedHash) {
  if (!url.startsWith(publicBase)) throw new Error('Unexpected public URL');
  const response = await fetch(url, { signal: AbortSignal.timeout(30000), redirect: 'error' });
  if (!response.ok || response.headers.get('content-type') !== 'image/webp') throw new Error('Public image unavailable');
  if (!response.headers.get('cache-control')?.includes('immutable')) throw new Error('Image cache headers missing');
  const bytes = Buffer.from(await response.arrayBuffer());
  if (hash(bytes) !== expectedHash) throw new Error('Public bytes differ');
  return { status: response.status, contentType: response.headers.get('content-type'), cacheControl: response.headers.get('cache-control'), bytes: bytes.length };
}
try {
  // Validate the complete batch before the first external write.
  for (const item of manifest.groups) {
    const bytes = readFileSync(resolve(dirname(manifestPath), item.path));
    if (hash(bytes) !== item.sha256 || bytes.length !== item.bytes || bytes.length > 6 * 1024 * 1024) throw new Error(`Invalid file: ${item.number}`);
    const meta = await sharp(bytes, { animated: true, limitInputPixels: 80000000 }).metadata();
    const width = meta.width, height = meta.pageHeight || meta.height, frames = meta.pages || 1;
    if (meta.format !== 'webp' || frames < 2 || frames > 600 || width * height * frames > 80000000 || meta.loop !== 0) throw new Error(`Invalid animation: ${item.number}`);
    if (width !== item.width || height !== item.height || frames !== item.frames || Math.abs(meta.delay.reduce((a,b)=>a+b,0) - item.duration_ms) > 1) throw new Error(`Metadata differs: ${item.number}`);
  }
  for (const item of manifest.groups) {
    let saved = report.groups.find(g => g.number === item.number && g.sha256 === item.sha256);
    if (!saved) {
      const bytes = readFileSync(resolve(dirname(manifestPath), item.path));
      if (hash(bytes) !== item.sha256) throw new Error(`File changed during upload: ${item.number}`);
      const { url } = await uploadToStorage(bytes, { contentType: 'image/webp' }, config);
      saved = { ...item, url };
      report.groups = report.groups.filter(g => g.number !== item.number);
      report.groups.push(saved);
      save();
    }
    saved.publicCheck = await verify(saved.url, saved.sha256);
    save();
    console.log(JSON.stringify({ number: saved.number, bytes: saved.bytes, publicRead: 'ok' }));
  }
} finally { db.close(); }
