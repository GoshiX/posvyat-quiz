import test, { after } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import sharp from 'sharp';

const dataDir = mkdtempSync(join(tmpdir(), 'quiz-media-test-'));
process.env.DATA_DIR = dataDir;
const { prepareImage, MEDIA_LIMITS } = await import('./media.mjs');
const { AppError } = await import('./domain.mjs');
after(() => rmSync(dataDir, { recursive: true, force: true }));

const tinyGif = Buffer.from('R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7', 'base64');
const invalid = error => error instanceof AppError && error.status === 400;

async function animation({ width = 24, height = 16, frames = 3, delay = [80, 160, 240], loop = 3, format = 'gif' } = {}) {
  const raw = Buffer.alloc(width * height * frames * 4);
  for (let frame = 0; frame < frames; frame++) {
    for (let pixel = frame * width * height * 4; pixel < (frame + 1) * width * height * 4; pixel += 4) {
      raw[pixel + frame % 3] = 255;
      raw[pixel + 3] = 255;
    }
  }
  const pipeline = sharp(raw, { raw: { width, height: height * frames, channels: 4, pageHeight: height } });
  return format === 'gif'
    ? pipeline.gif({ loop, delay, keepDuplicateFrames: true }).toBuffer()
    : pipeline.webp({ loop, delay, lossless: true }).toBuffer();
}

test('photos are resized without enlarging and produce a bounded WebP', async () => {
  const photo = await sharp({ create: { width: 2400, height: 1600, channels: 3, noise: { type: 'gaussian' } } }).jpeg({ quality: 96 }).toBuffer();
  const result = await prepareImage(photo);
  const metadata = await sharp(result.bytes).metadata();
  assert.equal(result.extension, 'webp');
  assert.equal(result.contentType, 'image/webp');
  assert.deepEqual([metadata.width, metadata.height], [960, 640]);
  assert.equal(result.pages, 1);
  assert.ok(result.bytes.length < photo.length / 2);
  assert.ok(result.bytes.length <= MEDIA_LIMITS.staticBytes);

  const small = await sharp({ create: { width: 32, height: 24, channels: 3, background: '#a44' } }).png().toBuffer();
  const smallResult = await prepareImage(new Uint8Array(small));
  assert.deepEqual([smallResult.width, smallResult.height], [32, 24]);
});

test('EXIF orientation is applied before sizing and private metadata is removed', async () => {
  const input = await sharp({ create: { width: 1200, height: 600, channels: 3, background: '#795' } })
    .withMetadata({ orientation: 6 })
    .withExifMerge({ IFD0: { Artist: 'Private uploader', Copyright: 'private location' } })
    .jpeg().toBuffer();
  assert.equal((await sharp(input).metadata()).orientation, 6);
  const result = await prepareImage(input);
  const metadata = await sharp(result.bytes).metadata();
  assert.deepEqual([result.width, result.height], [480, 960]);
  assert.equal(metadata.orientation, undefined);
  for (const field of ['exif', 'xmp', 'iptc', 'icc']) assert.equal(metadata[field], undefined);
  assert.equal(metadata.hasProfile, false);
  assert.equal(result.bytes.includes(Buffer.from('Private uploader')), false);
});

test('transparent PNG keeps its alpha channel for card, logo and icon', async () => {
  const input = await sharp({ create: { width: 1200, height: 600, channels: 4, background: { r: 12, g: 180, b: 30, alpha: 0.5 } } }).png().toBuffer();
  for (const [purpose, expected] of [['card', [960, 480]], ['logo', [960, 480]], ['icon', [384, 192]]]) {
    const result = await prepareImage(input, { purpose });
    const { data, info } = await sharp(result.bytes).raw().toBuffer({ resolveWithObject: true });
    assert.deepEqual([info.width, info.height], expected);
    assert.equal(info.channels, 4);
    assert.ok(data[3] >= 126 && data[3] <= 129);
  }
});

test('GIF frames retain their colours, delays and loop, each within the card size', async () => {
  const input = await animation({ width: 1200, height: 600 });
  const result = await prepareImage(input);
  const metadata = await sharp(result.bytes, { animated: true }).metadata();
  assert.equal(result.extension, 'webp');
  assert.equal(result.animated, true);
  assert.equal(result.pages, 3);
  assert.deepEqual([result.width, result.height], [960, 480]);
  assert.equal(metadata.pages, 3);
  assert.equal(metadata.pageHeight, 480);
  assert.deepEqual(metadata.delay, [80, 160, 240]);
  assert.equal(metadata.loop, 3);
  assert.ok(result.bytes.length <= MEDIA_LIMITS.animatedBytes);
  const { data, info } = await sharp(result.bytes, { animated: true }).removeAlpha().raw().toBuffer({ resolveWithObject: true });
  for (let frame = 0; frame < 3; frame++) {
    const offset = frame * info.width * metadata.pageHeight * 3;
    assert.ok(data[offset + frame] > 200);
    assert.ok(data[offset + (frame + 1) % 3] < 40);
  }
});

