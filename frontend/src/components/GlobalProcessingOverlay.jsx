import React, { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';
import axios from 'axios';

const ProcessingContext = createContext(null);

const DEFAULT_STATE = {
  visible: false,
  title: 'Processing…',
  message: 'Please wait while NMTS completes your request.',
  progress: null,
};

function operationCopy(config = {}) {
  const url = String(config.url || '').toLowerCase();
  const method = String(config.method || 'get').toLowerCase();

  if (url.includes('/auth/login')) return { title: 'Signing in…', message: 'Verifying your NMTS account' };
  if (url.includes('/upload') && method !== 'get') return { title: 'Uploading…', message: 'Uploading and validating inventory data' };
  if (url.includes('/publish')) return { title: 'Publishing…', message: 'Publishing the selected upload batch' };
  if (url.includes('/cancel')) return { title: 'Cancelling…', message: 'Cancelling the selected transaction' };
  if (url.includes('/export') || url.includes('/download') || url.includes('/raw-file') || config.responseType === 'blob') return { title: 'Preparing file…', message: 'Generating and downloading your file' };
  if (url.includes('/request') && (method === 'post' || method === 'put' || method === 'patch')) return { title: 'Sending request…', message: 'Processing the branch request transaction' };
  if (url.includes('/dispatch')) return { title: 'Dispatching…', message: 'Updating dispatch details' };
  if (url.includes('/receive')) return { title: 'Receiving…', message: 'Updating received stock details' };
  if (url.includes('/complete')) return { title: 'Completing…', message: 'Completing the request transaction' };
  if (url.includes('/report')) return { title: 'Generating report…', message: 'Preparing report data' };
  if (method === 'delete') return { title: 'Removing…', message: 'Updating NMTS records' };
  if (method === 'post' || method === 'put' || method === 'patch') return { title: 'Processing…', message: 'Saving your changes securely' };
  return { title: 'Loading…', message: 'Retrieving the latest NMTS data' };
}

export function ProcessingProvider({ children }) {
  const [state, setState] = useState(DEFAULT_STATE);
  const activeCount = useRef(0);
  const showTimer = useRef(null);
  const originalFetch = useRef(null);

  const begin = useCallback((copy = {}, immediate = false) => {
    activeCount.current += 1;
    const show = () => setState({ visible: true, progress: null, ...copy });
    if (immediate) show();
    else {
      clearTimeout(showTimer.current);
      showTimer.current = setTimeout(show, 220);
    }
  }, []);

  const update = useCallback((patch) => {
    setState((current) => ({ ...current, ...patch }));
  }, []);

  const end = useCallback(() => {
    activeCount.current = Math.max(0, activeCount.current - 1);
    if (activeCount.current === 0) {
      clearTimeout(showTimer.current);
      setState(DEFAULT_STATE);
    }
  }, []);

  const run = useCallback(async (copy, task) => {
    begin(copy, true);
    try { return await task(); }
    finally { end(); }
  }, [begin, end]);

  useEffect(() => {
    const requestId = axios.interceptors.request.use((config) => {
      if (config?.skipGlobalProcessing) return config;
      const copy = operationCopy(config);
      begin(copy);
      config.__nmtsGlobalProcessing = true;

      const existingDownload = config.onDownloadProgress;
      const existingUpload = config.onUploadProgress;
      const progressHandler = (event, existing) => {
        if (event?.total) update({ progress: Math.round((event.loaded * 100) / event.total) });
        if (existing) existing(event);
      };
      config.onDownloadProgress = (event) => progressHandler(event, existingDownload);
      config.onUploadProgress = (event) => progressHandler(event, existingUpload);
      return config;
    });

    const responseId = axios.interceptors.response.use(
      (response) => { if (response?.config?.__nmtsGlobalProcessing) end(); return response; },
      (error) => { if (error?.config?.__nmtsGlobalProcessing) end(); return Promise.reject(error); }
    );

    originalFetch.current = window.fetch.bind(window);
    window.fetch = async (input, init = {}) => {
      if (init?.skipGlobalProcessing) return originalFetch.current(input, init);
      const config = { url: typeof input === 'string' ? input : input?.url, method: init?.method || 'get' };
      begin(operationCopy(config));
      try { return await originalFetch.current(input, init); }
      finally { end(); }
    };

    return () => {
      axios.interceptors.request.eject(requestId);
      axios.interceptors.response.eject(responseId);
      if (originalFetch.current) window.fetch = originalFetch.current;
      clearTimeout(showTimer.current);
    };
  }, [begin, end, update]);

  const value = useMemo(() => ({ begin, end, update, run }), [begin, end, update, run]);

  return (
    <ProcessingContext.Provider value={value}>
      {children}
      <GlobalProcessingOverlay {...state} />
    </ProcessingContext.Provider>
  );
}

export function useProcessing() {
  const value = useContext(ProcessingContext);
  if (!value) throw new Error('useProcessing must be used within ProcessingProvider');
  return value;
}

function GlobalProcessingOverlay({ visible, title, message, progress }) {
  if (!visible) return null;
  const hasProgress = Number.isFinite(progress);
  const ringStyle = hasProgress
    ? { '--nmts-progress': `${Math.max(3, Math.min(100, progress)) * 3.6}deg` }
    : undefined;

  return (
    <div className="nmts-processing-backdrop" role="status" aria-live="polite" aria-busy="true">
      <div className="nmts-processing-card">
        <div className={`nmts-processing-ring ${hasProgress ? 'has-progress' : ''}`} style={ringStyle}>
          <div className="nmts-processing-logo-wrap">
            <img src="/LoginPage Image.png" alt="Sleeping Stock" className="nmts-processing-logo" />
          </div>
        </div>
        <h2>{title}</h2>
        <p className="nmts-processing-message">{message}</p>
        {hasProgress && <div className="nmts-processing-percent">{progress}%</div>}
        <div className="nmts-processing-track"><span style={hasProgress ? { width: `${progress}%` } : undefined} /></div>
        <p className="nmts-processing-note">Please do not close or refresh this page.</p>
      </div>
    </div>
  );
}
