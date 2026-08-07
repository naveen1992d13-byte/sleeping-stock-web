import React, { useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import axios from 'axios';
import { API, useAuth } from '@/App';
import { Button } from '@/components/ui/button';
import { NmtsModal } from '@/components/NmtsModal';

function priorityBadgeClass(priority) {
  const p = String(priority || 'Normal');
  if (p === 'Urgent') return 'nmts-priority-badge nmts-priority-urgent';
  if (p === 'Important') return 'nmts-priority-badge nmts-priority-important';
  return 'nmts-priority-badge nmts-priority-normal';
}

/**
 * Login popup for Admin/User — fetched once per layout mount (IST daily rules on backend).
 */
export function NoticeLoginPopup() {
  const { user } = useAuth();
  const navigate = useNavigate();
  const fetched = useRef(false);
  const [open, setOpen] = useState(false);
  const [data, setData] = useState(null);

  useEffect(() => {
    if (!user || user.role === 'master' || fetched.current) return;
    const sessionKey = `notice_popup_fetched_${user.id}`;
    if (sessionStorage.getItem(sessionKey)) return;

    fetched.current = true;
    sessionStorage.setItem(sessionKey, '1');

    axios
      .get(`${API}/notice-board/popups`)
      .then((res) => {
        const primary = res.data?.primary;
        if (primary) {
          setData(res.data);
          setOpen(true);
        }
      })
      .catch(() => {});
  }, [user]);

  if (!data?.primary) return null;

  const notice = data.primary;
  const extras = (data.notices || []).length;

  const remindLater = async () => {
    try {
      await axios.post(`${API}/notice-board/popups/dismiss`, { notice_id: notice.id });
    } catch {
      /* ignore */
    }
    setOpen(false);
  };

  const viewNotice = () => {
    setOpen(false);
    navigate(`/notice-board?notice=${notice.id}`);
  };

  return (
    <NmtsModal
      open={open}
      onClose={() => setOpen(false)}
      title={notice.subject}
      maxWidth="max-w-lg"
    >
      <div className="nmts-notice-login-panel -mx-4 -mt-2 px-1 pb-1">
        <div className="flex items-start gap-4 mb-4">
          <img
            src="/sleeping-stock-logo.png"
            alt="Sleeping Stock"
            className="h-14 w-14 object-contain shrink-0"
            onError={(e) => {
              e.currentTarget.style.display = 'none';
            }}
          />
          <div className="min-w-0 flex-1">
            <p className="text-xs font-semibold uppercase tracking-wide text-emerald-700 mb-1">
              Notice from NMTS
            </p>
            <div className="mt-2 flex flex-wrap items-center gap-2 text-xs">
              <span className="rounded-md border border-slate-200 bg-slate-50 px-2 py-0.5 text-slate-700">
                {notice.notice_type}
              </span>
              <span className={priorityBadgeClass(notice.priority)}>{notice.priority}</span>
            </div>
          </div>
        </div>
        <p className="text-sm text-slate-600 leading-relaxed line-clamp-5 whitespace-pre-wrap">
          {notice.content}
        </p>
        {extras > 1 && (
          <p className="mt-3 text-xs text-amber-800 bg-amber-50 border border-amber-100 rounded-lg px-3 py-2">
            {extras - 1} more active notice(s) available on Notice Board.
          </p>
        )}
        <div className="flex flex-wrap gap-2 pt-5 border-t border-slate-100 mt-5">
          <Button type="button" className="nmts-btn-primary" onClick={viewNotice}>
            View Notice
          </Button>
          {notice.priority !== 'Urgent' || !notice.acknowledgement_required ? (
            <Button type="button" variant="outline" onClick={remindLater}>
              Remind Me Later
            </Button>
          ) : null}
          <Button type="button" variant="ghost" onClick={() => setOpen(false)}>
            Close
          </Button>
        </div>
      </div>
    </NmtsModal>
  );
}
