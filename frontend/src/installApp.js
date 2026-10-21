import React, { useEffect, useState } from 'react';
import { isProductionInstallUi, isStandaloneDisplay } from '@/testingUi';
import { clearAuthStorage, isInstallPromptSnoozed, snoozeInstallPrompt } from '@/utils/sessionActivity';
import './installApp.css';

const INSTALLED_MESSAGE = 'Sleeping Stock is now open in its own window. You can close this tab.';

function replaceTabWithInstalledMessage() {
  clearAuthStorage();
  const logo = '/icons/sleepingstock-logo.png';
  document.open();
  document.write(`<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Sleeping Stock</title>
  <style>
    html, body { margin: 0; height: 100%; background: #ffffff; }
    body { min-height: 100vh; display: grid; place-items: center; font-family: Inter, ui-sans-serif, system-ui, sans-serif; color: #14240c; }
    main { max-width: 420px; padding: 32px 24px; text-align: center; }
    img { width: min(220px, 60vw); height: auto; object-fit: contain; }
    p { margin: 20px 0 0; font-size: 18px; line-height: 1.45; font-weight: 600; }
  </style>
</head>
<body>
  <main>
    <img src="${logo}" alt="Sleeping Stock" />
    <p>${INSTALLED_MESSAGE}</p>
  </main>
</body>
</html>`);
  document.close();
}

export function LaunchScreen({ children }) {
  const enabled = isProductionInstallUi() && isStandaloneDisplay();
  const [visible, setVisible] = useState(enabled);

  useEffect(() => {
    if (!enabled) return undefined;
    const timer = setTimeout(() => setVisible(false), 1000);
    return () => clearTimeout(timer);
  }, [enabled]);

  if (!visible) return children;

  return (
    <div className="nmts-launch" data-testid="launch-screen" role="img" aria-label="Sleeping Stock">
      <img src="/icons/sleepingstock-logo.png" alt="" />
    </div>
  );
}

export function InstallPrompt({ user }) {
  const [open, setOpen] = useState(false);

  useEffect(() => {
    if (!isProductionInstallUi() || isStandaloneDisplay()) return undefined;
    const onInstalled = () => {
      if (isStandaloneDisplay()) return;
      replaceTabWithInstalledMessage();
    };
    window.addEventListener('appinstalled', onInstalled);
    return () => window.removeEventListener('appinstalled', onInstalled);
  }, []);

  useEffect(() => {
    if (!user || !isProductionInstallUi() || isStandaloneDisplay() || isInstallPromptSnoozed()) {
      setOpen(false);
      return undefined;
    }
    const sync = () => {
      if (window.__nmtsInstallPrompt) setOpen(true);
    };
    sync();
    window.addEventListener('nmts-install-prompt', sync);
    return () => window.removeEventListener('nmts-install-prompt', sync);
  }, [user]);

  const install = async () => {
    const promptEvent = window.__nmtsInstallPrompt;
    window.__nmtsInstallPrompt = null;
    setOpen(false);
    if (!promptEvent) return;
    try {
      await promptEvent.prompt();
    } catch {
      /* the browser dismissed the prompt */
    }
  };

  const notNow = () => {
    snoozeInstallPrompt();
    setOpen(false);
  };

  if (!open) return null;

  return (
    <div className="nmts-install-backdrop" data-testid="install-modal">
      <div className="nmts-install-modal" role="dialog" aria-modal="true" aria-labelledby="nmts-install-title">
        <img src="/icons/sleepingstock-logo.png" alt="" />
        <p id="nmts-install-title">Install Sleeping Stock on this computer? It will open in its own window from your desktop.</p>
        <div className="nmts-install-actions">
          <button type="button" data-testid="install-confirm" onClick={install}>Install</button>
          <button type="button" data-testid="install-dismiss" onClick={notNow}>Not now</button>
        </div>
      </div>
    </div>
  );
}

export function useInstalledApp() {
  const [installed, setInstalled] = useState(false);

  useEffect(() => {
    if (!isProductionInstallUi() || isStandaloneDisplay()) return undefined;
    const detect = navigator.getInstalledRelatedApps;
    if (typeof detect !== 'function') return undefined;
    let cancelled = false;
    detect.call(navigator).then((apps) => {
      if (!cancelled && Array.isArray(apps) && apps.length > 0) setInstalled(true);
    }).catch(() => {});
    return () => { cancelled = true; };
  }, []);

  return installed && isProductionInstallUi() && !isStandaloneDisplay();
}

export function openInstalledApp() {
  const url = `${window.location.origin}/login`;
  window.open(url, '_blank', 'noopener');
}
