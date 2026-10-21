import React, { createContext, useState, useContext, useEffect } from 'react';
import { BrowserRouter, Routes, Route, Navigate, useLocation } from 'react-router-dom';
import axios from 'axios';
import { Toaster } from 'sonner';
import './App.css';
import { ProcessingProvider } from './components/GlobalProcessingOverlay';

// Import pages — module screens mount via DashboardLayout keep-alive.
import { LoginPage } from './pages/LoginPage';
import { DashboardLayout } from './pages/DashboardLayout';
import { SessionInactivityGuard } from './components/SessionInactivityGuard';
import {
  touchSessionActivity,
  isSessionExpiredByInactivity,
  clearAuthStorage,
  getAuthToken,
  readAuthItem,
  writeAuthItem,
  removeAuthItem,
} from './utils/sessionActivity';
import { resolveApiUrl, resolveBackendUrl } from '@/backendUrl';
import { getFirstAllowedMenuItem } from './config/menuConfig';
import { isTestingUiEnabled } from './testingUi';
import { TestingBanner } from './components/TestingBanner';
import { InstallPrompt, LaunchScreen } from './installApp';

const AuthContext = createContext(null);

// Inlined at build time: only a Testing build (REACT_APP_APP_ENV=testing) mounts the banner.
const IS_TESTING_BUILD = String(process.env.REACT_APP_APP_ENV || '').trim().toLowerCase() === 'testing';

export const MENU_PERMISSIONS = {
  '/': 'Analytics',
  '/dashboard': 'Analytics',
  '/notice-board': 'Notice Board',
  '/users': 'User Hub',
  '/upload': 'Upload Center',
  '/products': 'Product Hub',
  '/product-history': 'Product Hub History',
  '/orders': 'Order Desk',
  '/order-history': 'Order History',
  '/requests': 'Request Center',
  '/reports': 'Reports',
  '/analytics': 'Analytics',
  '/storage-cost-monitor': 'Storage & Cost Monitor',
  '/query': null, // Available to master, admin, and user (Query Desk).
  '/sleeping-stock-mobile': null, // Available to master, admin and user; API applies scope rules.
  '/stock-audit': null,
};

export const normalizePermissions = (permissions) => {
  if (Array.isArray(permissions)) {
    return permissions.map((item) => String(item).trim()).filter(Boolean);
  }

  if (permissions && typeof permissions === 'object') {
    return Object.keys(permissions).filter((key) => permissions[key]).map((key) => String(key).trim()).filter(Boolean);
  }

  if (typeof permissions === 'string' && permissions.trim()) {
    return [permissions.trim()];
  }

  return [];
};

export const canAccessPermission = (user, permissionName) => {
  if (!user) return false;
  if (user.role === 'master') return true;
  if (!permissionName) return true;
  return normalizePermissions(user.permissions).includes(permissionName);
};

export const canAccessMenuItem = (user, item) => {
  if (!user || !item) return false;
  if (item.testingOnly && !isTestingUiEnabled()) return false;
  if (item.masterOnly) return user.role === 'master';
  if (item.allRoles) return true;
  if (item.allowUser && user.role === 'user') return true;
  if (item.adminOnly && user.role === 'user') {
    return normalizePermissions(user.permissions).includes(item.permissionLabel || item.label);
  }
  return canAccessPermission(user, item.permissionLabel || item.label);
};

export const useAuth = () => {
  const context = useContext(AuthContext);
  if (!context) throw new Error('useAuth must be used within AuthProvider');
  return context;
};

export const BACKEND_URL = resolveBackendUrl();
export const API = resolveApiUrl();

