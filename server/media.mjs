import sharp from 'sharp';
import { AppError } from './domain.mjs';

export const MEDIA_LIMITS = Object.freeze({
  inputBytes: 10 * 1024 * 1024,
  inputPixels: 80_000_000,
  frames: 600,
  durationMs: 120_000,
  staticBytes: 2 * 1024 * 1024,
  animatedBytes: 6 * 1024 * 1024,
  processingSeconds: 30,
});

const purposes = Object.freeze({ card: 960, icon: 384, logo: 960 });
let processing = false;

// Keep the single upload job from competing with request handling for CPU and
// retaining a large decoded animation in libvips' operation cache.
sharp.concurrency(1);
sharp.cache({ memory: 8, files: 0, items: 20 });

function inputFormat(bytes) {
  if (bytes.subarray(0, 8).equals(Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]))) return 'png';
  if (bytes[0] === 0xff && bytes[1] === 0xd8 && bytes[2] === 0xff) return 'jpeg';
  if (['GIF87a', 'GIF89a'].includes(bytes.subarray(0, 6).toString('ascii'))) return 'gif';
  if (bytes.subarray(0, 4).toString('ascii') === 'RIFF' && bytes.subarray(8, 12).toString('ascii') === 'WEBP') return 'webp';
  throw new AppError('Поддерживаются PNG, JPEG, GIF и WebP');
}

// A tiny GIF may already be smaller than WebP. Only retain image/control blocks
// and the standard loop extension: comments, profiles and trailing data are not
// carried through. The decoder still validates every frame before this is used.
function plainTinyGif(bytes) {
  if (bytes.length > 1024 || bytes.length < 14) return false;
  let offset = 13;
  if (bytes[10] & 0x80) offset += 3 * (2 ** ((bytes[10] & 7) + 1));
  const skipBlocks = () => {
    while (offset < bytes.length) {
      const length = bytes[offset++];
      if (!length) return true;
      offset += length;
    }
    return false;
  };
  while (offset < bytes.length) {
    const marker = bytes[offset++];
    if (marker === 0x3b) return offset === bytes.length;
    if (marker === 0x2c) {
      if (offset + 9 > bytes.length) return false;
      const flags = bytes[offset + 8];
      offset += 9;
      if (flags & 0x80) offset += 3 * (2 ** ((flags & 7) + 1));
      offset++; // LZW minimum code size
      if (!skipBlocks()) return false;
    } else if (marker === 0x21) {
      const label = bytes[offset++];
      if (label === 0xf9) {
        if (bytes[offset] !== 4 || bytes[offset + 5] !== 0) return false;
        offset += 6;
      } else if (label === 0xff) {
        if (bytes[offset] !== 11 || bytes.subarray(offset + 1, offset + 12).toString('ascii') !== 'NETSCAPE2.0') return false;
        offset += 12;
        if (bytes[offset] !== 3 || bytes[offset + 1] !== 1 || bytes[offset + 4] !== 0) return false;
        offset += 5;
      } else return false;
    } else return false;
  }
  return false;
}

function validateMetadata(metadata) {
  const pages = metadata.pages || 1;
  const width = metadata.width;
  const height = metadata.pageHeight || metadata.height;
  if (!Number.isSafeInteger(width) || !Number.isSafeInteger(height) || width < 1 || height < 1 || !Number.isSafeInteger(pages) || pages < 1) {
    throw new AppError('Не удалось прочитать размеры изображения');
  }
  if (pages > MEDIA_LIMITS.frames) throw new AppError(`В анимации должно быть не больше ${MEDIA_LIMITS.frames} кадров`);
  if (width * height * pages > MEDIA_LIMITS.inputPixels) throw new AppError('Слишком большое разрешение изображения или анимации');
  if (pages > 1) {
    const delays = metadata.delay;
    if (!Array.isArray(delays) || delays.length !== pages || delays.some(delay => !Number.isSafeInteger(delay) || delay < 0)) {
      throw new AppError('Некорректные задержки кадров анимации');
    }
    // Browsers render a zero-delay frame with a minimum delay too.
    const duration = delays.reduce((total, delay) => total + Math.max(delay, 10), 0);
    if (duration > MEDIA_LIMITS.durationMs) throw new AppError('Продолжительность анимации должна быть не больше 2 минут');
  }
  return { width, height, pages };
}

