import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '@/App.js';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { User, Key, ArrowRight } from 'lucide-react';
import './LoginPage.css';

export function LoginPage() {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [loading, setLoading] = useState(false);
  const { login } = useAuth();
  const navigate = useNavigate();

  const handleSubmit = async (e) => {
    e.preventDefault();
    setLoading(true);

    try {
      await login(email.trim(), password);
      toast.success('Login successful!');
      navigate('/');
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

              <div className="nmts-login-field">
                <Key className="nmts-field-icon" aria-hidden="true" />
                <input
                  type="password"
                  data-testid="login-password-input"
                  placeholder="Password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  required
                  autoComplete="current-password"
                  aria-label="Password"
                />
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
