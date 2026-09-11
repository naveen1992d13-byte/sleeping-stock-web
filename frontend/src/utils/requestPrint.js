import { toast } from 'sonner';

const ITEMS_PER_PAGE = 12;

const esc = (v) => String(v ?? '').replace(/[&<>'"]/g, (c) => ({
  '&': '&amp;',
  '<': '&lt;',
  '>': '&gt;',
  "'": '&#39;',
  '"': '&quot;',
}[c]));

const nfmt = (v) => Number(v || 0).toLocaleString(undefined, { maximumFractionDigits: 2 });
const dtfmt = (v) => (v ? String(v).slice(0, 16).replace('T', ' ') : '-');

export function displayRequestStatus(status) {
  if (status === 'Approved') return 'Accepted';
  if (status === 'Partially Approved') return 'Partially Accepted';
  return status || 'Requested';
}

export const REQUEST_PRINT_COLUMNS = [
  'S.No',
  'PART NUMBER',
  'PART DESCRIPTION',
  'LOC',
  'REQUEST QTY',
  'ACCEPT QTY',
  'PURCHASE AGING',
  'SALES AGING',
  'STATUS / REMARKS',
];

export function buildRequestPrintHtml(group, logoUrl = '') {
  const items = Array.isArray(group?.items) ? group.items : [];
  const pages = [];
  for (let i = 0; i < items.length; i += ITEMS_PER_PAGE) pages.push(items.slice(i, i + ITEMS_PER_PAGE));
  if (!pages.length) pages.push([]);
  const totalAccepted = items.reduce((a, i) => a + Number(i.accepted_qty ?? i.approved_qty ?? 0), 0);
  const pageHtml = pages.map((pageItems, pageIndex) => `
    <section class="page">
      <div class="page-no">PAGE ${pageIndex + 1} OF ${pages.length}</div>
      <header><img src="${esc(logoUrl)}"/><div><div class="brand">Sleeping<span>Stocks</span></div><div class="sub">NMTS | Non Moving Tracking System</div></div><h1>PARTS TRANSFER REQUEST</h1></header>
      <div class="summary">
        <div class="meta"><b>REQUEST NO</b><span>: ${esc(group.request_number || '-')}</span><b>REFERENCE NO (ORDER NO)</b><span>: ${esc(group.order_number || '-')}</span><b>REQUEST DATE</b><span>: ${esc(dtfmt(group.requested_at))}</span></div>
        <div class="metric"><small>TOTAL ITEMS</small><strong>${group.total_items}</strong></div>
        <div class="metric"><small>REQUEST QTY</small><strong>${nfmt(group.total_qty)}</strong></div>
        <div class="metric"><small>ACCEPT QTY</small><strong>${nfmt(totalAccepted)}</strong></div>
        <div class="metric"><small>TOTAL VALUE</small><strong>₹ ${nfmt(group.total_value)}</strong></div>
        <div class="metric"><small>STATUS</small><strong class="status">${esc(displayRequestStatus(group.status))}</strong></div>
      </div>
      <div class="route">
        <div><h3>STOCK SOURCE (FROM)</h3><p><b>Brand:</b> ${esc(group.supplying_brand || group.requesting_brand || '-')}</p><p><b>Dealer:</b> ${esc(group.supplying_dealer || '-')}</p><p><b>Branch:</b> ${esc(group.supplying_branch || '-')}</p><p><b>Requested To:</b> ${esc((group.receiver_users || []).map(u => `${u.name || 'User'}${u.id ? ` (${u.id})` : ''}`).join(', ') || 'Supplying Branch Team')}</p></div>
        <div class="arrow">→</div>
        <div><h3>STOCK DESTINATION (TO)</h3><p><b>Brand:</b> ${esc(group.requesting_brand || '-')}</p><p><b>Dealer:</b> ${esc(group.requesting_dealer || '-')}</p><p><b>Branch:</b> ${esc(group.requesting_branch || '-')}</p><p><b>Requested By:</b> ${esc(group.requested_user_name || '-')} ${group.requested_user_id ? `(${esc(group.requested_user_id)})` : ''}</p></div>
      </div>
      <table><thead><tr>${REQUEST_PRINT_COLUMNS.map((h) => `<th>${h}</th>`).join('')}</tr></thead>
      <tbody>${pageItems.map((i, idx) => `<tr><td>${pageIndex * ITEMS_PER_PAGE + idx + 1}</td><td>${esc(i.part_number)}</td><td>${esc(i.description || '-')}</td><td>${esc(i.loc_at_request || '-')}</td><td>${nfmt(i.requested_qty)}</td><td>${nfmt(i.accepted_qty ?? i.approved_qty ?? 0)}</td><td>${esc(i.purchase_aging_days_at_request ?? i.purchase_aging_at_request ?? '-')}</td><td>${esc(i.sales_aging_days_at_request ?? i.sales_aging_at_request ?? '-')}</td><td>${esc(displayRequestStatus(i.status))}<br/><small>${esc(i.approval_remarks || i.remarks || '')}</small></td></tr>`).join('')}</tbody></table>
      ${pageIndex === pages.length - 1 ? `<div class="signatures"><div>REQUESTED BY<span></span><small>Signature</small></div><div>RECEIVED BY<span></span><small>Signature</small></div><div>APPROVED BY<span></span><small>Signature</small></div><div>DISPATCHED BY<span></span><small>Signature</small></div></div><div class="notes"><b>Notes:</b> Please verify part number, accepted quantity and LOC before dispatch.</div>` : `<div class="continued">(Contd... Page ${pageIndex + 2})</div>`}
    </section>`).join('');

  return `<!doctype html><html><head><title>${esc(group.request_number || 'Request')}</title><style>
    @page{size:A4 portrait;margin:8mm}*{box-sizing:border-box}body{margin:0;font-family:Arial,sans-serif;color:#17211b;background:#eee}.page{position:relative;width:100%;min-height:190mm;background:white;padding:8mm;page-break-after:always}.page:last-child{page-break-after:auto}.page-no{position:absolute;right:8mm;top:1.5mm;font-size:9px;font-weight:bold;line-height:1;white-space:nowrap;z-index:2}header{display:flex;align-items:center;border-bottom:2px solid #14532d;padding:7mm 34mm 8px 0;margin-bottom:8px;min-height:24mm}header img{width:70px;height:55px;object-fit:contain}.brand{font-size:24px;font-weight:800}.brand span{color:#15803d}.sub{font-size:9px;font-weight:bold;color:#166534}h1{margin-left:auto;margin-right:0;color:#14532d;font-size:22px;white-space:nowrap}.summary{display:grid;grid-template-columns:2.2fr repeat(5,.75fr);border:1px solid #cbd5d1;border-radius:8px;overflow:hidden}.meta{display:grid;grid-template-columns:150px 1fr;gap:7px;padding:10px;font-size:10px}.metric{border-left:1px solid #d5ddd8;text-align:center;padding:13px 5px}.metric small{font-size:8px}.metric strong{display:block;color:#14532d;font-size:15px;margin-top:6px}.metric .status{font-size:11px}.route{display:grid;grid-template-columns:1fr 60px 1fr;gap:10px;margin:10px 0}.route>div:not(.arrow){border:1px solid #d5ddd8;border-radius:8px;padding:10px}.route h3{color:#166534;font-size:11px;margin:0 0 8px}.route p{font-size:9px;margin:5px 0}.arrow{display:flex;align-items:center;justify-content:center;font-size:30px;color:#15803d}table{width:100%;border-collapse:collapse;font-size:8px}th{background:#14532d;color:white;padding:6px 4px}td{border:1px solid #d7ddd9;padding:5px 4px;text-align:center}td:nth-child(2),td:nth-child(3),td:last-child{text-align:left}.continued{text-align:right;margin-top:7px;font-size:9px;font-weight:bold}.signatures{display:grid;grid-template-columns:repeat(4,1fr);border:1px solid #d5ddd8;margin-top:12px}.signatures div{text-align:center;padding:8px;border-right:1px solid #d5ddd8;font-size:9px;font-weight:bold}.signatures div:last-child{border-right:0}.signatures span{display:block;height:45px;border:1px dashed #b8c2bc;margin:8px}.signatures small{font-weight:normal}.notes{border:1px solid #d5ddd8;border-top:0;padding:8px;font-size:8px}@media print{body{background:white}.page{padding:0;min-height:auto}}
  </style></head><body>${pageHtml}</body></html>`;
}

export function openRequestPrint(group) {
  const logo = `${window.location.origin}/sleeping-stock-logo.png`;
  const printHtml = buildRequestPrintHtml(group, logo);
  const iframe = document.createElement('iframe');
  iframe.setAttribute('aria-hidden', 'true');
  iframe.style.position = 'fixed';
  iframe.style.right = '0';
  iframe.style.bottom = '0';
  iframe.style.width = '1px';
  iframe.style.height = '1px';
  iframe.style.border = '0';
  iframe.style.opacity = '0';
  document.body.appendChild(iframe);
  const cleanup = () => setTimeout(() => iframe.remove(), 1000);
  iframe.onload = () => {
    try {
      iframe.contentWindow.focus();
      setTimeout(() => { iframe.contentWindow.print(); cleanup(); }, 250);
    } catch (error) {
      cleanup();
      toast.error('Unable to start printing this request');
    }
  };
  iframe.srcdoc = printHtml;
}