test('a 15-second 30 fps GIF retains all 450 frames, their timing and loop', async () => {
  // GIF delays use centiseconds: this cadence averages exactly 30 fps.
  const frames = 450;
  const delay = Array.from({ length: frames }, (_, frame) => frame % 3 === 2 ? 40 : 30);
  const input = await animation({ width: 96, height: 54, frames, delay, loop: 2 });
  const result = await prepareImage(input);
  const metadata = await sharp(result.bytes, { animated: true }).metadata();
  assert.equal(result.extension, 'webp');
  assert.equal(result.animated, true);
  assert.equal(result.pages, frames);
  assert.equal(metadata.pages, frames);
  assert.deepEqual([result.width, result.height], [96, 54]);
  assert.deepEqual(metadata.delay, delay);
  assert.equal(metadata.delay.reduce((total, duration) => total + duration, 0), 15_000);
  assert.equal(metadata.loop, 2);
  assert.ok(result.bytes.length <= MEDIA_LIMITS.animatedBytes);

  const { data, info } = await sharp(result.bytes, { animated: true }).removeAlpha().raw().toBuffer({ resolveWithObject: true });
  for (let frame = 0; frame < frames; frame++) {
    const offset = frame * info.width * metadata.pageHeight * 3;
    assert.ok(data[offset + frame % 3] > 200);
    assert.ok(data[offset + (frame + 1) % 3] < 40);
  }
});

test('animated WebP stays animated, while icons select just the first frame', async () => {
  const input = await animation({ width: 800, height: 400, format: 'webp' });
  const card = await prepareImage(input);
  const metadata = await sharp(card.bytes, { animated: true }).metadata();
  assert.equal(metadata.pages, 3);
  assert.deepEqual(metadata.delay, [80, 160, 240]);
  assert.equal(metadata.loop, 3);

  const icon = await prepareImage(input, { purpose: 'icon' });
  const { data, info } = await sharp(icon.bytes).removeAlpha().raw().toBuffer({ resolveWithObject: true });
  assert.equal(icon.animated, false);
  assert.equal(icon.pages, 1);
  assert.deepEqual([info.width, info.height], [384, 192]);
  assert.ok(data[0] > 200 && data[1] < 40 && data[2] < 40);
});

test('a validated tiny GIF remains byte-for-byte compatible, but its comments are stripped', async () => {
  const result = await prepareImage(tinyGif);
  assert.equal(result.extension, 'gif');
  assert.equal(result.contentType, 'image/gif');
  assert.deepEqual(result.bytes, tinyGif);
  const comment = Buffer.from('private note');
  const commented = Buffer.concat([tinyGif.subarray(0, -1), Buffer.from([0x21, 0xfe, comment.length]), comment, Buffer.from([0, 0x3b])]);
  const cleaned = await prepareImage(commented);
  assert.equal(cleaned.extension, 'webp');
  assert.equal(cleaned.bytes.includes(comment), false);
  const oversizedScreen = Buffer.from(tinyGif);
  oversizedScreen.writeUInt16LE(9000, 6);
  oversizedScreen.writeUInt16LE(9000, 8);
  const cropped = await prepareImage(oversizedScreen);
  assert.equal(cropped.extension, 'webp');
  assert.deepEqual([cropped.width, cropped.height], [1, 1]);
});

test('unsupported content and truncated pixel data fail as user errors', async () => {
  for (const input of [Buffer.from('<svg xmlns="http://www.w3.org/2000/svg" width="1" height="1"/>'), Buffer.from('GIF89a'), Buffer.from([0xff, 0xd8, 0xff]), Buffer.alloc(20)]) {
    await assert.rejects(prepareImage(input), invalid);
  }
  const jpeg = await sharp({ create: { width: 256, height: 128, channels: 3, noise: { type: 'gaussian' } } }).jpeg().toBuffer();
  const truncated = jpeg.subarray(0, Math.floor(jpeg.length / 2));
  assert.equal((await sharp(truncated).metadata()).format, 'jpeg');
  await assert.rejects(prepareImage(truncated), invalid);
  await assert.rejects(prepareImage(tinyGif, { purpose: 'unknown' }), invalid);
  // Validation failures must release the active-processing slot.
  assert.equal((await prepareImage(tinyGif)).contentType, 'image/gif');
});

test('byte, pixel, frame and duration limits reject compressed resource bombs', async () => {
  await assert.rejects(prepareImage(Buffer.alloc(MEDIA_LIMITS.inputBytes + 1)), invalid);
  const hugeCanvas = Buffer.from(tinyGif);
  hugeCanvas.writeUInt16LE(9000, 6);
  hugeCanvas.writeUInt16LE(9000, 8);
  const imageOffset = hugeCanvas.indexOf(0x2c);
  hugeCanvas.writeUInt16LE(9000, imageOffset + 5);
  hugeCanvas.writeUInt16LE(9000, imageOffset + 7);
  await assert.rejects(prepareImage(hugeCanvas), error => invalid(error) && /разрешение/.test(error.message));

  // The raised frame limit must still count the pixels of every frame.
  const manyPixels = await animation({ width: 1, height: 1, frames: 450, delay: 30 });
  manyPixels.writeUInt16LE(500, 6);
  manyPixels.writeUInt16LE(400, 8);
  const frameOffset = manyPixels.indexOf(0x2c);
  manyPixels.writeUInt16LE(500, frameOffset + 5);
  manyPixels.writeUInt16LE(400, frameOffset + 7);
  await assert.rejects(prepareImage(manyPixels), error => invalid(error) && /разрешение/.test(error.message));

  const manyFrames = await animation({ width: 1, height: 1, frames: MEDIA_LIMITS.frames + 1, delay: 10 });
  await assert.rejects(prepareImage(manyFrames), error => invalid(error) && /кадров/.test(error.message));

  const long = await animation({ frames: 2, delay: [61_000, 61_000] });
  await assert.rejects(prepareImage(long), error => invalid(error) && /2 минут/.test(error.message));
});

test('concurrent processing is bounded and releases the slot afterwards', async () => {
  const first = prepareImage(tinyGif);
  await assert.rejects(prepareImage(tinyGif), error => error instanceof AppError && error.status === 429);
  await first;
  assert.equal((await prepareImage(tinyGif)).contentType, 'image/gif');
});
