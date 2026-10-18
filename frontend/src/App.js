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
  clearSessionActivity,
  isSessionExpiredByInactivity,
  clearAuthStorage,
} from './utils/sessionActivity';
import { resolveApiUrl, resolveBackendUrl } from '@/backendUrl';
import { getFirstAllowedMenuItem } from './config/menuConfig';
import { isTestingUiEnabled } from './testingUi';
import { TestingBanner } from './components/TestingBanner';

const AuthContext = createContext(null);

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

export const isMasterLevelRole = (role) =>
  role === 'master_admin' || role === 'master';

export const isMasterAdminRole = (role) => role === 'master_admin';

export const ROLE_LABELS = {
  master_admin: 'Master Admin',
  master: 'Master',
  admin: 'Admin',
  user: 'User',
};

export const roleLabel = (role) => ROLE_LABELS[role] || String(role || 'User');

/** Roles the actor may create (exact hierarchy matrix; never includes Master Admin). */
export const creatableRolesFor = (actorRole) => {
  if (actorRole === 'master_admin') return ['master', 'admin', 'user'];
  if (actorRole === 'master') return ['admin', 'user'];
  if (actorRole === 'admin') return ['user'];
  return [];
};

export const isProtectedMasterAdminUser = (row) => {
  if (!row) return false;
  if (row.role === 'master_admin') return true;
  const email = String(row.email || '').trim().toLowerCase();
  if (email === 'admin@sleepingstock.in' || email === 'testing.master@sleepingstock.in') return true;
  if (row.is_testing_master) return true;
  return false;
};

/** Production Master Admin mirrored into Testing (read-only base row). */
export const isMirroredProductionMasterAdmin = (row) => {
  if (!row) return false;
  const email = String(row.email || '').trim().toLowerCase();
  if (email !== 'admin@sleepingstock.in') return false;
  const origin = String(row.data_origin || '').trim().toLowerCase();
  return origin === '' || origin === 'production' || origin === 'snapshot';
};

export const canAccessPermission = (user, permissionName) => {
  if (!user) return false;
  if (isMasterLevelRole(user.role)) return true;
  if (!permissionName) return true;
  return normalizePermissions(user.permissions).includes(permissionName);
};

export const canAccessMenuItem = (user, item) => {
  if (!user || !item) return false;
  if (item.testingOnly && !isTestingUiEnabled()) return false;
  if (item.masterOnly) return isMasterLevelRole(user.role);
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
  const token = localStorage.getItem('token');
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

function AuthProvider({ children }) {
  const [user, setUser] = useState(null);
  const [loading, setLoading] = useState(true);

  const saveUserSession = (token, loggedUser) => {
    const cleanUser = {
      ...loggedUser,
      permissions: normalizePermissions(loggedUser?.permissions),
    };

    localStorage.setItem('token', token);
    localStorage.setItem('user', JSON.stringify(cleanUser));
    localStorage.setItem('permissions', JSON.stringify(cleanUser.permissions));
    touchSessionActivity();
    setUser(cleanUser);
    if (typeof console !== 'undefined') {
      console.info('[nmts-auth] session saved', { role: cleanUser.role, email: cleanUser.email });
    }
    return cleanUser;
  };

  useEffect(() => {
    if (isSessionExpiredByInactivity()) {
      clearAuthStorage();
      setUser(null);
      setLoading(false);
      return;
    }

    const token = localStorage.getItem('token');
    const storedUser = localStorage.getItem('user');

    if (storedUser) {
      try {
        const parsedUser = JSON.parse(storedUser);
        setUser({ ...parsedUser, permissions: normalizePermissions(parsedUser.permissions) });
      } catch {
        localStorage.removeItem('user');
        localStorage.removeItem('permissions');
      }
    }

    if (token) {
      // Only clear the session on definitive auth failures. Transient 502/timeouts
      // during Testing deploys must not wipe the user and empty Hub/Analytics.
      axios.get(`${API}/auth/me`, { timeout: 20000 })
        .then((res) => {
          touchSessionActivity();
          saveUserSession(token, res.data);
        })
        .catch((err) => {
          const status = err?.response?.status;
          console.warn('[nmts-auth] /auth/me failed', { status, message: err?.message });
          if (status === 401 || status === 403) {
            clearAuthStorage();
            setUser(null);
          }
        })
        .finally(() => setLoading(false));
    } else {
      setLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const onPageShow = (event) => {
      if (event.persisted) {
        console.info('[nmts-auth] App pageshow bfcache → reload');
        window.location.reload();
      }
    };
    window.addEventListener('pageshow', onPageShow);
    return () => window.removeEventListener('pageshow', onPageShow);
  }, []);

  const login = async (identifier, password) => {
    const payload = {
      email: String(identifier || '').trim(),
      password,
    };
    // Retry transient gateway/upstream failures (common on the 1Gi Testing host
    // when Chromium + uvicorn compete for RAM). Never retry definitive 401/403.
    let lastError = null;
    for (let attempt = 1; attempt <= 3; attempt += 1) {
      try {
        const response = await axios.post(
          `${API}/auth/login`,
          payload,
          { timeout: 30000, forceGlobalProcessing: true }
        );
        return saveUserSession(response.data.access_token, response.data.user);
      } catch (err) {
        lastError = err;
        const status = err?.response?.status;
        const transient = !status || status === 502 || status === 503 || status === 504
          || err?.code === 'ECONNABORTED' || err?.code === 'ERR_NETWORK';
        console.warn('[nmts-auth] login attempt failed', { attempt, status, transient });
        if (!transient || attempt === 3) break;
        await new Promise((r) => setTimeout(r, 700 * attempt));
      }
    }
    throw lastError;
  };

  const logout = () => {
    console.info('[nmts-auth] logout');
    clearAuthStorage();
    setUser(null);
  };

  const refreshUser = async () => {
    const token = localStorage.getItem('token');
    if (!token) return null;
    const res = await axios.get(`${API}/auth/me`);
    return saveUserSession(token, res.data);
  };

  return (
    <AuthContext.Provider value={{ user, login, logout, loading, refreshUser }}>
      {children}
    </AuthContext.Provider>
  );
}

function ProtectedRoute({ children, permission, masterOnly = false, allowUser = false }) {
  const { user, loading } = useAuth();
  const location = useLocation();

  if (loading) return null;

  if (!user) {
    return <Navigate to="/login" replace state={{ from: location }} />;
  }

  if (masterOnly && !isMasterLevelRole(user.role)) {
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
  const { user, loading } = useAuth();

  if (loading) return null;
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
  const { user, loading } = useAuth();
  if (user) return <HomeEntry />;
  if (loading && typeof localStorage !== 'undefined' && localStorage.getItem('token')) {
    return null;
  }
  return <LoginPage />;
}

function TestingBannerWithAuth() {
  const { user } = useAuth();
  return <TestingBanner user={user} />;
}

function App() {
  return (
    <ProcessingProvider>
    <AuthProvider>
      <BrowserRouter>
        <TestingBannerWithAuth />
        <SessionInactivityGuard />
        <Toaster position="top-right" richColors />
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
      </BrowserRouter>
    </AuthProvider>
    </ProcessingProvider>
  );
}

export default App;
