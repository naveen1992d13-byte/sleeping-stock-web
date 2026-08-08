import axios from 'axios';

import { API_BASE_URL, REQUEST_TIMEOUT_MS, normalizeApiBaseUrl } from './config/env';
import { getSession, getSessionToken, clearSession } from './services/session';

let onSessionInvalidated = null;

export class ApiError extends Error {
  constructor(message, { status = 0, kind = 'server', data = null } = {}) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.kind = kind;
    this.data = data;
  }
}

export function setOnSessionInvalidated(callback) {
  onSessionInvalidated = typeof callback === 'function' ? callback : null;
}

async function resolveBaseUrl(overrideUrl) {
  if (overrideUrl) return normalizeApiBaseUrl(overrideUrl);
  const session = await getSession();
  return normalizeApiBaseUrl(session?.apiBaseUrl || API_BASE_URL);
}

function errorFromAxios(error) {
  if (error instanceof ApiError) return error;
  const status = error?.response?.status || 0;
  const detail = error?.response?.data?.detail;
  const message = typeof detail === 'string' ? detail : detail?.message || error?.message || 'Unable to connect to the NMTS server.';
  const kind = status === 401 || status === 403 ? 'auth' : status ? 'server' : 'network';
  return new ApiError(message, { status, kind, data: error?.response?.data || null });
}

async function request(method, path, { data, params, auth = true, baseUrl } = {}) {
  try {
    const resolvedBaseUrl = await resolveBaseUrl(baseUrl);
    const headers = { 'Content-Type': 'application/json' };
    if (auth) {
      const token = await getSessionToken();
      if (!token) throw new ApiError('Device session not found. Please pair this device again.', { status: 401, kind: 'auth' });
      headers.Authorization = `Bearer ${token}`;
    }
    const response = await axios({ method, url: `${resolvedBaseUrl}${path}`, data, params, headers, timeout: REQUEST_TIMEOUT_MS });
    return response.data;
  } catch (rawError) {
    const error = errorFromAxios(rawError);
    if (auth && (error.status === 401 || error.status === 403)) {
      await clearSession();
      if (onSessionInvalidated) onSessionInvalidated(error);
    }
    throw error;
  }
}

export const verifyPairing = ({ mobileUserId, pairingType, pairingCode, pairingToken, deviceUserName, deviceUserMobile, deviceName, deviceInfo, appVersion, pushToken, apiBaseUrl }) =>
  request('post', '/mobile/pairing/verify', {
    auth: false,
    baseUrl: apiBaseUrl,
    data: {
      mobile_user_id: mobileUserId || null,
      pairing_type: pairingType || null,
      pairing_code: pairingCode,
      pairing_token: pairingToken || null,
      device_user_name: deviceUserName,
      device_user_mobile: deviceUserMobile,
      device_name: deviceName,
      device_info: deviceInfo,
      app_version: appVersion,
      push_token: pushToken,
    },
  });

export const validateSession = () => request('get', '/mobile/session/validate');
export const registerPushToken = (pushToken) => request('put', '/mobile/devices/push-token', { data: { push_token: pushToken } });
export const getNotifications = () => request('get', '/mobile/notifications');
export const acceptNotification = (requestGroupKey) => request('post', '/mobile/notifications/accept', { data: { request_group_key: requestGroupKey } });
export const skipNotification = (requestGroupKey) => request('post', '/mobile/notifications/skip', { data: { request_group_key: requestGroupKey } });
export const submitPartResponse = (requestGroupKey, parts) => request('post', '/mobile/notifications/respond', {
  data: {
    request_group_key: requestGroupKey,
    parts: parts.map((part) => ({
      order_request_id: part.orderRequestId ?? part.order_request_id,
      part_number: part.partNumber ?? part.part_number,
      accepted_qty: part.acceptedQty ?? part.accepted_qty,
      remark: part.remark || '',
    })),
  },
});
export const submitStockVerification = ({
  partNumber,
  partName,
  physicalQty,
  location,
  remark,
  entryMethod,
  clientId,
  verificationSessionId,
  isNewPart,
  verificationType = 'physical',
  damageQty = 0,
}) => request('post', '/mobile/stock-verification', {
  data: {
    part_number: partNumber,
    part_name: partName || '',
    physical_qty: physicalQty,
    location,
    remark,
    entry_method: entryMethod,
    client_id: clientId,
    verification_session_id: verificationSessionId,
    is_new_part: Boolean(isNewPart),
    verification_type: verificationType,
    damage_qty: damageQty,
  },
});

export const getAutoPerpetualTasks = () => request('get', '/mobile/auto-perpetual/tasks');
export const getAutoPerpetualSessionToday = () => request('get', '/mobile/auto-perpetual/session/today');
export const getStockVerificationHistory = (options = {}) => request('get', '/mobile/stock-verification/history', {
  params: typeof options === 'string' ? { part_number: options } : { ...(options.partNumber ? { part_number: options.partNumber } : {}), ...(options.limit ? { limit: options.limit } : {}) },
});
export const searchStock = (partNumbers) => request('get', '/mobile/stock-search', {
  params: { part_numbers: Array.isArray(partNumbers) ? partNumbers.join('\n') : partNumbers },
});
export const getLatestAppVersion = () => request('get', '/mobile/app-versions/latest', { auth: false });
