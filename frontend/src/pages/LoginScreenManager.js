import React, { useEffect, useState } from 'react';
import axios from 'axios';
import { API, useAuth } from '@/App';
import { toast } from 'sonner';
import { Image as ImageIcon } from 'lucide-react';
import { LoginImageCropper } from './LoginImageCropper';
import {
  LOGIN_MEDIA_ACCEPT,
  captureLoginVideoPoster,
  isLoginVideoFile,
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

function PreviewMedia({ row, alt }) {
  const kind = row?.media_kind === 'video' ? 'video' : 'image';
  const src = mediaSrc(row, 'media');
  const poster = mediaSrc(row, 'poster');
  if (!src) return <p className="lsm-empty">No media yet.</p>;
  if (kind === 'video') {
    return (
      <video
        src={src}
        poster={poster || undefined}
        autoPlay
        muted
        loop
        playsInline
        controls={false}
        disablePictureInPicture
      />
    );
  }
  return <img src={src} alt={alt} />;
}

export function LoginScreenManager() {
  const { user } = useAuth();
  const [state, setState] = useState({ default: null, scheduled: null });
  const [busy, setBusy] = useState(false);
  const [startAt, setStartAt] = useState('');
  const [endAt, setEndAt] = useState('');
  const [cropJob, setCropJob] = useState(null);
  const [videoJob, setVideoJob] = useState(null);
  const [videoSrc, setVideoSrc] = useState('');

  const load = async () => {
    const res = await axios.get(`${API}/login-screen/manage`);
    setState(res.data || { default: null, scheduled: null });
    if (res.data?.scheduled?.start_at) setStartAt(toIstValue(res.data.scheduled.start_at));
    if (res.data?.scheduled?.end_at) setEndAt(toIstValue(res.data.scheduled.end_at));
  };

  useEffect(() => {
    if (user?.role !== 'master') return;
    load().catch(() => toast.error('Could not load Login Screen media'));
  }, [user?.role]);

  useEffect(() => {
    if (!videoJob?.file) {
      setVideoSrc('');
      return undefined;
    }
    const url = URL.createObjectURL(videoJob.file);
    setVideoSrc(url);
    return () => URL.revokeObjectURL(url);
  }, [videoJob]);

  if (user?.role !== 'master') {
    return <div className="p-6 text-sm text-gray-500">Master Admin only.</div>;
  }

  const closeCrop = () => {
    if (cropJob?.input) cropJob.input.value = '';
    setCropJob(null);
  };

  const closeVideo = () => {
    if (videoJob?.input) videoJob.input.value = '';
    setVideoJob(null);
  };

  const chooseFile = async (kind, file, input) => {
    if (!file) return;
    if (kind === 'scheduled' && (!startAt || !endAt)) {
      toast.error('Select Start and End date/time in IST');
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
      setVideoJob({ kind, file, input });
      return;
    }
    const imageError = validateLoginImageFile(file);
    if (imageError) {
      toast.error(imageError);
      if (input) input.value = '';
      return;
    }
    setCropJob({ kind, file, input });
  };

  const uploadDefault = async (file, input, posterFile) => {
    if (!file) return;
    const form = new FormData();
    form.append('file', file);
    if (posterFile) form.append('poster', posterFile);
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

  const uploadScheduled = async (file, input, posterFile) => {
    if (!file) return;
    if (!startAt || !endAt) {
      toast.error('Select Start and End date/time in IST');
      if (input) input.value = '';
      return;
    }
    const form = new FormData();
    form.append('file', file);
    if (posterFile) form.append('poster', posterFile);
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

  const confirmCrop = (processed) => {
    const job = cropJob;
    setCropJob(null);
    if (!job) return;
    if (job.kind === 'scheduled') {
      uploadScheduled(processed, job.input);
    } else {
      uploadDefault(processed, job.input);
    }
  };

  const confirmVideo = async () => {
    const job = videoJob;
    if (!job) return;
    setBusy(true);
    try {
      const poster = await captureLoginVideoPoster(job.file);
      setVideoJob(null);
      if (job.kind === 'scheduled') {
        await uploadScheduled(job.file, job.input, poster);
      } else {
        await uploadDefault(job.file, job.input, poster);
      }
    } catch (error) {
      toast.error(error?.message || 'Video preview failed');
      setBusy(false);
    }
  };

  return (
    <div className="nmts-login-screen-manager" data-testid="login-screen-manager">
      <header className="nmts-module-header">
        <div className="nmts-module-header-row">
          <ImageIcon className="h-5 w-5 nmts-module-header-icon" />
          <h1>Login Screen Manager</h1>
        </div>
        <p>Images: JPG, PNG, WebP. Videos: MP4 H.264/AAC, max 30 seconds / 20 MB. Square 1:1, recommended 1080×1080. Dates and times are IST.</p>
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
          <label className="lsm-field">Start Date/Time (IST)
            <input className="lsm-input" type="datetime-local" value={startAt} onChange={(e) => setStartAt(e.target.value)} />
          </label>
          <label className="lsm-field">End Date/Time (IST)
            <input className="lsm-input" type="datetime-local" value={endAt} onChange={(e) => setEndAt(e.target.value)} />
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
          title={cropJob.kind === 'scheduled' ? 'Crop Scheduled Image' : 'Crop Default Image'}
          onConfirm={confirmCrop}
          onCancel={closeCrop}
        />
      )}

      {videoJob && (
        <div className="lsm-crop-overlay" role="presentation" onMouseDown={(e) => e.target === e.currentTarget && closeVideo()}>
          <div className="lsm-crop-panel" role="dialog" aria-modal="true" aria-label="Preview login video" onMouseDown={(e) => e.stopPropagation()}>
            <div className="lsm-crop-head">
              <h3>{videoJob.kind === 'scheduled' ? 'Preview Scheduled Video' : 'Preview Default Video'}</h3>
              <button type="button" className="lsm-crop-close" onClick={closeVideo} aria-label="Close">×</button>
            </div>
            <p className="lsm-crop-note">Square 1:1 preview · muted, looping, no controls</p>
            <div className="lsm-crop-view lsm-video-view" style={{ aspectRatio: '1 / 1' }}>
              {videoSrc && (
                <video
                  src={videoSrc}
                  autoPlay
                  muted
                  loop
                  playsInline
                  controls={false}
                  disablePictureInPicture
                />
              )}
            </div>
            <div className="lsm-crop-actions">
              <button type="button" className="lsm-crop-btn" onClick={closeVideo}>Cancel</button>
              <button type="button" className="lsm-crop-btn lsm-crop-btn--primary" disabled={busy} onClick={confirmVideo}>
                {busy ? 'Saving…' : 'Confirm Video'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
