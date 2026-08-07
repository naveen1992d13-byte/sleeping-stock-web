import Constants from 'expo-constants';

const fallbackApiBaseUrl = '';

export function normalizeApiBaseUrl(value) {
  const raw = String(value || '').trim().replace(/\/+$/, '');
  if (!raw) throw new Error('NMTS server URL is not configured. Scan the pairing QR from the NMTS website.');

  let parsed;
  try {
    parsed = new URL(raw);
  } catch {
    throw new Error('The NMTS server URL in the QR code is invalid.');
  }

  if (parsed.protocol !== 'https:') {
    throw new Error('Only secure HTTPS NMTS server URLs are allowed.');
  }
  return raw;
}

export const API_BASE_URL =
  process.env.EXPO_PUBLIC_API_BASE_URL ||
  Constants.expoConfig?.extra?.apiBaseUrl ||
  fallbackApiBaseUrl;

export const REQUEST_TIMEOUT_MS = 20000;