axios.interceptors.request.use((config) => {
  const token = getAuthToken();
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

axios.interceptors.response.use(
  (response) => response,
  (error) => {
    const status = error?.response?.status;
    const url = String(error?.config?.url || '');
    if (status === 401 && !url.includes('/auth/login')) {
      clearAuthStorage();
      if (typeof window !== 'undefined' && !window.location.pathname.startsWith('/login')) {
        window.location.assign('/login');
      }
    }
    return Promise.reject(error);
  },
);

function SessionBootstrapError({ message, onRetry }) {
  return (
    <div
      className="flex flex-col items-center justify-center gap-4 p-8 text-center"
      style={{ minHeight: '50vh', color: '#111827' }}
      data-testid="session-bootstrap-error"
    >
      <div style={{ fontSize: 18, fontWeight: 600 }}>Unable to reach the server</div>
      <div style={{ fontSize: 14, color: '#6B7280', maxWidth: 420 }}>
        {message || 'The API did not respond. Check your connection, then retry.'}
      </div>
      <button
        type="button"
        onClick={onRetry}
        style={{
          background: '#16A34A',
          color: '#fff',
          border: 0,
          borderRadius: 8,
          padding: '10px 18px',
          fontWeight: 600,
          cursor: 'pointer',
        }}
      >
        Retry
      </button>
    </div>
  );
}

function AuthProvider({ children }) {
  const [user, setUser] = useState(null);
  const [loading, setLoading] = useState(true);
  const [authError, setAuthError] = useState(null);
  const [authNonce, setAuthNonce] = useState(0);

  const saveUserSession = (token, loggedUser) => {
    const cleanUser = {
      ...loggedUser,
      permissions: normalizePermissions(loggedUser?.permissions),
    };

    writeAuthItem('token', token);
    writeAuthItem('user', JSON.stringify(cleanUser));
    writeAuthItem('permissions', JSON.stringify(cleanUser.permissions));
    touchSessionActivity();
    setUser(cleanUser);
    setAuthError(null);
    return cleanUser;
  };

  useEffect(() => {
    let cancelled = false;

    if (isSessionExpiredByInactivity()) {
      clearAuthStorage();
      setUser(null);
      setAuthError(null);
      setLoading(false);
      return undefined;
    }

    const token = getAuthToken();
    const storedUser = readAuthItem('user');

    if (storedUser) {
      try {
        const parsedUser = JSON.parse(storedUser);
        setUser({ ...parsedUser, permissions: normalizePermissions(parsedUser.permissions) });
      } catch {
        removeAuthItem('user');
        removeAuthItem('permissions');
      }
    }

    if (token) {
      setLoading(true);
      setAuthError(null);
      axios.get(`${API}/auth/me`, { timeout: 15000 })
        .then((res) => {
          if (cancelled) return;
          touchSessionActivity();
          saveUserSession(token, res.data);
        })
        .catch((err) => {
          if (cancelled) return;
          const status = err?.response?.status;
          const isAuthReject = status === 401 || status === 403;
          if (isAuthReject) {
            clearAuthStorage();
            setUser(null);
            setAuthError(null);
            return;
          }
          // Keep any cached user for Retry; show a clear error instead of a blank page.
          const detail = err?.response?.data?.detail;
          const msg = typeof detail === 'string'
            ? detail
            : (err?.code === 'ECONNABORTED'
              ? 'Session check timed out while the server was busy.'
              : (err?.message || 'Backend is unavailable.'));
          setAuthError(msg);
        })
        .finally(() => {
          if (!cancelled) setLoading(false);
        });
    } else {
      setLoading(false);
      setAuthError(null);
    }

    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [authNonce]);

  const login = async (identifier, password) => {
    const response = await axios.post(`${API}/auth/login`, {
      email: String(identifier || '').trim(),
      password,
    });

    return saveUserSession(response.data.access_token, response.data.user);
  };

  const logout = () => {
    clearAuthStorage();
    setUser(null);
    setAuthError(null);
  };

  const refreshUser = async () => {
    const token = getAuthToken();
    if (!token) return null;
    const res = await axios.get(`${API}/auth/me`, { timeout: 15000 });
    return saveUserSession(token, res.data);
  };

  const retrySession = () => {
    setAuthError(null);
    setLoading(true);
    setAuthNonce((n) => n + 1);
  };

  return (
    <AuthContext.Provider value={{ user, login, logout, loading, authError, retrySession, refreshUser }}>
      {children}
    </AuthContext.Provider>
  );
}

function ProtectedRoute({ children, permission, masterOnly = false, allowUser = false }) {
  const { user, loading, authError, retrySession } = useAuth();
  const location = useLocation();

  if (loading) {
    return (
      <div className="p-8 text-sm text-center" style={{ color: '#6B7280' }} data-testid="session-loading">
        Checking session…
      </div>
    );
  }

  if (authError) {
    return <SessionBootstrapError message={authError} onRetry={retrySession} />;
  }

  if (!user) {
    return <Navigate to="/login" replace state={{ from: location }} />;
  }

  if (masterOnly && user.role !== 'master') {
    return <Navigate to="/" replace />;
  }

  if (allowUser && user.role === 'user') {
    return children;
  }

  if (!canAccessPermission(user, permission)) {
    return <Navigate to="/" replace />;
  }

  return children;
}

/** Logged-in home: Analytics when allowed; otherwise first permitted module. */
function HomeEntry() {
  const { user, loading, authError, retrySession } = useAuth();

  if (loading) {
    return (
      <div className="p-8 text-sm text-center" style={{ color: '#6B7280' }} data-testid="session-loading">
        Checking session…
      </div>
    );
  }
  if (authError) {
    return <SessionBootstrapError message={authError} onRetry={retrySession} />;
  }
  if (!user) return <LoginPage />;

  if (canAccessPermission(user, 'Analytics')) {
    return <Navigate to="/analytics" replace />;
  }

  const first = getFirstAllowedMenuItem(user, canAccessMenuItem);
  if (first?.path) {
    return <Navigate to={first.path} replace />;
  }

  return (
    <div className="p-8 text-sm text-center" style={{ color: '#6B7280' }}>
      No modules are available for your account. Contact Master Admin.
    </div>
  );
}

/** Logged-out / is the existing LoginPage. Logged-in / enters the app. */
function PublicHome() {
  const { user, loading, authError, retrySession } = useAuth();
  if (authError) {
    return <SessionBootstrapError message={authError} onRetry={retrySession} />;
  }
  if (user) return <HomeEntry />;
  if (loading && getAuthToken()) {
    return (
      <div className="p-8 text-sm text-center" style={{ color: '#6B7280' }} data-testid="session-loading">
        Checking session…
      </div>
    );
  }
  return <LoginPage />;
}

function TestingBannerWithAuth() {
  const { user } = useAuth();
  return <TestingBanner user={user} />;
}

function InstallPromptGate() {
  const { user } = useAuth();
  return <InstallPrompt user={user} />;
}

function App() {
  return (
    <ProcessingProvider>
    <AuthProvider>
      <BrowserRouter>
        <LaunchScreen>
        {IS_TESTING_BUILD && <TestingBannerWithAuth />}
        <SessionInactivityGuard />
        <InstallPromptGate />
        <Toaster position="top-right" richColors />
        <div data-nmts-build={process.env.REACT_APP_BUILD_SHA || ''} hidden />
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route path="/" element={<PublicHome />} />
          <Route element={
            <ProtectedRoute>
              <DashboardLayout />
            </ProtectedRoute>
          }>
            {/* Keep-alive modules mount in DashboardLayout (display:none when inactive).
                Routes stay for permission gates + deep links; element is null to avoid double mount. */}
            <Route path="dashboard" element={
              <ProtectedRoute permission="Analytics">{null}</ProtectedRoute>
            } />
            <Route path="notice-board" element={
              <ProtectedRoute permission="Notice Board">{null}</ProtectedRoute>
            } />
            <Route path="products" element={
              <ProtectedRoute permission="Product Hub">{null}</ProtectedRoute>
            } />
            <Route path="product-history" element={
              <ProtectedRoute permission="Product Hub History">{null}</ProtectedRoute>
            } />
            <Route path="orders" element={
              <ProtectedRoute permission="Order Desk">{null}</ProtectedRoute>
            } />
            <Route path="order-history" element={
              <ProtectedRoute permission="Order Desk">{null}</ProtectedRoute>
            } />
            <Route path="upload" element={
              <ProtectedRoute permission="Upload Center">{null}</ProtectedRoute>
            } />
            <Route path="notifications" element={null} />
            <Route path="users" element={
              <ProtectedRoute permission="User Hub" allowUser>{null}</ProtectedRoute>
            } />
            <Route path="profile/:userId?" element={null} />
            <Route path="reports" element={
              <ProtectedRoute permission="Reports">{null}</ProtectedRoute>
            } />
            <Route path="analytics" element={
              <ProtectedRoute permission="Analytics">{null}</ProtectedRoute>
            } />
            <Route path="storage-cost-monitor" element={
              <ProtectedRoute permission="Storage & Cost Monitor" masterOnly>{null}</ProtectedRoute>
            } />
            <Route path="login-screen-manager" element={
              <ProtectedRoute masterOnly>{null}</ProtectedRoute>
            } />
            <Route path="requests" element={
              <ProtectedRoute permission="Request Center">{null}</ProtectedRoute>
            } />
            <Route path="sleeping-stock-mobile" element={<Navigate to="/users?section=mobile" replace />} />
            <Route path="stock-audit" element={
              <ProtectedRoute>{null}</ProtectedRoute>
            } />
            <Route path="nmts-mobile" element={<Navigate to="/users?section=mobile" replace />} />
            <Route path="query" element={
              <ProtectedRoute>{null}</ProtectedRoute>
            } />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Route>
        </Routes>
        </LaunchScreen>
      </BrowserRouter>
    </AuthProvider>
    </ProcessingProvider>
  );
}

export default App;
