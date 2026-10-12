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

export async function captureLoginVideoPoster(file, size = LOGIN_PROMO_OUTPUT_WIDTH) {
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
    const canvas = document.createElement('canvas');
    canvas.width = size;
    canvas.height = size;
    const ctx = canvas.getContext('2d');
    ctx.fillStyle = LOGIN_PROMO_FILL;
    ctx.fillRect(0, 0, size, size);
    const vw = video.videoWidth || size;
    const vh = video.videoHeight || size;
    const scale = Math.min(size / vw, size / vh);
    const dw = vw * scale;
    const dh = vh * scale;
    ctx.drawImage(video, (size - dw) / 2, (size - dh) / 2, dw, dh);
    const blob = await new Promise((resolve, reject) => {
      canvas.toBlob((next) => (next ? resolve(next) : reject(new Error('Poster capture failed'))), 'image/jpeg', 0.86);
    });
    return new File([blob], 'login-poster.jpg', { type: 'image/jpeg' });
  } finally {
    URL.revokeObjectURL(url);
  }
}
