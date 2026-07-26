import React, { useEffect, useMemo, useState } from 'react';
import { useOutletContext } from 'react-router-dom';
import axios from 'axios';
import { API } from '@/App';
import { Button } from '@/components/ui/button';
import { ClipboardList, RefreshCw, Search, Send, Ban, ChevronDown, ChevronRight, ArrowRight, Package, Printer } from 'lucide-react';
import { toast } from 'sonner';

const CLOSED_STATUSES = new Set(['Rejected', 'Cancelled', 'Completed']);
const STATUS_STYLES = {
  Requested: { bg: '#FEF3C7', fg: '#92400E' }, Approved: { bg: '#D1FAE5', fg: '#065F46' },
  'Partially Approved': { bg: '#DBEAFE', fg: '#1E40AF' }, Rejected: { bg: '#FEE2E2', fg: '#991B1B' },
  Cancelled: { bg: '#E5E7EB', fg: '#374151' }, Dispatched: { bg: '#E0F2FE', fg: '#075985' },
  Received: { bg: '#EDE9FE', fg: '#5B21B6' }, Completed: { bg: '#D1FAE5', fg: '#065F46' },
};
const nfmt = (v) => Number(v || 0).toLocaleString(undefined, { maximumFractionDigits: 2 });
const dtfmt = (v) => v ? String(v).slice(0, 16).replace('T', ' ') : '-';
const esc = (v) => String(v ?? '').replace(/[&<>'"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' }[c]));

function StatusBadge({ status }) {
  const s = STATUS_STYLES[status] || { bg: '#F3F4F6', fg: '#374151' };
  return <span className="inline-block rounded-full px-2.5 py-1 text-xs font-semibold" style={{ backgroundColor: s.bg, color: s.fg }}>{status || 'Requested'}</span>;
}

function ScopeCard({ title, subtitle, brand, dealer, branch, users, requestedBy }) {
  return <div className="rounded-xl border bg-slate-50 p-4">
    <div className="text-xs font-semibold uppercase tracking-wide text-emerald-700">{title}</div>
    <div className="mt-1 font-semibold text-slate-800">{subtitle}</div>
    <div className="mt-3 grid gap-2 text-sm sm:grid-cols-3">
      <div><span className="text-slate-500">Brand</span><div className="font-medium">{brand || '-'}</div></div>
      <div><span className="text-slate-500">Dealer</span><div className="font-medium">{dealer || '-'}</div></div>
      <div><span className="text-slate-500">Branch</span><div className="font-medium">{branch || '-'}</div></div>
    </div>
    <div className="mt-3 border-t pt-3 text-sm">
      {requestedBy ? <><span className="text-slate-500">Requested By</span><div className="font-medium">{requestedBy.name || '-'} {requestedBy.id ? `(${requestedBy.id})` : ''}</div></> : <><span className="text-slate-500">Request Sent To</span><div className="font-medium">{users?.length ? users.map(u => `${u.name || 'User'}${u.id ? ` (${u.id})` : ''}`).join(', ') : 'Supplying Branch Team'}</div></>}
    </div>
  </div>;
}

function getGroupStatus(items) {
  const pending = items.some(i => (i.status || 'Requested') === 'Requested');
  if (pending) return 'Requested';
  if (items.every(i => i.status === 'Rejected')) return 'Rejected';
  if (items.every(i => i.status === 'Cancelled')) return 'Cancelled';
  const allApproved = items.every(i => i.status === 'Approved');
  const partialQty = items.some(i => Number(i.accepted_qty ?? i.approved_qty ?? 0) < Number(i.requested_qty || 0));
  if (allApproved && !partialQty) return 'Approved';
  return 'Partially Approved';
}

