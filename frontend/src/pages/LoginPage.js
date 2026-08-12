import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuth, canAccessPermission, canAccessMenuItem } from '@/App';
import { getFirstAllowedMenuItem } from '@/config/menuConfig';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { User, Key, ArrowRight, Eye, EyeOff } from 'lucide-react';
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
  const { login } = useAuth();
  const navigate = useNavigate();

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

  return (
    <div className="nmts-login-page" data-testid="login-page">
      <div className="nmts-login-overlay" />

      <main className="nmts-login-shell">
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
      </main>
    </div>
  );
}