/** Validate and optimise a newly uploaded raster image once, before storage. */
export async function prepareImage(input, { purpose = 'card' } = {}) {
  if (!Object.hasOwn(purposes, purpose)) throw new AppError('Неизвестное назначение изображения');
  if (!(input instanceof Uint8Array) || !input.byteLength || input.byteLength > MEDIA_LIMITS.inputBytes) {
    throw new AppError('Размер файла должен быть до 10 МБ');
  }
  if (processing) throw new AppError('Другое изображение ещё обрабатывается. Повторите загрузку через несколько секунд', 429);
  const bytes = Buffer.isBuffer(input) ? input : Buffer.from(input.buffer, input.byteOffset, input.byteLength);
  const format = inputFormat(bytes);
  processing = true;
  try {
    const options = { failOn: 'warning', limitInputPixels: MEDIA_LIMITS.inputPixels };
    // Read the first page's header to reject frame/pixel bombs before loading all
    // pages. metadata.pages still contains the total number of frames.
    const metadata = await sharp(bytes, options).metadata();
    if (metadata.format !== format) throw new AppError('Формат файла не соответствует изображению');
    const source = validateMetadata(metadata);
    const animated = purpose !== 'icon' && source.pages > 1;
    const size = purposes[purpose];
    const outputLimit = animated ? MEDIA_LIMITS.animatedBytes : MEDIA_LIMITS.staticBytes;
    let output;
    for (const quality of [82, 64]) {
      // Sharp executes decoding/resizing/encoding in native worker threads.
      // Resize applies to each page, rather than the vertically stacked image.
      const pipeline = sharp(bytes, { ...options, animated })
        .rotate()
        .resize({ width: size, height: size, fit: 'inside', withoutEnlargement: true })
        .webp({ quality, alphaQuality: 100, effort: 3, ...(animated ? { loop: metadata.loop ?? 0, delay: metadata.delay } : {}) })
        .timeout({ seconds: MEDIA_LIMITS.processingSeconds });
      // Metadata is stripped by Sharp's default output settings.
      output = await pipeline.toBuffer({ resolveWithObject: true });
      if (output.data.length <= outputLimit) break;
    }
    if (output.data.length > outputLimit) throw new AppError('Изображение слишком большое после сжатия. Уменьшите размер или число кадров');

    const unchangedGif = format === 'gif'
      && source.width <= size && source.height <= size
      // Some GIF decoders crop a larger logical screen to the frame bounds.
      // Never pass such a screen through with unvalidated browser dimensions.
      && bytes.readUInt16LE(6) === source.width && bytes.readUInt16LE(8) === source.height
      && (purpose !== 'icon' || source.pages === 1)
      && bytes.length <= output.data.length && plainTinyGif(bytes);
    if (unchangedGif) {
      return { bytes, extension: 'gif', contentType: 'image/gif', ...source, animated };
    }
    const pages = animated ? (output.info.pages || source.pages) : 1;
    return {
      bytes: output.data, extension: 'webp', contentType: 'image/webp',
      width: output.info.width, height: output.info.pageHeight || output.info.height,
      pages, animated,
    };
  } catch (error) {
    if (error instanceof AppError) throw error;
    if (/pixel limit|image dimensions|out of memory|memory area|too large/i.test(error.message)) {
      throw new AppError('Слишком большое разрешение изображения или анимации');
    }
    if (/timeout/i.test(error.message)) throw new AppError('Обработка изображения заняла слишком много времени. Уменьшите размер или число кадров');
    throw new AppError('Не удалось прочитать изображение: файл повреждён или имеет неподдерживаемый формат');
  } finally {
    processing = false;
  }
}
