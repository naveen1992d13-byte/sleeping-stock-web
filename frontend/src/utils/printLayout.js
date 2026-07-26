import React from 'react';

// Injected once per page that offers a Print action. Hides the app chrome
// (header, scope bar) plus anything marked .no-print, and keeps only
// elements marked .print-area on the printed page. Table headers repeat on
// every printed page via `thead { display: table-header-group }`.
export function PrintStyles() {
  return (
    <style>{`
      @media print {
        #app-header, #app-scope-bar, .no-print { display: none !important; }
        body { background: #fff !important; }
        .print-area { display: block !important; }
        .print-only { display: block !important; }
        table { width: 100%; border-collapse: collapse; }
        thead { display: table-header-group; }
        tr { page-break-inside: avoid; }
        .print-page-break { page-break-before: always; }
        @page { size: A4; margin: 14mm 10mm; }
      }
      .print-only { display: none; }
    `}</style>
  );
}

// Common letterhead shown only when printing: brand heading + who/when.
export function PrintHeader({ title, subtitle, meta = [] }) {
  const now = new Date();
  const printedAt = `${now.toLocaleDateString('en-IN')} ${now.toLocaleTimeString('en-IN')}`;
  return (
    <div className="print-only" style={{ marginBottom: 16, borderBottom: '2px solid #047857', paddingBottom: 10 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
        <div>
          <div style={{ fontSize: 18, fontWeight: 800, color: '#047857' }}>Sleeping Stock — NMTS</div>
          <div style={{ fontSize: 13, fontWeight: 700, marginTop: 2 }}>{title}</div>
          {subtitle && <div style={{ fontSize: 11, color: '#374151' }}>{subtitle}</div>}
        </div>
        <div style={{ fontSize: 10, color: '#6B7280', textAlign: 'right' }}>Printed: {printedAt}</div>
      </div>
      {meta.length > 0 && (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 4, marginTop: 8, fontSize: 11 }}>
          {meta.map(([label, value]) => (
            <div key={label}><span style={{ color: '#6B7280' }}>{label}: </span><span style={{ fontWeight: 600 }}>{value ?? '-'}</span></div>
          ))}
        </div>
      )}
    </div>
  );
}
