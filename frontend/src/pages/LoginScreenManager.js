import React, { useEffect, useState } from 'react';
import axios from 'axios';
import { API, useAuth } from '@/App';
import { isTestingUiEnabled } from '@/testingUi';
import { toast } from 'sonner';
import { Image as ImageIcon } from 'lucide-react';

function toIstValue(iso) {
  if (!iso) return '';
  const dt = new Date(iso);
  if (Number.isNaN(dt.getTime())) return '';
  const parts = new Intl.DateTimeFormat('en-GB', {
    timeZone: 'Asia/Kolkata',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hourCycle: 'h23',
  }).formatToParts(dt);
  const get = (type) => parts.find((p) => p.type === type)?.value || '';
  return `${get('year')}-${get('month')}-${get('day')}T${get('hour')}:${get('minute')}`;
}

export function LoginScreenManager() {
  const { user } = useAuth();
  const [state, setState] = useState({ default: null, scheduled: null });
  const [busy, setBusy] = useState(false);
  const [startAt, setStartAt] = useState('');
  const [endAt, setEndAt] = useState('');

  const load = async () => {
    const res = await axios.get(`${API}/login-screen/manage`);
    setState(res.data || { default: null, scheduled: null });
    if (res.data?.scheduled?.start_at) setStartAt(toIstValue(res.data.scheduled.start_at));
    if (res.data?.scheduled?.end_at) setEndAt(toIstValue(res.data.scheduled.end_at));
  };

  useEffect(() => {
    if (!isTestingUiEnabled() || user?.role !== 'master') return;
    load().catch(() => toast.error('Could not load Login Screen images'));
  }, [user?.role]);

  if (!isTestingUiEnabled() || user?.role !== 'master') {
    return <div className="p-6 text-sm text-gray-500">Master Admin only in Testing.</div>;
  }

  const uploadDefault = async (file) => {
    if (!file) return;
    const form = new FormData();
    form.append('file', file);
    setBusy(true);
    try {
      await axios.post(`${API}/login-screen/default`, form);
      toast.success('Default Image updated');
      await load();
    } catch (error) {
      toast.error(error.response?.data?.detail || 'Default upload failed');
    } finally {
      setBusy(false);
    }
  };

  const uploadScheduled = async (file) => {
    if (!file) return;
    if (!startAt || !endAt) {
      toast.error('Select Start and End date/time in IST');
      return;
    }
    const form = new FormData();
    form.append('file', file);
    form.append('start_at', `${startAt}:00+05:30`);
    form.append('end_at', `${endAt}:00+05:30`);
    setBusy(true);
    try {
      await axios.post(`${API}/login-screen/scheduled`, form);
      toast.success('Scheduled Image saved');
      await load();
    } catch (error) {
      toast.error(error.response?.data?.detail || 'Scheduled upload failed');
    } finally {
      setBusy(false);
    }
  };

  const preview = (row) => row?.image_url ? `${API.replace(/\/api$/, '')}${row.image_url}` : null;

  return (
    <div className="p-6 max-w-3xl mx-auto space-y-6" data-testid="login-screen-manager">
      <div className="flex items-center gap-2">
        <ImageIcon className="h-5 w-5" />
        <h1 className="text-xl font-semibold">Login Screen Manager</h1>
      </div>
      <p className="text-sm text-gray-500">Testing only. Dates and times are IST.</p>

      <section className="rounded-xl border bg-white p-4 space-y-3">
        <h2 className="font-semibold">1. Default Image</h2>
        {preview(state.default) ? (
          <img src={preview(state.default)} alt="Default login image" style={{ maxWidth: '100%', height: 'auto', objectFit: 'contain' }} />
        ) : (
          <p className="text-sm text-gray-500">No Default Image yet.</p>
        )}
        <input
          type="file"
          accept="image/png,image/jpeg,image/webp"
          disabled={busy}
          onChange={(e) => uploadDefault(e.target.files?.[0])}
        />
      </section>

      <section className="rounded-xl border bg-white p-4 space-y-3">
        <h2 className="font-semibold">2. Scheduled Image</h2>
        {preview(state.scheduled) ? (
          <img src={preview(state.scheduled)} alt="Scheduled login image" style={{ maxWidth: '100%', height: 'auto', objectFit: 'contain' }} />
        ) : (
          <p className="text-sm text-gray-500">No Scheduled Image.</p>
        )}
        <label className="block text-sm">Start Date/Time (IST)
          <input className="mt-1 block border rounded px-2 py-1" type="datetime-local" value={startAt} onChange={(e) => setStartAt(e.target.value)} />
        </label>
        <label className="block text-sm">End Date/Time (IST)
          <input className="mt-1 block border rounded px-2 py-1" type="datetime-local" value={endAt} onChange={(e) => setEndAt(e.target.value)} />
        </label>
        <input
          type="file"
          accept="image/png,image/jpeg,image/webp"
          disabled={busy}
          onChange={(e) => uploadScheduled(e.target.files?.[0])}
        />
      </section>
    </div>
  );
}
