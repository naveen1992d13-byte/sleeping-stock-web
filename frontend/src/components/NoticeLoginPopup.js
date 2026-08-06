import React, { useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import axios from 'axios';
import { API, useAuth } from '@/App';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';

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
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>{notice.subject}</DialogTitle>
        </DialogHeader>
        <div className="space-y-2 text-sm">
          <p className="text-slate-600 line-clamp-4">{notice.content}</p>
          <p><span className="font-medium">Type:</span> {notice.notice_type}</p>
          <p><span className="font-medium">Priority:</span> {notice.priority}</p>
          {extras > 1 && (
            <p className="text-xs text-amber-700">{extras - 1} more active notice(s) available on Notice Board.</p>
          )}
        </div>
        <div className="flex flex-wrap gap-2 pt-2">
          <Button onClick={viewNotice}>View Notice</Button>
          {notice.priority !== 'Urgent' || !notice.acknowledgement_required ? (
            <Button variant="outline" onClick={remindLater}>Remind Me Later</Button>
          ) : null}
          <Button variant="ghost" onClick={() => setOpen(false)}>Close</Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
