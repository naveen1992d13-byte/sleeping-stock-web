import axios from 'axios';

export async function parseBlobError(error, fallback = 'Request failed') {
  const data = error?.response?.data;
  if (data instanceof Blob) {
    try {
      const text = await data.text();
      const parsed = JSON.parse(text);
      const detail = parsed?.detail;
      if (typeof detail === 'string' && detail.trim()) return detail;
      if (detail && typeof detail === 'object' && (detail.message || detail.msg)) {
        return detail.message || detail.msg;
      }
      if (typeof parsed?.message === 'string' && parsed.message.trim()) return parsed.message;
    } catch {
      /* not JSON */
    }
  }
  const detail = error?.response?.data?.detail;
  if (typeof detail === 'string' && detail.trim()) return detail;
  if (detail && typeof detail === 'object' && (detail.message || detail.msg)) {
    return detail.message || detail.msg;
  }
  return fallback;
}

export async function openInlineAttachment(href, fileName = 'attachment') {
  const res = await axios.get(href, { responseType: 'blob' });
  const ctype = String(res.headers?.['content-type'] || res.data?.type || '')
    .split(';')[0]
    .trim()
    .toLowerCase();
  if (ctype.includes('application/json')) {
    const text = await res.data.text();
    let parsed = {};
    try {
      parsed = JSON.parse(text);
    } catch {
      parsed = {};
    }
    const detail = parsed?.detail;
    const msg =
      (typeof detail === 'string' && detail) ||
      detail?.message ||
      parsed?.message ||
      'Unable to open attachment';
    const err = new Error(msg);
    err.status = res.status;
    throw err;
  }
  const blob =
    res.data instanceof Blob
      ? res.data
      : new Blob([res.data], { type: ctype || 'application/octet-stream' });
  const url = URL.createObjectURL(blob);
  const viewable = ctype === 'application/pdf' || ctype.startsWith('image/');
  // window.open returns null whenever 'noopener' is passed, so it cannot be used to detect a blocked popup.
  const opened = viewable ? window.open(url, '_blank') : null;
  if (opened) {
    opened.opener = null;
  } else {
    const a = document.createElement('a');
    a.href = url;
    a.download = fileName;
    document.body.appendChild(a);
    a.click();
    a.remove();
  }
  window.setTimeout(() => URL.revokeObjectURL(url), 120000);
  return { url, fileName, contentType: ctype };
}
