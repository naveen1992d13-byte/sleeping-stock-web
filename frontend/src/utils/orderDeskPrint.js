import { toast } from 'sonner';

const esc = (v) =>
  String(v ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');

const nfmt = (n) => Number(n || 0).toLocaleString('en-IN', { maximumFractionDigits: 2 });

export function openOrderDeskPrint({ order, items = [] }) {
  const logo = `${window.location.origin}/sleeping-stock-logo.png`;
  const rows = Array.isArray(items) ? items : [];
  const itemsPerPage = 14;
  const pages = [];
  for (let i = 0; i < rows.length; i += itemsPerPage) pages.push(rows.slice(i, i + itemsPerPage));
  if (!pages.length) pages.push([]);

  const pageHtml = pages
    .map((pageItems, pageIndex) => {
      const body = pageItems
        .map((item, idx) => {
          const req = Number(item.required_qty ?? item.quantity ?? 0);
          const alloc = (item.selected_sources || []).reduce((a, s) => a + Number(s.allocated_qty || 0), 0);
          const avail = Number(item.available_qty || 0);
          const bal = Math.max(req - alloc, 0);
          const src = (item.selected_sources && item.selected_sources[0]) || (item.same_dealer_sources && item.same_dealer_sources[0]) || {};
          return `<tr>
            <td>${pageIndex * itemsPerPage + idx + 1}</td>
            <td>${esc(item.part_number)}</td>
            <td>${esc(item.description || item.part_name || '-')}</td>
            <td>${nfmt(req)}</td>
            <td>${nfmt(avail)}</td>
            <td>${nfmt(alloc)}</td>
            <td>${nfmt(bal)}</td>
            <td>${esc(item.loc || item.location || src.loc || '-')}</td>
            <td>${esc(item.purchase_aging_days ?? '-')}</td>
            <td>${esc(item.sales_aging_days ?? '-')}</td>
            <td>${esc(src.branch || src.dealer_name || '-')}</td>
            <td>${esc(item.availability_status || order?.status || '-')}</td>
          </tr>`;
        })
        .join('');
      return `<section class="page">
        <div class="page-no">PAGE ${pageIndex + 1} OF ${pages.length}</div>
        <header><img src="${esc(logo)}"/><div><div class="brand">Sleeping<span>Stocks</span></div><div class="sub">NMTS | Order Desk Record</div></div><h1>ORDER DESK</h1></header>
        <div class="summary">
          <div class="meta"><b>ORDER NO</b><span>: ${esc(order?.order_number)}</span><b>BRAND</b><span>: ${esc(order?.brand_name)}</span><b>DEALER</b><span>: ${esc(order?.dealer_name)}</span><b>BRANCH</b><span>: ${esc(order?.branch)}</span><b>STATUS</b><span>: ${esc(order?.status)}</span></div>
        </div>
        <table><thead><tr><th>S.No</th><th>PART NO</th><th>DESCRIPTION</th><th>REQ QTY</th><th>AVAIL</th><th>ALLOC</th><th>BALANCE</th><th>LOC</th><th>PUR AGING</th><th>SAL AGING</th><th>SOURCE</th><th>STATUS</th></tr></thead><tbody>${body}</tbody></table>
      </section>`;
    })
    .join('');

  const printHtml = `<!doctype html><html><head><title>${esc(order?.order_number || 'Order')}</title><style>
    @page{size:A4 landscape;margin:8mm}*{box-sizing:border-box}body{margin:0;font-family:Arial,sans-serif;color:#17211b;background:#eee}.page{position:relative;width:100%;min-height:180mm;background:white;padding:8mm;page-break-after:always}.page:last-child{page-break-after:auto}.page-no{position:absolute;right:8mm;top:1.5mm;font-size:9px;font-weight:bold}header{display:flex;align-items:center;border-bottom:2px solid #14532d;padding-bottom:8px;margin-bottom:8px}header img{width:60px;height:48px;object-fit:contain}.brand{font-size:22px;font-weight:800}.brand span{color:#15803d}.sub{font-size:9px;font-weight:bold;color:#166534}h1{margin-left:auto;color:#14532d;font-size:20px}.summary{border:1px solid #cbd5d1;border-radius:8px;padding:10px;margin-bottom:8px;font-size:10px}.meta{display:grid;grid-template-columns:120px 1fr;gap:4px}table{width:100%;border-collapse:collapse;font-size:8px}th{background:#14532d;color:white;padding:5px 3px}td{border:1px solid #d7ddd9;padding:4px 3px;text-align:center}td:nth-child(2),td:nth-child(3){text-align:left}@media print{body{background:white}.page{padding:0;min-height:auto}}
  </style></head><body>${pageHtml}</body></html>`;

  const iframe = document.createElement('iframe');
  iframe.setAttribute('aria-hidden', 'true');
  iframe.style.cssText = 'position:fixed;right:0;bottom:0;width:1px;height:1px;border:0;opacity:0';
  document.body.appendChild(iframe);
  const cleanup = () => setTimeout(() => iframe.remove(), 1000);
  iframe.onload = () => {
    try {
      iframe.contentWindow.focus();
      setTimeout(() => {
        iframe.contentWindow.print();
        cleanup();
      }, 250);
    } catch {
      cleanup();
      toast.error('Unable to start Order Desk print');
    }
  };
  iframe.srcdoc = printHtml;
}
