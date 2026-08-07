import React from 'react';
import { createPortal } from 'react-dom';
import { Button } from '@/components/ui/button';

/**
 * NMTS confirmation dialog — stacks above NmtsModal (z-index 110).
 * Does not use Radix/shadcn AlertDialog to avoid body scroll-lock and z-index conflicts.
 */
export function NmtsConfirmDialog({
  open,
  title,
  message,
  confirmLabel = 'Confirm',
  cancelLabel = 'Cancel',
  variant = 'default',
  loading = false,
  onConfirm,
  onCancel,
  children,
}) {
  if (!open) return null;

  const confirmClass =
    variant === 'danger'
      ? 'bg-red-600 hover:bg-red-700 text-white'
      : 'nmts-btn-primary';

  const content = (
    <div
      className="nmts-confirm-overlay"
      role="presentation"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget && !loading) onCancel?.();
      }}
    >
      <div
        className="nmts-confirm-panel"
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="nmts-confirm-title"
        onMouseDown={(e) => e.stopPropagation()}
      >
        <h2 id="nmts-confirm-title" className="nmts-confirm-title">
          {title}
        </h2>
        {message ? <p className="nmts-confirm-message">{message}</p> : null}
        {children}
        <div className="nmts-confirm-actions">
          <Button type="button" variant="outline" disabled={loading} onClick={onCancel}>
            {cancelLabel}
          </Button>
          <Button
            type="button"
            className={confirmClass}
            disabled={loading}
            onClick={onConfirm}
          >
            {loading ? 'Please wait…' : confirmLabel}
          </Button>
        </div>
      </div>
    </div>
  );

  return createPortal(content, document.body);
}
