/** Desktop promo media: square 1:1 card, recommended 1080×1080. */
export const LOGIN_PROMO_ASPECT = 1;
export const LOGIN_PROMO_WIDTH = 1080;
export const LOGIN_PROMO_HEIGHT = 1080;
export const LOGIN_PROMO_OUTPUT_WIDTH = 1080;
export const LOGIN_PROMO_OUTPUT_HEIGHT = 1080;
export const LOGIN_PROMO_FILL = '#0b0f12';
export const LOGIN_IMAGE_ACCEPT = 'image/png,image/jpeg,image/webp';
export const LOGIN_MEDIA_ACCEPT = 'image/png,image/jpeg,image/webp,video/mp4,.mp4';
export const LOGIN_MAX_IMAGE_BYTES = 6 * 1024 * 1024;
export const LOGIN_MAX_VIDEO_BYTES = 20 * 1024 * 1024;
export const LOGIN_MAX_VIDEO_SECONDS = 30;

export function isLoginVideoFile(file) {
  if (!file) return false;
  const type = String(file.type || '').toLowerCase();
  const name = String(file.name || '').toLowerCase();
  return type === 'video/mp4' || type === 'application/mp4' || name.endsWith('.mp4');
}

export function isLoginImageFile(file) {
  if (!file) return false;
  const type = String(file.type || '').toLowerCase();
  const name = String(file.name || '').toLowerCase();
  return (
    type === 'image/png'
    || type === 'image/jpeg'
    || type === 'image/jpg'
    || type === 'image/webp'
    || name.endsWith('.png')
    || name.endsWith('.jpg')
    || name.endsWith('.jpeg')
    || name.endsWith('.webp')
  );
}

export function validateLoginImageFile(file) {
  if (!file) return 'Choose a JPG, PNG, or WebP image';
  if (!isLoginImageFile(file)) return 'Upload a JPG, PNG, or WebP image';
  if (file.size > LOGIN_MAX_IMAGE_BYTES) return 'Image must be 6 MB or smaller';
  return null;
}

export function validateLoginVideoFileMeta(file) {
  if (!file) return 'Choose an MP4 video';
  if (!isLoginVideoFile(file)) return 'Upload an MP4 video with H.264/AAC';
  if (file.size > LOGIN_MAX_VIDEO_BYTES) return 'Video must be 20 MB or smaller';
  return null;
}

export function readVideoDuration(file) {
  return new Promise((resolve, reject) => {
    const url = URL.createObjectURL(file);
    const video = document.createElement('video');
    video.preload = 'metadata';
    video.muted = true;
    video.playsInline = true;
    const cleanup = () => URL.revokeObjectURL(url);
    video.onloadedmetadata = () => {
      const duration = Number(video.duration || 0);
      cleanup();
      if (!Number.isFinite(duration) || duration <= 0) {
        reject(new Error('Video duration could not be read'));
        return;
      }
      resolve(duration);
    };
    video.onerror = () => {
      cleanup();
      reject(new Error('Video could not be loaded'));
    };
    video.src = url;
  });
}

export async function validateLoginVideoFile(file) {
  const metaError = validateLoginVideoFileMeta(file);
  if (metaError) return metaError;
  try {
    const duration = await readVideoDuration(file);
    if (duration > LOGIN_MAX_VIDEO_SECONDS + 0.05) return 'Video must be 30 seconds or shorter';
  } catch (error) {
    return error?.message || 'Video could not be loaded';
  }
  return null;
}

export function normalizeLoginDisplay(raw) {
  const src = raw && typeof raw === 'object' ? raw : {};
  const fit = src.fit === 'cover' ? 'cover' : 'contain';
  const zoom = Math.min(3, Math.max(1, Number(src.zoom) || 1));
  const offsetX = Math.max(-1, Math.min(1, Number(src.offset_x) || 0));
  const offsetY = Math.max(-1, Math.min(1, Number(src.offset_y) || 0));
  return { fit, zoom, offset_x: offsetX, offset_y: offsetY };
}

export function loginDisplayStyle(raw, { forceContain = false } = {}) {
  const settings = normalizeLoginDisplay(raw);
  const fit = forceContain ? 'contain' : settings.fit;
  return {
    objectFit: fit,
    objectPosition: 'center',
    transform: `translate(${settings.offset_x * 50}%, ${settings.offset_y * 50}%) scale(${settings.zoom})`,
    transformOrigin: 'center center',
  };
}

export function displaySettingsFromCropper({ mode, zoom, offset, viewSize, mediaWidth, mediaHeight }) {
  let nextZoom = Math.min(3, Math.max(1, Number(zoom) || 1));
  if (mode === 'cover' && mediaWidth && mediaHeight && viewSize?.w && viewSize?.h) {
    const contain = Math.min(viewSize.w / mediaWidth, viewSize.h / mediaHeight);
    const cover = Math.max(viewSize.w / mediaWidth, viewSize.h / mediaHeight);
    if (contain > 0) nextZoom = Math.min(3, nextZoom * (cover / contain));
  }
  return normalizeLoginDisplay({
    fit: 'contain',
    zoom: nextZoom,
    offset_x: viewSize?.w ? offset.x / viewSize.w : 0,
    offset_y: viewSize?.h ? offset.y / viewSize.h : 0,
  });
}

export async function captureLoginVideoPoster(file) {
  const url = URL.createObjectURL(file);
  const video = document.createElement('video');
  video.muted = true;
  video.playsInline = true;
  video.preload = 'auto';
  video.src = url;
  try {
    await new Promise((resolve, reject) => {
      video.onloadeddata = () => resolve();
      video.onerror = () => reject(new Error('Video could not be loaded'));
    });
    const seekTo = Math.min(0.12, Math.max(0, (video.duration || 0) / 4));
    if (Number.isFinite(video.duration) && video.duration > 0) {
      await new Promise((resolve) => {
        video.onseeked = () => resolve();
        try {
          video.currentTime = seekTo;
        } catch {
          resolve();
        }
      });
    }
    const vw = Math.max(1, video.videoWidth || LOGIN_PROMO_OUTPUT_WIDTH);
    const vh = Math.max(1, video.videoHeight || LOGIN_PROMO_OUTPUT_HEIGHT);
    const canvas = document.createElement('canvas');
    canvas.width = vw;
    canvas.height = vh;
    const ctx = canvas.getContext('2d');
    ctx.drawImage(video, 0, 0, vw, vh);
    const blob = await new Promise((resolve, reject) => {
      canvas.toBlob((next) => (next ? resolve(next) : reject(new Error('Poster capture failed'))), 'image/jpeg', 0.86);
    });
    return new File([blob], 'login-poster.jpg', { type: 'image/jpeg' });
  } finally {
    URL.revokeObjectURL(url);
  }
}
