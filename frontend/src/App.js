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

const AuthContext = createContext(null);

export const MENU_PERMISSIONS = {
  '/': 'Notice Board',
  '/notice-board': 'Notice Board',
  '/users': 'User Hub',
  '/upload': 'Upload Center',
  '/products': 'Product Hub',
  '/product-history': 'Product Hub History',
  '/orders': 'Order Desk',
  '/requests': 'Request Center',
  '/reports': 'Reports',
  '/analytics': 'Analytics',
  '/query': null, // Available to master, admin, and user (Query Desk).
  '/sleeping-stock-mobile': null, // Available to master, admin and user; API applies scope rules.
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

export const useAuth = () => {
  const context = useContext(AuthContext);
  if (!context) throw new Error('useAuth must be used within AuthProvider');
  return context;
};

const BACKEND_URL = process.env.REACT_APP_BACKEND_URL || 'http://127.0.0.1:8000';
export const API = `${BACKEND_URL}/api`;

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
    setUser(cleanUser);
    return cleanUser;
  };

  useEffect(() => {
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
          saveUserSession(token, res.data);
        })
        .catch(() => {
          localStorage.removeItem('token');
          localStorage.removeItem('user');
          localStorage.removeItem('permissions');
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
    localStorage.removeItem('token');
    localStorage.removeItem('user');
    localStorage.removeItem('permissions');
    setUser(null);
  };

  return (
    <AuthContext.Provider value={{ user, login, logout, loading }}>
      {children}
    </AuthContext.Provider>
  );
}

function ProtectedRoute({ children, permission }) {
  const { user, loading } = useAuth();
  const location = useLocation();

  if (loading) return null;

  if (!user) {
    return <Navigate to="/login" replace state={{ from: location }} />;
  }

  if (!canAccessPermission(user, permission)) {
    return <Navigate to="/" replace />;
  }

  return children;
}

function App() {
  return (
    <ProcessingProvider>
    <AuthProvider>
      <BrowserRouter>
        <Toaster position="top-right" richColors />
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route path="/" element={
            <ProtectedRoute>
              <DashboardLayout />
            </ProtectedRoute>
          }>
            <Route index element={
              <ProtectedRoute permission="Notice Board">
                <NoticeBoard />
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
