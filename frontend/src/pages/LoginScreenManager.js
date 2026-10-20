import React, { useEffect, useState } from 'react';
import axios from 'axios';
import { API, useAuth } from '@/App';
import { toast } from 'sonner';
import { Image as ImageIcon } from 'lucide-react';
import { LoginImageCropper } from './LoginImageCropper';
import {
  LOGIN_MEDIA_ACCEPT,
  LOGIN_PROMO_ALT_HEIGHT,
  LOGIN_PROMO_ALT_WIDTH,
  LOGIN_PROMO_ASPECT_LABEL,
  LOGIN_PROMO_OUTPUT_HEIGHT,
  LOGIN_PROMO_OUTPUT_WIDTH,
  isLoginVideoFile,
  loginDisplayStyle,
  validateLoginImageFile,
  validateLoginVideoFile,
} from './loginPromoCrop';
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

function mediaSrc(row, kind) {
  const path = kind === 'poster' ? (row?.poster_url || row?.image_url) : (row?.media_url || row?.image_url);
  return path ? `${API.replace(/\/api$/, '')}${path}` : null;
}

function hasMedia(row) {
  return Boolean(row && (row.media_url || row.image_url));
}

function apiDetailMessage(error, fallback) {
  const status = error?.response?.status;
  if (status === 502 || status === 503 || error?.code === 'ERR_NETWORK') {
    return 'Backend is unavailable. Retry once the service is healthy.';
  }
  const detail = error?.response?.data?.detail;
  if (typeof detail === 'string' && detail.trim()) return detail;
  if (Array.isArray(detail) && detail.length) {
    return detail.map((item) => item?.msg || item?.detail || String(item)).filter(Boolean).join('; ') || fallback;
  }
  if (detail && typeof detail === 'object' && (detail.message || detail.msg)) {
    return detail.message || detail.msg;
  }
  return fallback;
}

function PreviewMedia({ row, alt }) {
  const kind = row?.media_kind === 'video' ? 'video' : 'image';
  const src = mediaSrc(row, 'media');
  const poster = mediaSrc(row, 'poster');
  const blurSrc = poster || src;
  const style = loginDisplayStyle(row?.display_settings);
  if (!src) return <p className="lsm-empty">No media yet.</p>;
  return (
    <div className="lsm-preview-frame">
      {blurSrc && (
        <div className="lsm-media-blur" aria-hidden="true">
          <img src={blurSrc} alt="" />
        </div>
      )}
      <div className="lsm-preview-fg">
        {kind === 'video' ? (
          <video
            src={src}
            poster={poster || undefined}
            autoPlay
            muted
            loop
            playsInline
            controls={false}
            disablePictureInPicture
            style={style}
          />
        ) : (
          <img src={src} alt={alt} style={style} />
        )}
      </div>
    </div>
  );
}

