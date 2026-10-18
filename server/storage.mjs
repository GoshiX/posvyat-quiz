import { randomUUID, createHash } from 'node:crypto';
import { S3Client, PutObjectCommand, DeleteObjectCommand } from '@aws-sdk/client-s3';
import { settings, saveSettings, encrypt, decrypt } from './store.mjs';
import { AppError, assert, text } from './domain.mjs';

const ENDPOINT = 'https://storage.yandexcloud.net';
const REGION = 'ru-central1';
const DEFAULT_BUCKET = 'posvyat2026';
const CACHE_CONTROL = 'public, max-age=31536000, immutable';
const TIMEOUT = 15_000;
const DEFAULTS = { enabled: false, bucket: DEFAULT_BUCKET, accessKeyId: '', secret: '', lastTest: null };
const IMAGE_EXTENSIONS = { 'image/png': 'png', 'image/jpeg': 'jpg', 'image/gif': 'gif', 'image/webp': 'webp' };
// A complete 1x1 transparent PNG. A successful check must read these exact bytes anonymously.
const PROBE = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=', 'base64');

export function storageSettings() {
  const stored = settings().storage;
  return { ...DEFAULTS, ...(stored && typeof stored === 'object' && !Array.isArray(stored) ? stored : {}) };
}

function bucketName(value) {
  const bucket = text(value, 63) || DEFAULT_BUCKET;
  assert(bucket.length >= 3 && /^[a-z0-9][a-z0-9.-]*[a-z0-9]$/.test(bucket) && !bucket.includes('..') && !/^\d+\.\d+\.\d+\.\d+$/.test(bucket), 'Некорректное имя бакета Object Storage');
  return bucket;
}

function publicBaseUrl(config) {
  return `${ENDPOINT}/${bucketName(config.bucket)}`;
}

export function publicStorage() {
  const config = storageSettings();
  return {
    enabled: config.enabled === true,
    bucket: config.bucket,
    endpoint: ENDPOINT,
    region: REGION,
    publicBaseUrl: publicBaseUrl(config),
    accessKeyIdSet: !!config.accessKeyId,
    secretSet: !!config.secret,
    lastTest: config.lastTest ? { ok: config.lastTest.ok === true, message: config.lastTest.message, at: config.lastTest.at } : null,
  };
}

function fingerprint(config) {
  return createHash('sha256').update(JSON.stringify([config.bucket, config.accessKeyId, config.secret])).digest('hex');
}

export function saveStorageSettings(body) {
  assert(body && typeof body === 'object' && !Array.isArray(body), 'Некорректные настройки Object Storage');
  for (const field of ['endpoint', 'region', 'publicBaseUrl']) assert(!Object.hasOwn(body, field), 'Адрес Object Storage задаётся сервером');
  const before = storageSettings();
  const config = { ...before };
  if (body.enabled !== undefined) {
    assert(typeof body.enabled === 'boolean', 'Некорректное значение Object Storage');
    config.enabled = body.enabled;
  }
  if (body.bucket !== undefined) config.bucket = bucketName(body.bucket);
  for (const [input, stored, max] of [['accessKeyId', 'accessKeyId', 300], ['secretAccessKey', 'secret', 1000]]) {
    if (body[input] === undefined) continue;
    const value = text(body[input], max);
    // Empty password fields mean "keep the existing key".
    if (value) {
      let previous = '';
      try { previous = decrypt(config[stored]); } catch { /* Replace an unreadable old key. */ }
      if (value !== previous) config[stored] = encrypt(value);
    }
  }
  if (config.enabled) credentials(config);
  if (fingerprint(before) !== fingerprint(config)) config.lastTest = null;
  const latest = settings();
  saveSettings({ ...latest, storage: config });
  return publicStorage();
}

function credentials(config) {
  assert(config.accessKeyId && config.secret, 'Укажите Access Key ID и Secret Access Key для Object Storage');
  try {
    const accessKeyId = decrypt(config.accessKeyId), secretAccessKey = decrypt(config.secret);
    assert(accessKeyId && secretAccessKey, 'Укажите Access Key ID и Secret Access Key для Object Storage');
    return { accessKeyId, secretAccessKey };
  } catch {
    throw new AppError('Не удалось прочитать ключи Object Storage. Введите их заново');
  }
}

