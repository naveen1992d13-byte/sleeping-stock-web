import { useEffect } from 'react';
import { useNavigate, useLocation } from 'react-router-dom';
import { useAuth } from '../App';
import { toast } from 'sonner';
import {
  touchSessionActivity,
  isSessionExpiredByInactivity,
  clearAuthStorage,
  NMTS_SESSION_TIMEOUT_MS,
} from '../utils/sessionActivity';

const ACTIVITY_EVENTS = ['mousedown', 'keydown', 'scroll', 'touchstart', 'click'];

/**
 * Resets inactivity timer on user activity; logs out after 30 minutes idle.
 */
export function SessionInactivityGuard() {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();

  useEffect(() => {
    if (!user) return undefined;

    touchSessionActivity();

    const onActivity = () => {
      touchSessionActivity();
    };

    ACTIVITY_EVENTS.forEach((ev) => window.addEventListener(ev, onActivity, { passive: true }));

    const interval = window.setInterval(() => {
      if (isSessionExpiredByInactivity()) {
        logout();
        clearAuthStorage();
        navigate('/login', { replace: true });
        toast.info('Session expired due to inactivity. Please sign in again.');
      }
    }, 60 * 1000);

    return () => {
      ACTIVITY_EVENTS.forEach((ev) => window.removeEventListener(ev, onActivity));
      window.clearInterval(interval);
    };
  }, [user, logout, navigate, location.pathname]);

  return null;
}

export { NMTS_SESSION_TIMEOUT_MS };