export function LoginScreenManager() {
  const { user } = useAuth();
  const [state, setState] = useState({ default: null, scheduled: null });
  const [busy, setBusy] = useState(false);
  const [startAt, setStartAt] = useState('');
  const [endAt, setEndAt] = useState('');
  const [cropJob, setCropJob] = useState(null);

  const load = async () => {
    let manage = null;
    let active = null;
    let manageError = null;
    try {
      const res = await axios.get(`${API}/login-screen/manage`);
      manage = res.data || {};
    } catch (error) {
      manageError = error;
    }
    try {
      const res = await axios.get(`${API}/login-screen/active`);
      active = res.data || null;
    } catch {
      active = manage?.active || null;
    }
    const activeRow = hasMedia(manage?.active) ? manage.active : (hasMedia(active) ? active : null);
    const defaultRow = hasMedia(manage?.default)
      ? manage.default
      : (activeRow?.type === 'default' ? activeRow : manage?.default);
    const scheduledRow = hasMedia(manage?.scheduled)
      ? manage.scheduled
      : (activeRow?.type === 'scheduled' ? activeRow : manage?.scheduled);
    setState({
      default: defaultRow || null,
      scheduled: scheduledRow || null,
      active: activeRow,
    });
    const scheduledTimes = scheduledRow || manage?.scheduled;
    if (scheduledTimes?.start_at) setStartAt(toIstValue(scheduledTimes.start_at));
    if (scheduledTimes?.end_at) setEndAt(toIstValue(scheduledTimes.end_at));
    if (manageError && !hasMedia(activeRow) && !hasMedia(defaultRow) && !hasMedia(scheduledRow)) {
      throw manageError;
    }
  };

  useEffect(() => {
    if (user?.role !== 'master') return;
    load().catch((error) => toast.error(apiDetailMessage(error, 'Could not load Login Screen media')));
  }, [user?.role]);

  if (user?.role !== 'master') {
    return <div className="p-6 text-sm text-gray-500">Master Admin only.</div>;
  }

  const closeCrop = () => {
    if (cropJob?.input) cropJob.input.value = '';
    setCropJob(null);
  };

  const chooseFile = async (kind, file, input) => {
    if (!file) return;
    if (kind === 'scheduled' && (!startAt || !endAt)) {
      toast.error('Select Start and End date/time in IST');type="date" value={(starttype="time" value={(startAt.split('T')[1] || '').slice(0type="date" value={(endAt.split('T')[0] || '')} onCltype="time" value={(endAt.split('T')[1] || '').slice(0,5)} onClick={(e) => e.currentTarget.showPicker?.()} onChangeick={(e) => e.currentTarget.showPicker?.()} onChange,5)} onClick={(e) => e.currentTarget.showPicker?.()} onChangeAt.split('T')[0] || '')} onClick={(e) => e.currentTarget.showPicker?.()} onChange
      if (input) input.value = '';
      return;
    }
    if (isLoginVideoFile(file)) {
      const error = await validateLoginVideoFile(file);
      if (error) {
        toast.error(error);
        if (input) input.value = '';
        return;
      }
      setCropJob({ kind, file, input, mediaKind: 'video' });
      return;
    }
    const imageError = validateLoginImageFile(file);
    if (imageError) {
      toast.error(imageError);
      if (input) input.value = '';
      return;
    }
    setCropJob({ kind, file, input, mediaKind: 'image' });
  };

  const uploadDefault = async (file, input, posterFile, displaySettings) => {
    if (!file) return;
    const form = new FormData();
    form.append('file', file);
    if (posterFile) form.append('poster', posterFile);
    if (displaySettings) form.append('display_settings', JSON.stringify(displaySettings));
    setBusy(true);
    try {
      await axios.post(`${API}/login-screen/default`, form);
      toast.success('Default media updated');
      await load();
    } catch (error) {
      toast.error(error.response?.data?.detail || 'Default upload failed');
    } finally {
      setBusy(false);
      if (input) input.value = '';
    }
  };

  const uploadScheduled = async (file, input, posterFile, displaySettings) => {
    if (!file) return;
    if (!startAt || !endAt) {
      toast.error('Select Start and End date/time in IST');
      if (input) input.value = '';
      return;
    }
    const form = new FormData();
    form.append('file', file);
    if (posterFile) form.append('poster', posterFile);
    if (displaySettings) form.append('display_settings', JSON.stringify(displaySettings));
    form.append('start_at', `${startAt}:00+05:30`);
    form.append('end_at', `${endAt}:00+05:30`);
    setBusy(true);
    try {
      await axios.post(`${API}/login-screen/scheduled`, form);
      toast.success('Scheduled media saved');
      await load();
    } catch (error) {
      toast.error(error.response?.data?.detail || 'Scheduled upload failed');
    } finally {
      setBusy(false);
      if (input) input.value = '';
    }
  };

  const confirmCrop = ({ file, posterFile, displaySettings } = {}) => {
    const job = cropJob;
    setCropJob(null);
    if (!job || !file) return;
    if (job.kind === 'scheduled') {
      uploadScheduled(file, job.input, posterFile, displaySettings);
    } else {
      uploadDefault(file, job.input, posterFile, displaySettings);
    }
  };

  return (
    <div className="nmts-login-screen-manager" data-testid="login-screen-manager">
      <header className="nmts-module-header">
        <div className="nmts-module-header-row">
          <ImageIcon className="h-5 w-5 nmts-module-header-icon" />
          <h1>Login Screen Manager</h1>
        </div>
        <p>Images: JPG, PNG, WebP. Videos: MP4 H.264/AAC, max 30 seconds / 20 MB. Recommended aspect ratio {LOGIN_PROMO_ASPECT_LABEL}. Recommended output {LOGIN_PROMO_OUTPUT_WIDTH}×{LOGIN_PROMO_OUTPUT_HEIGHT} (alternative {LOGIN_PROMO_ALT_WIDTH}×{LOGIN_PROMO_ALT_HEIGHT}). Crop, zoom, and reposition are saved for the login display. Dates and times are IST.</p>
      </header>

      <div className="lsm-grid">
        <section className="nmts-card lsm-card" data-testid="lsm-default-card">
          <h2>Default Media</h2>
          <div className="lsm-preview">
            {mediaSrc(state.default, 'media') ? (
              <PreviewMedia row={state.default} alt="Default login media" />
            ) : (
              <p className="lsm-empty">No Default Media yet.</p>
            )}
          </div>
          <label className="lsm-file" data-busy={busy ? 'true' : 'false'}>
            <span className="nmts-btn-primary lsm-file-btn">{busy ? 'Uploading…' : 'Upload Default Media'}</span>
            <input
              type="file"
              accept={LOGIN_MEDIA_ACCEPT}
              disabled={busy}
              onChange={(e) => chooseFile('default', e.target.files?.[0], e.target)}
            />
          </label>
        </section>

        <section className="nmts-card lsm-card" data-testid="lsm-scheduled-card">
          <h2>Scheduled Media</h2>
          <div className="lsm-preview">
            {mediaSrc(state.scheduled, 'media') ? (
              <PreviewMedia row={state.scheduled} alt="Scheduled login media" />
            ) : (
              <p className="lsm-empty">No Scheduled Media.</p>
            )}
          </div>
          <label className="lsm-field">Start Date (IST)
            <input className="lsm-input" type="date" value={(startAt.split('T')[0] || '')} onChange={(e) => setStartAt(`${e.target.value}T${(startAt.split('T')[1] || '00:00').slice(0,5)}`)} />
          </label>
          <label className="lsm-field">Start Time (IST)
            <input className="lsm-input" type="time" value={(startAt.split('T')[1] || '').slice(0,5)} onChange={(e) => setStartAt(`${startAt.split('T')[0] || ''}T${e.target.value}`)} />
          </label>
          <label className="lsm-field">End Date (IST)
            <input className="lsm-input" type="date" value={(endAt.split('T')[0] || '')} onChange={(e) => setEndAt(`${e.target.value}T${(endAt.split('T')[1] || '00:00').slice(0,5)}`)} />
          </label>
          <label className="lsm-field">End Time (IST)
            <input className="lsm-input" type="time" value={(endAt.split('T')[1] || '').slice(0,5)} onChange={(e) => setEndAt(`${endAt.split('T')[0] || ''}T${e.target.value}`)} />
          </label>
          <label className="lsm-file" data-busy={busy ? 'true' : 'false'}>
            <span className="nmts-btn-primary lsm-file-btn">{busy ? 'Uploading…' : 'Upload Scheduled Media'}</span>
            <input
              type="file"
              accept={LOGIN_MEDIA_ACCEPT}
              disabled={busy}
              onChange={(e) => chooseFile('scheduled', e.target.files?.[0], e.target)}
            />
          </label>
        </section>
      </div>

      {cropJob && (
        <LoginImageCropper
          file={cropJob.file}
          title={
            cropJob.mediaKind === 'video'
              ? (cropJob.kind === 'scheduled' ? 'Adjust Scheduled Video' : 'Adjust Default Video')
              : (cropJob.kind === 'scheduled' ? 'Crop Scheduled Image' : 'Crop Default Image')
          }
          onConfirm={confirmCrop}
          onCancel={closeCrop}
        />
      )}
    </div>
  );
}
