import React, { createContext, useState, useContext, useEffect } from 'react';
import { BrowserRouter, Routes, Route, Navigate, useLocation } from 'react-router-dom';
import axios from 'axios';
import { Toaster } from 'sonner';
import './App.css';
import { ProcessingProvider } from './components/GlobalProcessingOverlay';

// Import pages
import { LoginPage } from './pages/LoginPage';
import { DashboardLayout } from './pages/DashboardLayout';
import NoticeBoard from './pages/NoticeBoard';
import { Products } from './pages/Products';
import { ProductHubHistory } from './pages/ProductHubHistory';
import { Orders } from './pages/Orders';
import UploadCenter from "./pages/Upload";
import { Notifications } from './pages/Notifications';
import Users from './pages/Users';
import { Profile } from './pages/Profile';
import { Reports } from './pages/Reports';
import { Analytics } from './pages/Analytics';
import { Requests } from './pages/Requests';
import { QueryDesk } from './pages/QueryDesk';
import NMTSMobile from './pages/NMTSMobile';
import { OrderHistory } from './pages/OrderHistory';
import StockAudit from './pages/StockAudit';
import { StorageCostMonitor } from './pages/StorageCostMonitor';
import { SessionInactivityGuard } from './components/SessionInactivityGuard';
import {
  touchSessionActivity,
  clearSessionActivity,
  isSessionExpiredByInactivity,
  clearAuthStorage,
} from './utils/sessionActivity';
import { resolveApiUrl, resolveBackendUrl } from '@/backendUrl';
import { getFirstAllowedMenuItem } from './config/menuConfig';

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

export const canAccessPermission = (user, permissionName) => {
  if (!user) return false;
  if (user.role === 'master') return true;
  if (!permissionName) return true;
  return normalizePermissions(user.permissions).includes(permissionName);
};

export const canAccessMenuItem = (user, item) => {
  if (!user || !item) return false;
  if (item.masterOnly) return user.role === 'master';
  if (item.allRoles) return true;
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
      axios.get(`${API}/auth/me`)
        .then((res) => {
          touchSessionActivity();
          saveUserSession(token, res.data);
        })
        .catch(() => {
          clearAuthStorage();
          setUser(null);
        })
        .finally(() => setLoading(false));
    } else {
      setLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

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
  };

  return (
    <AuthContext.Provider value={{ user, login, logout, loading }}>
      {children}
    </AuthContext.Provider>
  );
}

function ProtectedRoute({ children, permission, masterOnly = false }) {
  const { user, loading } = useAuth();
  const location = useLocation();

  if (loading) return null;

  if (!user) {
    return <Navigate to="/login" replace state={{ from: location }} />;
  }

  if (masterOnly && user.role !== 'master') {
    return <Navigate to="/" replace />;
  }

  if (!canAccessPermission(user, permission)) {
    return <Navigate to="/" replace />;
  }

  return children;
}

/** Default home: Analytics/Dashboard when allowed; otherwise first permitted module. */
function HomeEntry() {
  const { user, loading } = useAuth();

  if (loading) return null;
  if (!user) return <Navigate to="/login" replace />;

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

function App() {
  return (
    <ProcessingProvider>
    <AuthProvider>
      <BrowserRouter>
        <SessionInactivityGuard />
        <Toaster position="top-right" richColors />
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route path="/" element={
            <ProtectedRoute>
              <DashboardLayout />
            </ProtectedRoute>
          }>
            <Route index element={<HomeEntry />} />
            <Route path="dashboard" element={
              <ProtectedRoute permission="Analytics">
                <Analytics />
              </ProtectedRoute>
            } />
            <Route path="notice-board" element={
              <ProtectedRoute permission="Notice Board">
                <NoticeBoard />
              </ProtectedRoute>
            } />
            <Route path="products" element={
              <ProtectedRoute permission="Product Hub">
                <Products />
              </ProtectedRoute>
            } />
            <Route path="product-history" element={
              <ProtectedRoute permission="Product Hub History">
                <ProductHubHistory />
              </ProtectedRoute>
            } />
            <Route path="orders" element={
              <ProtectedRoute permission="Order Desk">
                <Orders />
              </ProtectedRoute>
            } />
            <Route path="order-history" element={
              <ProtectedRoute permission="Order Desk">
                <OrderHistory />
              </ProtectedRoute>
            } />
            <Route path="upload" element={
              <ProtectedRoute permission="Upload Center">
                <UploadCenter />
              </ProtectedRoute>
            } />
            <Route path="notifications" element={<Notifications />} />
            <Route path="users" element={
              <ProtectedRoute permission="User Hub">
                <Users />
              </ProtectedRoute>
            } />
            <Route path="profile/:userId?" element={<Profile />} />
            <Route path="reports" element={
              <ProtectedRoute permission="Reports">
                <Reports />
              </ProtectedRoute>
            } />
            <Route path="analytics" element={
              <ProtectedRoute permission="Analytics">
                <Analytics />
              </ProtectedRoute>
            } />
            <Route path="storage-cost-monitor" element={
              <ProtectedRoute permission="Storage & Cost Monitor" masterOnly>
                <StorageCostMonitor />
              </ProtectedRoute>
            } />
            <Route path="requests" element={
              <ProtectedRoute permission="Request Center">
                <Requests />
              </ProtectedRoute>
            } />
            <Route path="sleeping-stock-mobile" element={
              <ProtectedRoute>
                <NMTSMobile />
              </ProtectedRoute>
            } />
            <Route path="stock-audit" element={
              <ProtectedRoute>
                <StockAudit />
              </ProtectedRoute>
            } />
            <Route path="nmts-mobile" element={<Navigate to="/sleeping-stock-mobile" replace />} />
            <Route path="query" element={
              <ProtectedRoute>
                <QueryDesk />
              </ProtectedRoute>
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
