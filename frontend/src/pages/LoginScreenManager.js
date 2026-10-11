import React, { useEffect, useState } from 'react';
import axios from 'axios';
import { API, useAuth } from '@/App';
import { isTestingUiEnabled } from '@/testingUi';
import { toast } from 'sonner';
import { Image as ImageIcon } from 'lucide-react';
import './LoginScreenManager.css';

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

  const uploadDefault = async (file, input) => {
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
      if (input) input.value = '';
    }
  };

  const uploadScheduled = async (file, input) => {
    if (!file) return;
    if (!startAt || !endAt) {
      toast.error('Select Start and End date/time in IST');
      if (input) input.value = '';
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
      if (input) input.value = '';
    }
  };

  const preview = (row) => row?.image_url ? `${API.replace(/\/api$/, '')}${row.image_url}` : null;

  return (
    <div className="nmts-login-screen-manager" data-testid="login-screen-manager">
      <header className="nmts-module-header">
        <div className="nmts-module-header-row">
          <ImageIcon className="h-5 w-5 nmts-module-header-icon" />
          <h1>Login Screen Manager</h1>
        </div>
        <p>Testing only. Dates and times are IST.</p>
      </header>

      <div className="lsm-grid">
        <section className="nmts-card lsm-card" data-testid="lsm-default-card">
          <h2>Default Image</h2>
          <div className="lsm-preview">
            {preview(state.default) ? (
              <img src={preview(state.default)} alt="Default login image" />
            ) : (
              <p className="lsm-empty">No Default Image yet.</p>
            )}
          </div>
          <label className="lsm-file" data-busy={busy ? 'true' : 'false'}>
            <span className="nmts-btn-primary lsm-file-btn">{busy ? 'Uploading…' : 'Upload Default Image'}</span>
            <input
              type="file"
              accept="image/png,image/jpeg,image/webp"
              disabled={busy}
              onChange={(e) => uploadDefault(e.target.files?.[0], e.target)}
            />
          </label>
        </section>

        <section className="nmts-card lsm-card" data-testid="lsm-scheduled-card">
          <h2>Scheduled Image</h2>
          <div className="lsm-preview">
            {preview(state.scheduled) ? (
              <img src={preview(state.scheduled)} alt="Scheduled login image" />
            ) : (
              <p className="lsm-empty">No Scheduled Image.</p>
            )}
          </div>
          <label className="lsm-field">Start Date/Time (IST)
            <input className="lsm-input" type="datetime-local" value={startAt} onChange={(e) => setStartAt(e.target.value)} />
          </label>
          <label className="lsm-field">End Date/Time (IST)
            <input className="lsm-input" type="datetime-local" value={endAt} onChange={(e) => setEndAt(e.target.value)} />
          </label>
          <label className="lsm-file" data-busy={busy ? 'true' : 'false'}>
            <span className="nmts-btn-primary lsm-file-btn">{busy ? 'Uploading…' : 'Upload Scheduled Image'}</span>
            <input
              type="file"
              accept="image/png,image/jpeg,image/webp"
              disabled={busy}
              onChange={(e) => uploadScheduled(e.target.files?.[0], e.target)}
            />
          </label>
        </section>
      </div>
    </div>
  );
}
