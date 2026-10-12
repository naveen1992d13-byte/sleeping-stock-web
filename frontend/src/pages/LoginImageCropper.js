import React, { useEffect, useMemo, useRef, useState } from 'react';
import {
  LOGIN_PROMO_ASPECT,
  LOGIN_PROMO_FILL,
  LOGIN_PROMO_HEIGHT,
  LOGIN_PROMO_OUTPUT_HEIGHT,
  LOGIN_PROMO_OUTPUT_WIDTH,
  LOGIN_PROMO_WIDTH,
} from './loginPromoCrop';

function layout(img, viewW, viewH, mode, zoom, ox, oy) {
  const base = mode === 'contain'
    ? Math.min(viewW / img.width, viewH / img.height)
    : Math.max(viewW / img.width, viewH / img.height);
  const scale = base * zoom;
  const w = img.width * scale;
  const h = img.height * scale;
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
  const imgRef = useRef(null);
  const dragRef = useRef(null);
  const [src, setSrc] = useState('');
  const [ready, setReady] = useState(false);
  const [mode, setMode] = useState('contain');
  const [zoom, setZoom] = useState(1);
  const [offset, setOffset] = useState({ x: 0, y: 0 });
  const [viewSize, setViewSize] = useState({ w: LOGIN_PROMO_WIDTH, h: LOGIN_PROMO_HEIGHT });
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    const url = URL.createObjectURL(file);
    setSrc(url);
    setReady(false);
    setMode('contain');
    setZoom(1);
    setOffset({ x: 0, y: 0 });
    return () => URL.revokeObjectURL(url);
  }, [file]);

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
    const img = imgRef.current;
    if (!ready || !img) return null;
    return layout(img, viewSize.w, viewSize.h, mode, zoom, offset.x, offset.y);
  }, [ready, mode, zoom, offset, viewSize]);

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
    const img = imgRef.current;
    if (!img || !placed) return;
    setBusy(true);
    try {
      const canvas = document.createElement('canvas');
      canvas.width = LOGIN_PROMO_OUTPUT_WIDTH;
      canvas.height = LOGIN_PROMO_OUTPUT_HEIGHT;
      const ctx = canvas.getContext('2d');
      ctx.fillStyle = LOGIN_PROMO_FILL;
      ctx.fillRect(0, 0, canvas.width, canvas.height);
      ctx.imageSmoothingEnabled = true;
      ctx.imageSmoothingQuality = 'high';
      const sx = canvas.width / viewSize.w;
      ctx.drawImage(img, placed.x * sx, placed.y * sx, placed.w * sx, placed.h * sx);
      const blob = await new Promise((resolve, reject) => {
        canvas.toBlob((next) => (next ? resolve(next) : reject(new Error('Crop failed'))), 'image/png');
      });
      const name = String(file.name || 'login-image').replace(/\.[^.]+$/, '') + '.png';
      onConfirm(new File([blob], name, { type: 'image/png' }));
    } catch {
      onCancel?.();
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="lsm-crop-overlay" role="presentation" onMouseDown={(e) => e.target === e.currentTarget && onCancel?.()}>
      <div className="lsm-crop-panel" role="dialog" aria-modal="true" aria-label={title} onMouseDown={(e) => e.stopPropagation()}>
        <div className="lsm-crop-head">
          <h3>{title}</h3>
          <button type="button" className="lsm-crop-close" onClick={onCancel} aria-label="Close">×</button>
        </div>
        <p className="lsm-crop-note">Final preview · {LOGIN_PROMO_WIDTH}×{LOGIN_PROMO_HEIGHT} square (1:1)</p>
        <div
          ref={viewRef}
          className="lsm-crop-view"
          style={{ aspectRatio: `${LOGIN_PROMO_WIDTH} / ${LOGIN_PROMO_HEIGHT}` }}
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={endDrag}
          onPointerCancel={endDrag}
        >
          {src && (
            <img
              ref={imgRef}
              src={src}
              alt=""
              draggable={false}
              onLoad={() => setReady(true)}
              style={placed ? {
                position: 'absolute',
                left: placed.x,
                top: placed.y,
                width: placed.w,
                height: placed.h,
                maxWidth: 'none',
              } : { opacity: 0 }}
            />
          )}
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
            {busy ? 'Saving…' : 'Confirm Crop'}
          </button>
        </div>
      </div>
    </div>
  );
}
