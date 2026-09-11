import React, { useEffect, useMemo, useRef, useState } from 'react';
import { useOutletContext } from 'react-router-dom';
import axios from 'axios';
import { API } from '@/App';
import { Button } from '@/components/ui/button';
import { ClipboardList, RefreshCw, Search, Send, Ban, ChevronDown, ChevronRight, ArrowRight, Package, Printer } from 'lucide-react';
import { toast } from 'sonner';
import { displayRequestStatus, openRequestPrint } from '@/utils/requestPrint';

const CLOSED_STATUSES = new Set(['Rejected', 'Cancelled', 'Completed']);
const STATUS_STYLES = {
  Requested: { bg: '#FEF3C7', fg: '#92400E' },
  Waiting: { bg: '#FEF3C7', fg: '#92400E' },
  Approved: { bg: '#D1FAE5', fg: '#065F46' },
  Accepted: { bg: '#D1FAE5', fg: '#065F46' },
  'Partially Approved': { bg: '#D1FAE5', fg: '#065F46' },
  'Partially Accepted': { bg: '#D1FAE5', fg: '#065F46' },
  Rejected: { bg: '#FCE7F3', fg: '#9F1239' },
  'No Response': { bg: '#FCE7F3', fg: '#9F1239' },
  Cancelled: { bg: '#FCE7F3', fg: '#9F1239' },
  Dispatched: { bg: '#E0F2FE', fg: '#075985' },
  Received: { bg: '#EDE9FE', fg: '#5B21B6' },
  Completed: { bg: '#D1FAE5', fg: '#065F46' },
};

function formatDeadlineCountdown(deadline, nowMs) {
  if (!deadline) return '';
  const end = Date.parse(String(deadline));
  if (!Number.isFinite(end)) return '';
  const left = Math.max(0, Math.floor((end - nowMs) / 1000));
  if (left <= 0) return 'Expired';
  const minutes = Math.floor(left / 60);
  const seconds = left % 60;
  return `${minutes}:${String(seconds).padStart(2, '0')} left`;
}

function groupRowTint(status) {
  const label = displayStatus(status);
  if (['Accepted', 'Completed', 'Partially Accepted'].includes(label)) return 'bg-emerald-50/70';
  if (label === 'Requested' || label === 'Waiting') return 'bg-amber-50/80';
  if (['Rejected', 'Cancelled', 'No Response'].includes(label)) return 'bg-rose-50/70';
  return '';
}
const nfmt = (v) => Number(v || 0).toLocaleString(undefined, { maximumFractionDigits: 2 });
const dtfmt = (v) => v ? String(v).slice(0, 16).replace('T', ' ') : '-';

/** UI display mapping — DB may still store Approved; show Accepted. */
function displayStatus(status) {
  return displayRequestStatus(status);
}

