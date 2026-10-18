import React, { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import axios from 'axios';
import { API, useAuth, canAccessPermission, canAccessMenuItem } from '@/App';
import { getFirstAllowedMenuItem } from '@/config/menuConfig';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { User, Key, ArrowRight, Eye, EyeOff } from 'lucide-react';
import { loginDisplayStyle } from './loginPromoCrop';
import './LoginPage.css';

function seedHomeTab(user) {
  try {
    let tab = null;
    if (canAccessPermission(user, 'Analytics')) {
      tab = { id: 'analytics', label: 'Analytics', path: '/analytics', permission: 'Analytics' };
    } else {
      const first = getFirstAllowedMenuItem(user, canAccessMenuItem);
      if (first) {
        tab = {
          id: first.id,
          label: first.label,
          path: first.path,
          permission: first.permissionLabel || first.label,
        };
      }
    }
    if (tab) {
      localStorage.setItem('openTabs', JSON.stringify([tab]));
      localStorage.setItem('activeTab', tab.id);
      return tab.path;
    }
  } catch {
    /* ignore */
  }
  return '/';
}

export function LoginPage() {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [loading, setLoading] = useState(false);
  const [showPassword, setShowPassword] = useState(false);
  const [maintenance, setMaintenance] = useState(null);
  const [promoUrl, setPromoUrl] = useState(null);
  const [promoPosterUrl, setPromoPosterUrl] = useState(null);
  const [promoKind, setPromoKind] = useState('image');
  const [promoFailed, setPromoFailed] = useState(false);
  const [promoDisplay, setPromoDisplay] = useState(null);
  // loading | ready | error — error keeps the two-panel shell so a flaky
  // /login-screen/active response cannot flash the compact "old" form.
  const [promoLoadState, setPromoLoadState] = useState('loading');
  const { login } = useAuth();
  const navigate = useNavigate();

  useEffect(() => {
    // Reject bfcache restores of a prior SPA document after logout/back.
    const onPageShow = (event) => {
      if (event.persisted) {
        console.info('[nmts-auth] pageshow bfcache restore → hard reload');
        window.location.reload();
      }
    };
    window.addEventListener('pageshow', onPageShow);
    return () => window.removeEventListener('pageshow', onPageShow);
  }, []);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await axios.get(`${API}/maintenance/status`);
        if (!cancelled) setMaintenance(res.data || null);
      } catch {
        if (!cancelled) setMaintenance(null);
      }
    })();
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    let cancelled = false;
    const timers = { expiry: null, poll: null };
    let lastUrl = null;

    const clearExpiry = () => {
      if (timers.expiry) {
        clearTimeout(timers.expiry);
        timers.expiry = null;
      }
    };

    const applyPayload = (data) => {
      const origin = API.replace(/\/api$/, '');
      const mediaPath = data?.media_url || data?.image_url;
      const posterPath = data?.poster_url || (data?.media_kind === 'video' ? data?.image_url : mediaPath);
      const nextUrl = mediaPath ? `${origin}${mediaPath}` : null;
      const nextPoster = posterPath ? `${origin}${posterPath}` : nextUrl;
      const nextKind = data?.media_kind === 'video' ? 'video' : 'image';
      if (lastUrl !== `${nextKind}:${nextUrl}:${nextPoster}`) {
        lastUrl = `${nextKind}:${nextUrl}:${nextPoster}`;
        if (!cancelled) {
          setPromoFailed(false);
          setPromoUrl(nextUrl);
          setPromoPosterUrl(nextPoster);
          setPromoKind(nextKind);
          setPromoDisplay(data?.display_settings || null);
        }
      }
      clearExpiry();
      if (data?.type === 'scheduled' && data?.end_at) {
        const endMs = new Date(data.end_at).getTime();
        if (!Number.isNaN(endMs)) {
          timers.expiry = setTimeout(() => {
            fetchActive();
          }, Math.max(250, endMs - Date.now() + 400));
        }
      }
    };

    const fetchActive = async () => {
      try {
        const res = await axios.get(`${API}/login-screen/active`, { timeout: 15000 });
        if (!cancelled) {
          applyPayload(res.data || {});
          setPromoLoadState('ready');
        }
      } catch {
        /* keep current promo; stay in split shell on transient failures */
        if (!cancelled) setPromoLoadState((prev) => (prev === 'ready' ? 'ready' : 'error'));
      }
    };

    fetchActive();
    timers.poll = setInterval(fetchActive, 30000);

    return () => {
      cancelled = true;
      clearExpiry();
      if (timers.poll) clearInterval(timers.poll);
    };
  }, []);

  const handleSubmit = async (e) => {
    e.preventDefault();
    setLoading(true);
    const submitEmail = String(email || '').trim();
    const submitPassLen = String(password || '').length;
    console.info('[nmts-auth] login submit', { email: submitEmail, passLen: submitPassLen });

    try {
      const loggedUser = await login(submitEmail, password);
      console.info('[nmts-auth] login ok', { role: loggedUser?.role, email: loggedUser?.email });
      toast.success('Login successful!');
      const path = seedHomeTab(loggedUser);
      navigate(path || '/');
    } catch (error) {
      const status = error?.response?.status;
      const detail = error?.response?.data?.detail;
      console.warn('[nmts-auth] login failed', { status, detail, email: submitEmail, passLen: submitPassLen });
      toast.error(detail || 'Login failed');
    } finally {
      setLoading(false);
    }
  };

  const showPromo = Boolean(promoUrl) && !promoFailed;
  // Keep the two-panel shell while loading or after a fetch error so a slow
  // /login-screen/active cannot flash the compact form and look like an old build.
  const useSplitShell = showPromo || promoLoadState !== 'ready' || Boolean(promoUrl);

  return (
    <div className="nmts-login-page" data-testid="login-page" data-nmts-build={process.env.REACT_APP_TESTING_COMMIT || ''}>
      <div className="nmts-login-overlay" />

      <main className="nmts-login-shell">
        <div className={`nmts-login-panel${useSplitShell ? ' nmts-login-panel--split' : ''}`}>
        <div className="nmts-login-panel-run" aria-hidden="true" />
        <div className="nmts-login-panel-face">
        {showPromo && (
          <aside className="nmts-login-promo" aria-label="Login promotional media">
            <div className="nmts-login-promo-bg" aria-hidden="true">
              <img src={promoPosterUrl || promoUrl} alt="" />
            </div>
            <div className="nmts-login-promo-media">
              {promoKind === 'video' ? (
                <video
                  src={promoUrl}
                  poster={promoPosterUrl || undefined}
                  autoPlay
                  muted
                  loop
                  playsInline
                  controls={false}
                  disablePictureInPicture
                  data-testid="login-promo-video"
                  style={loginDisplayStyle(promoDisplay, { forceContain: true })}
                  onError={() => setPromoFailed(true)}
                />
              ) : (
                <img
                  src={promoUrl}
                  alt=""
                  data-testid="login-promo-image"
                  style={loginDisplayStyle(promoDisplay, { forceContain: true })}
                  onError={() => setPromoFailed(true)}
                />
              )}
            </div>
          </aside>
        )}
        <section className="nmts-login-card" aria-label="NMTS login">
          <div className="nmts-login-card-glow" />

          <div className="nmts-login-content">
            <div className="nmts-login-brand">
              <img
                src="/LoginPage Image.png"
                alt="Sleeping Stock Logo"
                className="nmts-login-logo"
              />
            </div>

            <header className="nmts-login-header">
              <h1>
                Welcome to <span>NMTS</span>
              </h1>
              <p>Non Moving Tracking System</p>
              <div className="nmts-title-accent" />
            </header>

            {maintenance?.maintenance_active && (
              <div
                className="rounded-lg border px-3 py-2 text-sm mb-3"
                style={{ background: '#FEF3C7', borderColor: '#F59E0B', color: '#92400E' }}
                data-testid="maintenance-banner"
              >
                {maintenance.message || 'System maintenance in progress. Please try again after 4:00 AM.'}
                <div className="text-xs mt-1 opacity-90">Master Admin may sign in for Storage / Archive monitoring (read-only).</div>
              </div>
            )}

            <form onSubmit={handleSubmit} className="nmts-login-form">
              <div className="nmts-login-field">
                <User className="nmts-field-icon" aria-hidden="true" />
                <input
                  id="login-identifier"
                  type="text"
                  data-testid="login-email-input"
                  placeholder="User ID / Email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  required
                  autoComplete="username"
                  aria-label="User ID or Email"
                />
              </div>

              <div className="nmts-login-field nmts-login-field--password">
                <Key className="nmts-field-icon" aria-hidden="true" />
                <input
                  type={showPassword ? 'text' : 'password'}
                  data-testid="login-password-input"
                  placeholder="Password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  required
                  autoComplete="current-password"
                  aria-label="Password"
                />
                <button
                  type="button"
                  className="nmts-password-toggle"
                  onClick={() => setShowPassword((v) => !v)}
                  aria-label={showPassword ? 'Hide password' : 'Show password'}
                  tabIndex={0}
                >
                  {showPassword ? (
                    <EyeOff className="nmts-password-toggle-icon" aria-hidden="true" />
                  ) : (
                    <Eye className="nmts-password-toggle-icon" aria-hidden="true" />
                  )}
                </button>
              </div>

              <Button
                type="submit"
                data-testid="login-submit-button"
                className="nmts-login-button"
                disabled={loading}
              >
                <span>{loading ? 'Logging in...' : 'Login'}</span>
                {!loading && <ArrowRight className="nmts-login-arrow" aria-hidden="true" />}
              </Button>
            </form>

            <footer className="nmts-login-footer">
              © 2026 NMTS. All rights reserved.
            </footer>
          </div>
        </section>
        </div>
        </div>
      </main>
    </div>
  );
}
