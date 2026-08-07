import React from 'react';
import { X } from 'lucide-react';

/**
 * Centered NMTS-themed modal (white surface, green accent, dark text).
 */
export function NmtsModal({ open, onClose, title, children, maxWidth = 'max-w-2xl' }) {
  if (!open) return null;

  return (
    <div
      className="nmts-modal-overlay"
      role="presentation"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose?.();
      }}
    >
      <div
        className={`nmts-modal-panel ${maxWidth}`}
        role="dialog"
        aria-modal="true"
        aria-labelledby={title ? 'nmts-modal-title' : undefined}
        onMouseDown={(e) => e.stopPropagation()}
      >
        <div className="nmts-modal-header">
          {title ? (
            <h2 id="nmts-modal-title" className="nmts-modal-title">
              {title}
            </h2>
          ) : (
            <span />
          )}
          <button type="button" className="nmts-modal-close" onClick={onClose} aria-label="Close">
            <X className="h-5 w-5" />
          </button>
        </div>
        <div className="nmts-modal-body">{children}</div>
      </div>
    </div>
  );
}