function openRequestPrint(group) {
  const logo = `${window.location.origin}/sleeping-stock-logo.png`;
  const itemsPerPage = 12;
  const pages = [];
  for (let i = 0; i < group.items.length; i += itemsPerPage) pages.push(group.items.slice(i, i + itemsPerPage));
  if (!pages.length) pages.push([]);
  const totalAccepted = group.items.reduce((a, i) => a + Number(i.accepted_qty ?? i.approved_qty ?? 0), 0);
  const pageHtml = pages.map((items, pageIndex) => `
    <section class="page">
      <div class="page-no">PAGE ${pageIndex + 1} OF ${pages.length}</div>
      <header><img src="${esc(logo)}"/><div><div class="brand">Sleeping<span>Stocks</span></div><div class="sub">NMTS | Non Moving Tracking System</div></div><h1>PARTS TRANSFER REQUEST</h1></header>
      <div class="summary">
        <div class="meta"><b>REQUEST NO</b><span>: ${esc(group.request_number || '-')}</span><b>REFERENCE NO (ORDER NO)</b><span>: ${esc(group.order_number || '-')}</span><b>REQUEST DATE</b><span>: ${esc(dtfmt(group.requested_at))}</span></div>
        <div class="metric"><small>TOTAL ITEMS</small><strong>${group.total_items}</strong></div>
        <div class="metric"><small>REQUEST QTY</small><strong>${nfmt(group.total_qty)}</strong></div>
        <div class="metric"><small>ACCEPT QTY</small><strong>${nfmt(totalAccepted)}</strong></div>
        <div class="metric"><small>TOTAL VALUE</small><strong>₹ ${nfmt(group.total_value)}</strong></div>
        <div class="metric"><small>STATUS</small><strong class="status">${esc(group.status)}</strong></div>
      </div>
      <div class="route">
        <div><h3>STOCK SOURCE (FROM)</h3><p><b>Brand:</b> ${esc(group.supplying_brand || group.requesting_brand || '-')}</p><p><b>Dealer:</b> ${esc(group.supplying_dealer || '-')}</p><p><b>Branch:</b> ${esc(group.supplying_branch || '-')}</p><p><b>Requested To:</b> ${esc((group.receiver_users || []).map(u => `${u.name || 'User'}${u.id ? ` (${u.id})` : ''}`).join(', ') || 'Supplying Branch Team')}</p></div>
        <div class="arrow">→</div>
        <div><h3>STOCK DESTINATION (TO)</h3><p><b>Brand:</b> ${esc(group.requesting_brand || '-')}</p><p><b>Dealer:</b> ${esc(group.requesting_dealer || '-')}</p><p><b>Branch:</b> ${esc(group.requesting_branch || '-')}</p><p><b>Requested By:</b> ${esc(group.requested_user_name || '-')} ${group.requested_user_id ? `(${esc(group.requested_user_id)})` : ''}</p></div>
      </div>
      <table><thead><tr><th>S.No</th><th>PART NUMBER</th><th>PART DESCRIPTION</th><th>LOC</th><th>REQUEST QTY</th><th>ACCEPT QTY</th><th>PURCHASE AGING</th><th>SALES AGING</th><th>STATUS / REMARKS</th></tr></thead>
      <tbody>${items.map((i, idx) => `<tr><td>${pageIndex * itemsPerPage + idx + 1}</td><td>${esc(i.part_number)}</td><td>${esc(i.description || '-')}</td><td>${esc(i.loc_at_request || '-')}</td><td>${nfmt(i.requested_qty)}</td><td>${nfmt(i.accepted_qty ?? i.approved_qty ?? 0)}</td><td>${esc(i.purchase_aging_days_at_request ?? i.purchase_aging_at_request ?? '-')}</td><td>${esc(i.sales_aging_days_at_request ?? i.sales_aging_at_request ?? '-')}</td><td>${esc(i.status || 'Requested')}<br/><small>${esc(i.approval_remarks || i.remarks || '')}</small></td></tr>`).join('')}</tbody></table>
      ${pageIndex === pages.length - 1 ? `<div class="signatures"><div>REQUESTED BY<span></span><small>Signature</small></div><div>RECEIVED BY<span></span><small>Signature</small></div><div>APPROVED BY<span></span><small>Signature</small></div><div>DISPATCHED BY<span></span><small>Signature</small></div></div><div class="notes"><b>Notes:</b> Please verify part number, accepted quantity and LOC before dispatch.</div>` : `<div class="continued">(Contd... Page ${pageIndex + 2})</div>`}
    </section>`).join('');

  const printHtml = `<!doctype html><html><head><title>${esc(group.request_number || 'Request')}</title><style>
    @page{size:A4 portrait;margin:8mm}*{box-sizing:border-box}body{margin:0;font-family:Arial,sans-serif;color:#17211b;background:#eee}.page{position:relative;width:100%;min-height:190mm;background:white;padding:8mm;page-break-after:always}.page:last-child{page-break-after:auto}.page-no{position:absolute;right:8mm;top:1.5mm;font-size:9px;font-weight:bold;line-height:1;white-space:nowrap;z-index:2}header{display:flex;align-items:center;border-bottom:2px solid #14532d;padding:7mm 34mm 8px 0;margin-bottom:8px;min-height:24mm}header img{width:70px;height:55px;object-fit:contain}.brand{font-size:24px;font-weight:800}.brand span{color:#15803d}.sub{font-size:9px;font-weight:bold;color:#166534}h1{margin-left:auto;margin-right:0;color:#14532d;font-size:22px;white-space:nowrap}.summary{display:grid;grid-template-columns:2.2fr repeat(5,.75fr);border:1px solid #cbd5d1;border-radius:8px;overflow:hidden}.meta{display:grid;grid-template-columns:150px 1fr;gap:7px;padding:10px;font-size:10px}.metric{border-left:1px solid #d5ddd8;text-align:center;padding:13px 5px}.metric small{font-size:8px}.metric strong{display:block;color:#14532d;font-size:15px;margin-top:6px}.metric .status{font-size:11px}.route{display:grid;grid-template-columns:1fr 60px 1fr;gap:10px;margin:10px 0}.route>div:not(.arrow){border:1px solid #d5ddd8;border-radius:8px;padding:10px}.route h3{color:#166534;font-size:11px;margin:0 0 8px}.route p{font-size:9px;margin:5px 0}.arrow{display:flex;align-items:center;justify-content:center;font-size:30px;color:#15803d}table{width:100%;border-collapse:collapse;font-size:8px}th{background:#14532d;color:white;padding:6px 4px}td{border:1px solid #d7ddd9;padding:5px 4px;text-align:center}td:nth-child(2),td:nth-child(3),td:last-child{text-align:left}.continued{text-align:right;margin-top:7px;font-size:9px;font-weight:bold}.signatures{display:grid;grid-template-columns:repeat(4,1fr);border:1px solid #d5ddd8;margin-top:12px}.signatures div{text-align:center;padding:8px;border-right:1px solid #d5ddd8;font-size:9px;font-weight:bold}.signatures div:last-child{border-right:0}.signatures span{display:block;height:45px;border:1px dashed #b8c2bc;margin:8px}.signatures small{font-weight:normal}.notes{border:1px solid #d5ddd8;border-top:0;padding:8px;font-size:8px}@media print{body{background:white}.page{padding:0;min-height:auto}}
  </style></head><body>${pageHtml}</body></html>`;
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

export function Requests() {
  const { scopeBrand, scopeDealer, scopeBranch } = useOutletContext() || {};
  const [view, setView] = useState('incoming');
  const [stage, setStage] = useState('pending');
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(false);
  const [search, setSearch] = useState('');
  const [expanded, setExpanded] = useState({});
  const [itemDrafts, setItemDrafts] = useState({});

  const load = async () => {
    setLoading(true);
    try {
      const res = await axios.get(`${API}/requests`, { params: {
        view, search: search || undefined,
        brand: scopeBrand || undefined, dealer: scopeDealer || undefined, branch: scopeBranch || undefined,
      } });
      setRows(res.data || []);
    } catch (e) { toast.error(e.response?.data?.detail || 'Unable to load Request Center'); }
    finally { setLoading(false); }
  };
  // Global Dashboard scope is the single source of truth for Request Center.
  // Scope changes reset transient UI and fetch server-enforced results.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => {
    setExpanded({});
    setItemDrafts({});
    load();
  }, [view, scopeBrand, scopeDealer, scopeBranch]);

  const groups = useMemo(() => {
    const map = new Map();
    rows.forEach((r) => {
      const key = r.request_number || `legacy-${r.id}`;
      if (!map.has(key)) map.set(key, { ...r, key, items: [], receiver_users: r.receiver_users || [] });
      map.get(key).items.push(r);
    });
    return Array.from(map.values()).map(g => ({
      ...g, status: getGroupStatus(g.items), total_items: g.items.length,
      total_qty: g.items.reduce((a, i) => a + Number(i.requested_qty || 0), 0),
      total_value: g.items.reduce((a, i) => a + Number(i.value_at_request || 0), 0),
    })).filter(g => stage === 'completed' ? CLOSED_STATUSES.has(g.status) : !CLOSED_STATUSES.has(g.status));
  }, [rows, stage]);

  const updateDraft = (item, field, value) => setItemDrafts(p => ({ ...p, [item.id]: { accepted_qty: item.accepted_qty ?? item.approved_qty ?? item.requested_qty, remarks: item.approval_remarks || '', ...(p[item.id] || {}), [field]: value } }));
  const draftFor = (item) => itemDrafts[item.id] || { accepted_qty: item.accepted_qty ?? item.approved_qty ?? item.requested_qty, remarks: item.approval_remarks || '' };

  const decideItem = async (item) => {
    const draft = draftFor(item);
    const acceptedQty = Number(draft.accepted_qty);
    const requestedQty = Number(item.requested_qty || 0);
    if (!Number.isFinite(acceptedQty)) return toast.error('Accepted Quantity must be numeric');
    if (acceptedQty < 0 || acceptedQty > requestedQty) return toast.error('Accepted Quantity must be between 0 and Requested Quantity');
    if (acceptedQty < requestedQty && !String(draft.remarks || '').trim()) return toast.error('Remark is required for Partial or Rejected responses');
    setLoading(true);
    try {
      await axios.post(`${API}/requests/${item.id}/approve`, { accepted_qty: acceptedQty, remarks: draft.remarks || '' });
      toast.success(acceptedQty === requestedQty ? 'Part accepted and sent' : acceptedQty === 0 ? 'Part rejected and sent' : 'Partial acceptance sent');
      await load();
    } catch (e) { toast.error(e.response?.data?.detail || 'Unable to send this part response'); }
    finally { setLoading(false); }
  };

  const transitionGroup = async (group, action) => {
    setLoading(true);
    try {
      const statusMap = { dispatch: ['Approved'], receive: ['Dispatched'], complete: ['Received'] };
      const actionable = group.items.filter(i => statusMap[action]?.includes(i.status) && Number(i.accepted_qty ?? i.approved_qty ?? 0) > 0);
      if (!actionable.length) throw new Error(`No items are ready to ${action}`);
      await Promise.all(actionable.map(i => axios.post(`${API}/requests/${i.id}/${action}`, {})));
      toast.success(`Accepted items ${action === 'dispatch' ? 'dispatched' : action === 'receive' ? 'received' : 'completed'}`);
      await load();
    } catch (e) { toast.error(e.response?.data?.detail || e.message || `Unable to ${action} request`); }
    finally { setLoading(false); }
  };

  const cancelGroup = async (group) => {
    setLoading(true);
    try {
      const actionable = group.items.filter(i => ['Requested', 'Approved'].includes(i.status));
      await Promise.all(actionable.map(i => axios.post(`${API}/requests/${i.id}/cancel`, { remarks: 'Request cancelled' })));
      toast.success('Request cancelled'); await load();
    } catch (e) { toast.error(e.response?.data?.detail || 'Unable to cancel request'); }
    finally { setLoading(false); }
  };

  return <div className="space-y-6" data-testid="requests-page">
    <div className="rounded-2xl p-6" style={{ backgroundColor: '#34D399' }}><div className="flex items-center gap-3"><ClipboardList className="h-8 w-8 text-white"/><div><h1 className="text-2xl font-bold text-white">Request Center</h1><p className="text-emerald-100">Review and accept each part individually, including partial quantities.</p></div></div></div>
    <div className="rounded-xl border bg-white overflow-hidden">
      <div className="flex flex-wrap gap-2 p-4 border-b">
        <Button variant={view==='incoming'?'default':'outline'} onClick={()=>setView('incoming')}>Requests To Me</Button>
        <Button variant={view==='outgoing'?'default':'outline'} onClick={()=>setView('outgoing')}>My Requests</Button>
        <Button variant={view==='all'?'default':'outline'} onClick={()=>setView('all')}>All Requests</Button>
        <div className="mx-2 h-9 border-l"/><Button variant={stage==='pending'?'default':'outline'} onClick={()=>setStage('pending')}>Pending</Button><Button variant={stage==='completed'?'default':'outline'} onClick={()=>setStage('completed')}>Completed</Button>
        <div className="ml-auto flex gap-2"><div className="relative"><Search className="absolute left-2 top-2.5 h-4 w-4 text-slate-400"/><input value={search} onChange={e=>setSearch(e.target.value)} onKeyDown={e=>e.key==='Enter'&&load()} placeholder="Request / Order / Part No" className="h-9 rounded border pl-8 pr-3 text-sm"/></div><Button size="sm" variant="outline" onClick={load}><RefreshCw className="mr-2 h-4 w-4"/>Refresh</Button></div>
      </div>
      <div className="divide-y">
        {groups.map(g => {
          const open=!!expanded[g.key]; const supplier=`${g.supplying_branch || '-'} / ${g.supplying_dealer || '-'}`; const destination=`${g.requesting_branch || '-'} / ${g.requesting_dealer || '-'}`;
          return <div key={g.key} className="p-4">
            <div className="grid w-full items-center gap-4 md:grid-cols-[32px_1.2fr_2fr_.6fr_.7fr_.9fr_.8fr_auto]">
              <button onClick={()=>setExpanded(p=>({...p,[g.key]:!open}))}>{open?<ChevronDown className="h-5 w-5"/>:<ChevronRight className="h-5 w-5"/>}</button>
              <button className="text-left" onClick={()=>setExpanded(p=>({...p,[g.key]:!open}))}><div className="text-xs text-slate-500">Request / Order No</div><div className="font-bold text-emerald-700">{g.request_number || 'Legacy Request'}</div><div className="text-xs text-slate-600">{g.order_number || '-'}</div></button>
              <button className="text-left" onClick={()=>setExpanded(p=>({...p,[g.key]:!open}))}><div className="text-xs text-slate-500">Stock Movement</div><div className="flex items-center gap-2 font-semibold"><span>{supplier}</span><ArrowRight className="h-4 w-4 text-emerald-600"/><span>{destination}</span></div></button>
              <span><div className="text-xs text-slate-500">Line Items</div><div className="font-semibold">{g.total_items}</div></span><span><div className="text-xs text-slate-500">Total Qty</div><div className="font-semibold">{nfmt(g.total_qty)}</div></span><span><div className="text-xs text-slate-500">Total Value</div><div className="font-semibold">₹{nfmt(g.total_value)}</div></span><span><StatusBadge status={g.status}/><div className="mt-1 text-xs text-slate-500">{dtfmt(g.requested_at)}</div></span>
              <Button size="sm" variant="outline" onClick={()=>openRequestPrint(g)}><Printer className="mr-1 h-4 w-4"/>Print</Button>
            </div>
            {open && <div className="mt-5 rounded-xl border bg-white p-4">
              <div className="grid gap-4 lg:grid-cols-[1fr_48px_1fr]"><ScopeCard title="Stock Source (From)" subtitle="Supplying Location" brand={g.supplying_brand || g.requesting_brand} dealer={g.supplying_dealer} branch={g.supplying_branch} users={g.receiver_users}/><div className="hidden items-center justify-center lg:flex"><ArrowRight className="h-7 w-7 text-emerald-600"/></div><ScopeCard title="Stock Destination (To)" subtitle="Requesting Location" brand={g.requesting_brand} dealer={g.requesting_dealer} branch={g.requesting_branch} requestedBy={{name:g.requested_user_name,id:g.requested_user_id}}/></div>
              <div className="mt-5 overflow-x-auto"><div className="mb-2 flex items-center gap-2 font-semibold"><Package className="h-4 w-4 text-emerald-600"/>Item-wise Acceptance</div><table className="w-full min-w-[1450px] text-sm"><thead className="bg-emerald-50"><tr>{['Part Number','Part Name','Request Qty','Accept Quantity','Purchase Aging','Sales Aging','LOC','Part Value','Status','Remarks','Action'].map(h=><th key={h} className="p-3 text-left">{h}</th>)}</tr></thead><tbody>{g.items.map(i=>{ const d=draftFor(i); const editable=view!=='outgoing' && i.status==='Requested'; return <tr key={i.id} className="border-t"><td className="p-3 font-semibold">{i.part_number}</td><td className="p-3">{i.description||'-'}</td><td className="p-3">{nfmt(i.requested_qty)}</td><td className="p-3">{editable?<input type="number" min="0" max={Number(i.requested_qty||0)} step="any" value={d.accepted_qty} onChange={e=>updateDraft(i,'accepted_qty',e.target.value)} className="h-9 w-28 rounded border px-2 font-semibold"/>:<span className="font-semibold">{nfmt(i.accepted_qty ?? i.approved_qty ?? 0)}</span>}</td><td className="p-3">{i.purchase_aging_days_at_request ?? i.purchase_aging_at_request ?? '-'}</td><td className="p-3">{i.sales_aging_days_at_request ?? i.sales_aging_at_request ?? '-'}</td><td className="p-3 font-medium">{i.loc_at_request || '-'}</td><td className="p-3">₹{nfmt(i.value_at_request)}</td><td className="p-3"><StatusBadge status={i.status}/>{i.decision_type==='Partial'&&<div className="mt-1 text-xs font-semibold text-blue-700">Partially Accepted</div>}{i.decided_at&&<div className="mt-1 text-xs text-slate-500">Sent: {dtfmt(i.decided_at)}</div>}</td><td className="p-3">{editable?<input value={d.remarks} onChange={e=>updateDraft(i,'remarks',e.target.value)} placeholder={Number(d.accepted_qty)<Number(i.requested_qty||0)?'Remark required':'Item remarks'} className="h-9 w-44 rounded border px-2"/>:(i.approval_remarks||i.remarks||'-')}</td><td className="p-3">{editable?<Button size="sm" disabled={loading} onClick={()=>decideItem(i)}><Send className="mr-1 h-4 w-4"/>Send</Button>:'Sent'}</td></tr>})}</tbody></table></div>
              <div className="mt-4 flex flex-wrap justify-end gap-2"><Button variant="outline" onClick={()=>openRequestPrint(g)}><Printer className="mr-1 h-4 w-4"/>Print Request</Button>{view!=='outgoing' && g.items.some(i=>i.status==='Approved' && Number(i.accepted_qty ?? i.approved_qty ?? 0)>0) && <Button disabled={loading} onClick={()=>transitionGroup(g,'dispatch')}>Dispatch Accepted</Button>}{view!=='incoming' && g.items.some(i=>i.status==='Dispatched') && <Button disabled={loading} onClick={()=>transitionGroup(g,'receive')}>Receive</Button>}{view!=='incoming' && g.items.some(i=>i.status==='Received') && <Button disabled={loading} onClick={()=>transitionGroup(g,'complete')}>Complete</Button>}{(view==='outgoing'||view==='all') && g.items.some(i=>['Requested','Approved'].includes(i.status)) && <Button variant="outline" disabled={loading} onClick={()=>cancelGroup(g)}><Ban className="mr-1 h-4 w-4"/>Cancel Request</Button>}</div>
            </div>}
          </div>;
        })}
        {!groups.length && <div className="p-12 text-center text-slate-500">{loading?'Loading requests…':'No requests found in this section.'}</div>}
      </div>
    </div>
  </div>;
}
