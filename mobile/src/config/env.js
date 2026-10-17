import Constants from 'expo-constants';

export const TESTING_API_BASE_URL = 'https://testing.sleepingstock.in/api';
export const PRODUCTION_API_BASE_URL = 'https://api.sleepingstock.in/api';
export const ALLOWED_API_BASE_URLS = [TESTING_API_BASE_URL, PRODUCTION_API_BASE_URL];

const fallbackApiBaseUrl = '';

export function normalizeApiBaseUrl(value) {
  let raw = String(value || '').trim().replace(/\/+$/, '');
  while (/\/api\/api$/i.test(raw)) {
    raw = raw.replace(/\/api$/i, '');
  }
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
  if (!ALLOWED_API_BASE_URLS.includes(raw)) {
    throw new Error('This NMTS server URL is not allowed. Use a Testing or Production pairing QR, or the Production pairing code.');
  }
  return raw;
}

export const API_BASE_URL =
  process.env.EXPO_PUBLIC_API_BASE_URL ||
  Constants.expoConfig?.extra?.apiBaseUrl ||
  fallbackApiBaseUrl;

export const REQUEST_TIMEOUT_MS = 20000;