function StatusBadge({ status }) {
  const label = displayStatus(status);
  const s = STATUS_STYLES[label] || STATUS_STYLES[status] || { bg: '#F3F4F6', fg: '#374151' };
  return <span className="inline-block rounded-full px-2.5 py-1 text-xs font-semibold" style={{ backgroundColor: s.bg, color: s.fg }}>{label}</span>;
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
  if (items.every(i => i.status === 'Completed')) return 'Completed';
  if (items.every(i => ['Dispatched', 'Received', 'Completed'].includes(i.status))) {
    if (items.some(i => i.status === 'Received') && items.every(i => ['Received', 'Completed'].includes(i.status))) {
      return 'Received';
    }
    return 'Dispatched';
  }
  const allApproved = items.every(i => i.status === 'Approved');
  const partialQty = items.some(i => Number(i.accepted_qty ?? i.approved_qty ?? 0) < Number(i.requested_qty || 0));
  if (allApproved && !partialQty) return 'Accepted';
  return 'Partially Accepted';
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
  const [nowMs, setNowMs] = useState(Date.now());
  const loadRef = useRef(null);

  const load = async ({ silent = false } = {}) => {
    if (!silent) setLoading(true);
    try {
      const res = await axios.get(`${API}/requests`, { params: {
        view, search: search || undefined,
        brand: scopeBrand || undefined, dealer: scopeDealer || undefined, branch: scopeBranch || undefined,
      } });
      setRows(res.data || []);
    } catch (e) {
      if (!silent) toast.error(e.response?.data?.detail || 'Unable to load Request Center');
    } finally {
      if (!silent) setLoading(false);
    }
  };
  loadRef.current = load;
  // Global Dashboard scope is the single source of truth for Request Center.
  // Scope changes reset transient UI and fetch server-enforced results.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => {
    setExpanded({});
    setItemDrafts({});
    load();
  }, [view, scopeBrand, scopeDealer, scopeBranch]);

  useEffect(() => {
    const id = setInterval(() => setNowMs(Date.now()), 1000);
    return () => clearInterval(id);
  }, []);

  useEffect(() => {
    const id = setInterval(() => { loadRef.current?.({ silent: true }); }, 30000);
    return () => clearInterval(id);
  }, []);

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
      // New workflow: Requested → Accepted(Approved) → Dispatched → Completed.
      // Legacy Received rows remain readable and can still Complete.
      const statusMap = {
        dispatch: ['Approved'],
        complete: ['Dispatched', 'Received'],
      };
      const actionable = group.items.filter(i => statusMap[action]?.includes(i.status) && Number(i.accepted_qty ?? i.approved_qty ?? 0) > 0);
      if (!actionable.length) throw new Error(`No items are ready to ${action}`);
      await Promise.all(actionable.map(i => axios.post(`${API}/requests/${i.id}/${action}`, {})));
      toast.success(`Accepted items ${action === 'dispatch' ? 'dispatched' : 'completed'}`);
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
    <div className="rounded-xl border bg-white overflow-hidden shadow-sm">
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
          const deadline = g.response_deadline || g.items.find(i => i.response_deadline)?.response_deadline;
          const unresolved = g.items.some(i => (i.status || 'Requested') === 'Requested');
          const frozen = g.timer_frozen || ['responded', 'cancelled', 'timeout'].includes(g.response_status);
          const countdown = (unresolved && !frozen) ? formatDeadlineCountdown(deadline, nowMs) : '';
          return <div key={g.key} className={`p-4 ${groupRowTint(g.status)}`}>
            <div className="grid w-full items-center gap-4 md:grid-cols-[32px_1.2fr_2fr_.6fr_.7fr_.9fr_1fr_auto]">
              <button onClick={()=>setExpanded(p=>({...p,[g.key]:!open}))}>{open?<ChevronDown className="h-5 w-5"/>:<ChevronRight className="h-5 w-5"/>}</button>
              <button className="text-left" onClick={()=>setExpanded(p=>({...p,[g.key]:!open}))}><div className="text-xs text-slate-500">Request / Order No</div><div className="font-bold text-emerald-700">{g.request_number || 'Legacy Request'}</div><div className="text-xs text-slate-600">{g.order_number || '-'}</div></button>
              <button className="text-left" onClick={()=>setExpanded(p=>({...p,[g.key]:!open}))}><div className="text-xs text-slate-500">Stock Movement</div><div className="flex items-center gap-2 font-semibold"><span>{supplier}</span><ArrowRight className="h-4 w-4 text-emerald-600"/><span>{destination}</span></div></button>
              <span><div className="text-xs text-slate-500">Line Items</div><div className="font-semibold">{g.total_items}</div></span><span><div className="text-xs text-slate-500">Total Qty</div><div className="font-semibold">{nfmt(g.total_qty)}</div></span><span><div className="text-xs text-slate-500">Total Value</div><div className="font-semibold">₹{nfmt(g.total_value)}</div></span><span><StatusBadge status={g.status}/>{countdown && <div className="mt-1 text-xs font-semibold text-amber-800">{countdown}</div>}<div className="mt-1 text-xs text-slate-500">{g.response_status || dtfmt(g.requested_at)}</div></span>
              <Button size="sm" variant="outline" onClick={()=>openRequestPrint(g)}><Printer className="mr-1 h-4 w-4"/>Print</Button>
            </div>
            {open && <div className="mt-5 rounded-xl border bg-white p-4">
              <div className="grid gap-4 lg:grid-cols-[1fr_48px_1fr]"><ScopeCard title="Stock Source (From)" subtitle="Supplying Location" brand={g.supplying_brand || g.requesting_brand} dealer={g.supplying_dealer} branch={g.supplying_branch} users={g.receiver_users}/><div className="hidden items-center justify-center lg:flex"><ArrowRight className="h-7 w-7 text-emerald-600"/></div><ScopeCard title="Stock Destination (To)" subtitle="Requesting Location" brand={g.requesting_brand} dealer={g.requesting_dealer} branch={g.requesting_branch} requestedBy={{name:g.requested_user_name,id:g.requested_user_id}}/></div>
              <div className="mt-5 overflow-x-auto"><div className="mb-2 flex items-center gap-2 font-semibold"><Package className="h-4 w-4 text-emerald-600"/>Item-wise Acceptance</div><table className="w-full min-w-[1450px] text-sm"><thead className="bg-emerald-50"><tr>{['Part Number','Part Name','Request Qty','Accept Quantity','Purchase Aging','Sales Aging','LOC','Part Value','Status','Remarks','Action'].map(h=><th key={h} className="p-3 text-left">{h}</th>)}</tr></thead><tbody>{g.items.map(i=>{ const d=draftFor(i); const editable=view!=='outgoing' && i.status==='Requested'; return <tr key={i.id} className="border-t"><td className="p-3 font-semibold">{i.part_number}</td><td className="p-3">{i.description||'-'}</td><td className="p-3">{nfmt(i.requested_qty)}</td><td className="p-3">{editable?<input type="number" min="0" max={Number(i.requested_qty||0)} step="any" value={d.accepted_qty} onChange={e=>updateDraft(i,'accepted_qty',e.target.value)} className="h-9 w-28 rounded border px-2 font-semibold"/>:<span className="font-semibold">{nfmt(i.accepted_qty ?? i.approved_qty ?? 0)}</span>}</td><td className="p-3">{i.purchase_aging_days_at_request ?? i.purchase_aging_at_request ?? '-'}</td><td className="p-3">{i.sales_aging_days_at_request ?? i.sales_aging_at_request ?? '-'}</td><td className="p-3 font-medium">{i.loc_at_request || '-'}</td><td className="p-3">₹{nfmt(i.value_at_request)}</td><td className="p-3"><StatusBadge status={i.status}/>{i.decision_type==='Partial'&&<div className="mt-1 text-xs font-semibold text-blue-700">Partially Accepted</div>}{i.decided_at&&<div className="mt-1 text-xs text-slate-500">Sent: {dtfmt(i.decided_at)}</div>}</td><td className="p-3">{editable?<input value={d.remarks} onChange={e=>updateDraft(i,'remarks',e.target.value)} placeholder={Number(d.accepted_qty)<Number(i.requested_qty||0)?'Remark required':'Item remarks'} className="h-9 w-44 rounded border px-2"/>:(i.approval_remarks||i.remarks||'-')}</td><td className="p-3">{editable?<Button size="sm" disabled={loading} onClick={()=>decideItem(i)}><Send className="mr-1 h-4 w-4"/>Send</Button>:'Sent'}</td></tr>})}</tbody></table></div>
              <div className="mt-4 flex flex-wrap justify-end gap-2"><Button variant="outline" onClick={()=>openRequestPrint(g)}><Printer className="mr-1 h-4 w-4"/>Print Request</Button>{view!=='outgoing' && g.items.some(i=>i.status==='Approved' && Number(i.accepted_qty ?? i.approved_qty ?? 0)>0) && <Button disabled={loading} onClick={()=>transitionGroup(g,'dispatch')}>Dispatch Accepted</Button>}{view!=='incoming' && g.items.some(i=>['Dispatched','Received'].includes(i.status) && Number(i.accepted_qty ?? i.approved_qty ?? 0)>0) && <Button disabled={loading} onClick={()=>transitionGroup(g,'complete')}>Complete</Button>}{(view==='outgoing'||view==='all') && g.items.some(i=>['Requested','Approved'].includes(i.status)) && <Button variant="outline" disabled={loading} onClick={()=>cancelGroup(g)}><Ban className="mr-1 h-4 w-4"/>Cancel Request</Button>}</div>
            </div>}
          </div>;
        })}
        {!groups.length && <div className="p-12 text-center text-slate-500">{loading?'Loading requests…':'No requests found in this section.'}</div>}
      </div>
    </div>
  </div>;
}
