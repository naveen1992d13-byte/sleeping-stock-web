import React, { useEffect, useMemo, useRef, useState } from 'react';
import {
  LOGIN_PROMO_ASPECT,
  LOGIN_PROMO_HEIGHT,
  LOGIN_PROMO_WIDTH,
  captureLoginVideoPoster,
  displaySettingsFromCropper,
  isLoginVideoFile,
} from './loginPromoCrop';

function layout(mediaW, mediaH, viewW, viewH, mode, zoom, ox, oy) {
  const width = mediaW || 1;
  const height = mediaH || 1;
  const base = mode === 'contain'
    ? Math.min(viewW / width, viewH / height)
    : Math.max(viewW / width, viewH / height);
  const scale = base * zoom;
  const w = width * scale;
  const h = height * scale;
  return {
    x: (viewW - w) / 2 + ox,
    y: (viewH - h) / 2 + oy,
    w,
    h,
    scale,
  };
}

export function LoginImageCropper({ file, title, onConfirm, onCancel }) {
  const viewRef = useRef(null);
  const mediaRef = useRef(null);
  const dragRef = useRef(null);
  const isVideo = isLoginVideoFile(file);
  const [src, setSrc] = useState('');
  const [blurSrc, setBlurSrc] = useState('');
  const [ready, setReady] = useState(false);
  const [mediaSize, setMediaSize] = useState({ w: LOGIN_PROMO_WIDTH, h: LOGIN_PROMO_HEIGHT });
  const [mode, setMode] = useState('contain');
  const [zoom, setZoom] = useState(1);
  const [offset, setOffset] = useState({ x: 0, y: 0 });
  const [viewSize, setViewSize] = useState({ w: LOGIN_PROMO_WIDTH, h: LOGIN_PROMO_HEIGHT });
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    const url = URL.createObjectURL(file);
    setSrc(url);
    setBlurSrc(isVideo ? '' : url);
    setReady(false);
    setMode('contain');
    setZoom(1);
    setOffset({ x: 0, y: 0 });
    return () => URL.revokeObjectURL(url);
  }, [file, isVideo]);

  useEffect(() => {
    const el = viewRef.current;
    if (!el) return undefined;
    const measure = () => {
      const w = el.clientWidth || LOGIN_PROMO_WIDTH;
      setViewSize({ w, h: w / LOGIN_PROMO_ASPECT });
    };
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const placed = useMemo(() => {
    if (!ready) return null;
    return layout(mediaSize.w, mediaSize.h, viewSize.w, viewSize.h, mode, zoom, offset.x, offset.y);
  }, [ready, mediaSize, mode, zoom, offset, viewSize]);

  const markReady = (width, height, frameUrl) => {
    setMediaSize({ w: width || LOGIN_PROMO_WIDTH, h: height || LOGIN_PROMO_HEIGHT });
    if (frameUrl) setBlurSrc(frameUrl);
    setReady(true);
  };

  const captureVideoFrame = () => {
    const video = mediaRef.current;
    if (!video) return;
    markReady(video.videoWidth, video.videoHeight);
    try {
      const canvas = document.createElement('canvas');
      canvas.width = Math.max(1, video.videoWidth || LOGIN_PROMO_WIDTH);
      canvas.height = Math.max(1, video.videoHeight || LOGIN_PROMO_HEIGHT);
      canvas.getContext('2d').drawImage(video, 0, 0, canvas.width, canvas.height);
      setBlurSrc(canvas.toDataURL('image/jpeg', 0.72));
    } catch {
      /* poster-less blur still prefers contain foreground */
    }
  };

  const onPointerDown = (e) => {
    if (!placed) return;
    e.preventDefault();
    e.currentTarget.setPointerCapture(e.pointerId);
    dragRef.current = { x: e.clientX, y: e.clientY, ox: offset.x, oy: offset.y };
  };

  const onPointerMove = (e) => {
    const drag = dragRef.current;
    if (!drag) return;
    setOffset({
      x: drag.ox + (e.clientX - drag.x),
      y: drag.oy + (e.clientY - drag.y),
    });
  };

  const endDrag = () => {
    dragRef.current = null;
  };

  const reset = () => {
    setMode('contain');
    setZoom(1);
    setOffset({ x: 0, y: 0 });
  };

  const fitFull = () => {
    setMode('contain');
    setZoom(1);
    setOffset({ x: 0, y: 0 });
  };

  const confirm = async () => {
    if (!placed) return;
    setBusy(true);
    try {
      const displaySettings = displaySettingsFromCropper({
        mode,
        zoom,
        offset,
        viewSize,
        mediaWidth: mediaSize.w,
        mediaHeight: mediaSize.h,
      });
      const posterFile = isVideo ? await captureLoginVideoPoster(file) : null;
      onConfirm({ file, posterFile, displaySettings });
    } catch {
      onCancel?.();
    } finally {
      setBusy(false);
    }
  };

  const mediaStyle = placed ? {
    position: 'absolute',
    left: placed.x,
    top: placed.y,
    width: placed.w,
    height: placed.h,
    maxWidth: 'none',
    zIndex: 1,
  } : { opacity: 0 };

  return (
    <div className="lsm-crop-overlay" role="presentation" onMouseDown={(e) => e.target === e.currentTarget && onCancel?.()}>
      <div className="lsm-crop-panel" role="dialog" aria-modal="true" aria-label={title} onMouseDown={(e) => e.stopPropagation()}>
        <div className="lsm-crop-head">
          <h3>{title}</h3>
          <button type="button" className="lsm-crop-close" onClick={onCancel} aria-label="Close">×</button>
        </div>
        <p className="lsm-crop-note">
          {isVideo ? 'One muted looping video · poster used for the blurred background' : `Final preview · ${LOGIN_PROMO_WIDTH}×${LOGIN_PROMO_HEIGHT} square (1:1)`}
        </p>
        <div
          ref={viewRef}
          className="lsm-crop-view"
          style={{ aspectRatio: `${LOGIN_PROMO_WIDTH} / ${LOGIN_PROMO_HEIGHT}` }}
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={endDrag}
          onPointerCancel={endDrag}
        >
          {blurSrc && (
            <div className="lsm-media-blur" aria-hidden="true">
              <img src={blurSrc} alt="" />
            </div>
          )}
          {src && (isVideo ? (
            <video
              ref={mediaRef}
              src={src}
              autoPlay
              muted
              loop
              playsInline
              controls={false}
              disablePictureInPicture
              draggable={false}
              onLoadedData={captureVideoFrame}
              style={mediaStyle}
            />
          ) : (
            <img
              ref={mediaRef}
              src={src}
              alt=""
              draggable={false}
              onLoad={(e) => markReady(e.currentTarget.naturalWidth, e.currentTarget.naturalHeight)}
              style={mediaStyle}
            />
          ))}
        </div>
        <label className="lsm-crop-zoom">
          Zoom
          <input
            type="range"
            min="1"
            max="3"
            step="0.01"
            value={zoom}
            onChange={(e) => setZoom(Number(e.target.value))}
          />
        </label>
        <div className="lsm-crop-actions">
          <button type="button" className="lsm-crop-btn" onClick={reset}>Reset</button>
          <button type="button" className="lsm-crop-btn" onClick={fitFull}>Fit Full Image</button>
          <button type="button" className="lsm-crop-btn" onClick={() => { setMode('cover'); setZoom(1); setOffset({ x: 0, y: 0 }); }}>Fill Frame</button>
          <button type="button" className="lsm-crop-btn lsm-crop-btn--primary" disabled={busy || !ready} onClick={confirm}>
            {busy ? 'Saving…' : (isVideo ? 'Confirm Video' : 'Confirm Crop')}
          </button>
        </div>
      </div>
    </div>
  );
}