function transportEndpoint() {
  if (!process.env.S3_TEST_ENDPOINT || process.env.NODE_ENV === 'production') return ENDPOINT;
  let url;
  try { url = new URL(process.env.S3_TEST_ENDPOINT); } catch { throw new AppError('Некорректный тестовый адрес Object Storage'); }
  assert(['http:', 'https:'].includes(url.protocol) && ['localhost', '127.0.0.1', '[::1]'].includes(url.hostname) && !url.username && !url.password && !url.search && !url.hash && url.pathname === '/', 'Тестовый Object Storage должен находиться на localhost');
  return url.origin;
}

function createClient(config) {
  bucketName(config.bucket);
  return new S3Client({
    endpoint: transportEndpoint(),
    region: REGION,
    credentials: credentials(config),
    forcePathStyle: true,
    maxAttempts: 2,
    requestHandler: { connectionTimeout: 5_000, requestTimeout: TIMEOUT },
    requestChecksumCalculation: 'WHEN_REQUIRED',
    responseChecksumValidation: 'WHEN_REQUIRED',
  });
}

function put(client, config, key, bytes, contentType) {
  return client.send(new PutObjectCommand({ Bucket: config.bucket, Key: key, Body: bytes, ContentType: contentType, CacheControl: CACHE_CONTROL }), { abortSignal: AbortSignal.timeout(TIMEOUT) });
}

/** Upload validated image bytes; config, when supplied, uses storageSettings()'s encrypted keys. */
export async function uploadToStorage(bytes, { name, contentType } = {}, config = storageSettings()) {
  assert(config.enabled, 'Object Storage выключен');
  const extension = IMAGE_EXTENSIONS[contentType];
  assert(extension, 'Поддерживаются PNG, JPEG, GIF и WebP');
  assert(bytes instanceof Uint8Array && bytes.length && bytes.length <= 10 * 1024 * 1024, 'Размер файла должен быть до 10 МБ');
  const key = `quiz/${randomUUID()}.${extension}`;
  const client = createClient(config);
  try {
    await put(client, config, key, bytes, contentType);
    return { url: `${publicBaseUrl(config)}/${key}` };
  } catch {
    throw new AppError('Не удалось записать файл в Object Storage. Проверьте ключи и право записи в бакет', 502);
  } finally {
    client.destroy();
  }
}

async function readProbe(config, key) {
  const url = `${transportEndpoint()}/${config.bucket}/${key}`;
  const response = await fetch(url, { signal: AbortSignal.timeout(TIMEOUT), redirect: 'error', cache: 'no-store' });
  if (!response.ok) {
    await response.body?.cancel();
    throw new Error('Anonymous read failed');
  }
  let length = 0;
  const chunks = [];
  for await (const chunk of response.body) {
    length += chunk.length;
    if (length > PROBE.length) throw new Error('Unexpected probe bytes');
    chunks.push(chunk);
  }
  if (!Buffer.concat(chunks).equals(PROBE)) throw new Error('Unexpected probe bytes');
}

export async function testStorageConnection() {
  const config = storageSettings(), original = fingerprint(config);
  const key = `quiz/check-${randomUUID()}.png`;
  let client, attempted = false, uploaded = false;
  let result = { ok: true, message: 'Запись и публичное чтение Object Storage работают' };
  try {
    client = createClient(config);
    attempted = true;
    await put(client, config, key, PROBE, 'image/png');
    uploaded = true;
    await readProbe(config, key);
  } catch (error) {
    result = { ok: false, message: uploaded ? 'Файл записан, но публичное чтение недоступно. Разрешите анонимное чтение объектов в бакете' : error instanceof AppError ? error.message : 'Не удалось записать проверочный файл. Проверьте ключи, имя бакета и право записи в Object Storage' };
  } finally {
    if (client && attempted) {
      try {
        await client.send(new DeleteObjectCommand({ Bucket: config.bucket, Key: key }), { abortSignal: AbortSignal.timeout(TIMEOUT) });
      } catch {
        result = { ok: false, message: `${result.ok ? 'Запись и публичное чтение работают. ' : `${result.message}. `}Не удалось удалить проверочный файл. Проверьте право удаления объектов в бакете` };
      }
    }
    client?.destroy();
  }
  // An administrator may edit credentials/OAuth while the network check is running.
  const latest = settings();
  if (fingerprint({ ...DEFAULTS, ...latest.storage }) === original) {
    saveSettings({ ...latest, storage: { ...DEFAULTS, ...latest.storage, lastTest: { ...result, at: new Date().toISOString() } } });
  }
  return publicStorage();
}
