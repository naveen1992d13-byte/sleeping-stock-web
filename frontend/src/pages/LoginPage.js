import React, { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import axios from 'axios';
import { API, useAuth, canAccessPermission, canAccessMenuItem } from '@/App';
import { getFirstAllowedMenuItem } from '@/config/menuConfig';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { User, Key, ArrowRight, Eye, EyeOff } from 'lucide-react';
import { LOGIN_PROMO_ASPECT, LOGIN_PROMO_FILL, loginDisplayStyle } from './loginPromoCrop';
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

function ratioNearPromoAspect(width, height) {
  if (!width || !height) return false;
  const ratio = width / height;
  const target = LOGIN_PROMO_ASPECT;
  return Math.abs(ratio - target) / target <= 0.05;
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
  const [displaySettings, setDisplaySettings] = useState(null);
  const [mediaNatural, setMediaNatural] = useState({ w: 0, h: 0 });
  const { login } = useAuth();
  const navigate = useNavigate();

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
      const nextSettings = data?.display_settings || null;
      if (lastUrl !== `${nextKind}:${nextUrl}:${nextPoster}`) {
        lastUrl = `${nextKind}:${nextUrl}:${nextPoster}`;
        if (!cancelled) {
          setPromoFailed(false);
          setPromoUrl(nextUrl);
          setPromoPosterUrl(nextPoster);
          setPromoKind(nextKind);
          setMediaNatural({ w: 0, h: 0 });
        }
      }
      if (!cancelled) setDisplaySettings(nextSettings);
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
        if (!cancelled) applyPayload(res.data || {});
      } catch {
        /* keep current promo; split shell stays mounted regardless */
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

    try {
      const loggedUser = await login(email.trim(), password);
      toast.success('Login successful!');
      const path = seedHomeTab(loggedUser);
      navigate(path || '/');
    } catch (error) {
      toast.error(error.response?.data?.detail || 'Login failed');
    } finally {
      setLoading(false);
    }
  };

  const showPromo = Boolean(promoUrl) && !promoFailed;
  const useSplitShell = true; // two-panel shell mounted from first paint — no layout shift on promo swap

  const mediaStyle = useMemo(() => {
    const near = ratioNearPromoAspect(mediaNatural.w, mediaNatural.h);
    const base = loginDisplayStyle(displaySettings, { forceContain: !near });
    return {
      ...base,
      objectFit: near ? 'cover' : 'contain',
      background: LOGIN_PROMO_FILL,
    };
  }, [displaySettings, mediaNatural.w, mediaNatural.h]);

  const onMediaMeta = (el) => {
    if (!el) return;
    const w = el.naturalWidth || el.videoWidth || 0;
    const h = el.naturalHeight || el.videoHeight || 0;
    if (w > 0 && h > 0) setMediaNatural({ w, h });
  };

  return (
    <div className="nmts-login-page" data-testid="login-page">
      <div className="nmts-login-overlay" />

      <main className="nmts-login-shell">
        <div className="nmts-login-zoom" data-testid="login-zoom-wrap">
        <div className="nmts-login-zoom-inner">
        <div className={`nmts-login-panel${useSplitShell ? ' nmts-login-panel--split' : ''}`} data-testid="login-card-panel">
        <div className="nmts-login-panel-run" aria-hidden="true" />
        <div className="nmts-login-panel-face">
        <aside className="nmts-login-promo" aria-label="Login promotional media" data-testid="login-promo-aside">
            {showPromo ? (
              <>
            <div className="nmts-login-promo-bg" aria-hidden="true" />
            <div className="nmts-login-promo-media">
              {promoKind === 'video' ? (
                <video
                  src={promoUrl}
                  poster={promoPosterUrl || undefined}
                  autoPlay
                  muted
                  loop
                  playsInline
                  preload="auto"
                  controls={false}
                  disablePictureInPicture
                  data-testid="login-promo-video"
                  style={mediaStyle}
                  onLoadedMetadata={(e) => onMediaMeta(e.currentTarget)}
                  onError={() => setPromoFailed(true)}
                />
              ) : (
                <img
                  src={promoUrl}
                  alt=""
                  decoding="async"
                  fetchPriority="high"
                  data-testid="login-promo-image"
                  style={mediaStyle}
                  onLoad={(e) => onMediaMeta(e.currentTarget)}
                  onError={() => setPromoFailed(true)}
                />
              )}
            </div>
              </>
            ) : null}
        </aside>
        <section className="nmts-login-card" aria-label="NMTS login" data-testid="login-form-column">
          <div className="nmts-login-card-glow" />

          <div className="nmts-login-content">
            <div className="nmts-login-brand" data-testid="login-emblem">
              <img
                src="/LoginPage Image.png"
                alt="Sleeping Stock Logo"
                className="nmts-login-logo"
                data-testid="login-emblem-img"
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
        </div>
        </div>
      </main>
    </div>
  );
}
