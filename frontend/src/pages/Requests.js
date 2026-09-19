import React, { useEffect, useMemo, useRef, useState } from 'react';
import { useOutletContext, useSearchParams } from 'react-router-dom';
import axios from 'axios';
import { API } from '@/App';
import { Button } from '@/components/ui/button';
import { RefreshCw, Search, Send, Ban, ChevronDown, ChevronRight, ArrowRight, Package, Printer } from 'lucide-react';
import { toast } from 'sonner';
import { displayRequestStatus, openRequestPrint } from '@/utils/requestPrint';

const CLOSED_STATUSES = new Set(['Rejected', 'Cancelled', 'Completed']);
const STATUS_STYLES = {
  Requested: { bg: '#B45309', fg: '#FFFBEB' },
  Waiting: { bg: '#B45309', fg: '#FFFBEB' },
  Active: { bg: '#B45309', fg: '#FFFBEB' },
  Approved: { bg: '#047857', fg: '#ECFDF5' },
  Accepted: { bg: '#047857', fg: '#ECFDF5' },
  'Partially Approved': { bg: '#047857', fg: '#ECFDF5' },
  'Partially Accepted': { bg: '#047857', fg: '#ECFDF5' },
  Rejected: { bg: '#BE123C', fg: '#FFF1F2' },
  'No Response': { bg: '#BE123C', fg: '#FFF1F2' },
  Cancelled: { bg: '#BE123C', fg: '#FFF1F2' },
  Dispatched: { bg: '#0369A1', fg: '#E0F2FE' },
  Received: { bg: '#6D28D9', fg: '#F5F3FF' },
  Completed: { bg: '#047857', fg: '#ECFDF5' },
  'Factory Order': { bg: '#1E3A8A', fg: '#DBEAFE' },
};

const STAGE_RANK = { sent: 1, picking: 2, picking_finished: 3, in_transit: 4, finished: 5 };

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

const nfmt = (v) => Number(v || 0).toLocaleString(undefined, { maximumFractionDigits: 2 });
const dtfmt = (v) => v ? String(v).slice(0, 16).replace('T', ' ') : '-';
const valueCell = (v) => (v === null || v === undefined || v === '') ? '-' : `₹${nfmt(v)}`;

async function openRequestCenterPdf(group) {
  const requestNumber = group?.request_number;
  if (!requestNumber) {
    openRequestPrint(group);
    return;
  }
  try {
    const res = await axios.get(`${API}/request-center/${encodeURIComponent(requestNumber)}/pdf`, {
      responseType: 'blob',
    });
    const url = URL.createObjectURL(res.data);
    window.open(url, '_blank', 'noopener');
  } catch (_e) {
    openRequestPrint(group);
  }
}

function displayStatus(status) {
  return displayRequestStatus(status);
}

function StatusBadge({ status }) {
  const label = displayStatus(status);
  const s = STATUS_STYLES[label] || STATUS_STYLES[status] || { bg: '#F3F4F6', fg: '#374151' };
  return <span className="inline-block rounded-full px-2.5 py-1 text-xs font-bold" style={{ backgroundColor: s.bg, color: s.fg }}>{label}</span>;
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

function itemFulfillmentStage(item) {
  if (item?.fulfillment_stage) return item.fulfillment_stage;
  const status = item?.status || 'Requested';
  if (CLOSED_STATUSES.has(status) && status !== 'Completed') return null;
  if (status === 'Requested') return 'sent';
  if (status === 'Approved' || status === 'Partially Approved') return item?.picking_finished_at ? 'picking_finished' : 'picking';
  if (status === 'Dispatched') return 'in_transit';
  if (status === 'Received' || status === 'Completed') return 'finished';
  return null;
}

function groupFulfillmentStage(items) {
  const stages = (items || []).map(itemFulfillmentStage).filter(Boolean);
  if (!stages.length) return null;
  return stages.reduce((min, s) => (STAGE_RANK[s] || 99) < (STAGE_RANK[min] || 99) ? s : min, stages[0]);
}

function itemPartStatus(item) {
  if (item?.part_status) return item.part_status;
  const status = item?.status;
  if (status === 'Requested') return 'Pending';
  if (status === 'Rejected') return 'Not Available';
  if (status === 'Approved' || status === 'Partially Approved') {
    if (item?.picking_finished_at) {
      const accepted = Number(item.accepted_qty ?? item.approved_qty ?? 0);
      const requested = Number(item.requested_qty || 0);
      return accepted < requested ? 'Partial' : 'Picked';
    }
    return 'Picking';
  }
  if (status === 'Dispatched') return 'Dispatched';
  if (status === 'Received' || status === 'Completed') return 'Finished';
  return status || 'Pending';
}

function inTransitLevel(group) {
  const hours = Number(group.in_transit_hours || 0);
  const level = group.in_transit_level || (hours >= 72 ? '72h' : hours >= 48 ? '48h' : hours >= 24 ? '24h' : null);
  return level;
}

export function Requests() {
  const { scopeBrand, scopeDealer, scopeBranch } = useOutletContext() || {};
  const [searchParams] = useSearchParams();
  const highlightRequest = String(searchParams.get('highlight') || '').trim();
  const [tab, setTab] = useState('outgoing');
  const [subFilter, setSubFilter] = useState('sent');
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(false);
  const [search, setSearch] = useState('');
  const [expanded, setExpanded] = useState({});
  const [itemDrafts, setItemDrafts] = useState({});
  const [nowMs, setNowMs] = useState(Date.now());
  const [dispatchForm, setDispatchForm] = useState(null);
  const loadRef = useRef(null);

  const apiView = (tab === 'outgoing' || tab === 'receipts') ? 'outgoing' : 'incoming';

  const load = async ({ silent = false } = {}) => {
    if (!silent) setLoading(true);
    try {
      const res = await axios.get(`${API}/requests`, { params: {
        view: apiView, search: search || undefined,
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

  useEffect(() => {
    setExpanded(highlightRequest ? { [highlightRequest]: true } : {});
    setItemDrafts({});
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab, scopeBrand, scopeDealer, scopeBranch, highlightRequest]);

  useEffect(() => {
    if (tab === 'outgoing') setSubFilter('sent');
    else if (tab === 'incoming') setSubFilter('sent');
    else if (tab === 'dispatches') setSubFilter('picking_finished');
    else if (tab === 'receipts') setSubFilter('in_transit');
  }, [tab]);

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
    return Array.from(map.values()).map((g) => {
      const items = g.items;
      const stage = groupFulfillmentStage(items);
      const totalQty = items.reduce((a, i) => a + Number(i.requested_qty || 0), 0);
      const totalValue = items.reduce((a, i) => {
        const line = i.total_value ?? i.value_at_request ?? i.value;
        return a + Number(line || 0);
      }, 0);
      const hours = Math.max(0, ...items.map((i) => Number(i.in_transit_hours || 0)));
      const level = items.map((i) => i.in_transit_level).find(Boolean);
      return {
        ...g,
        fulfillment_stage: stage,
        total_items: items.length,
        total_qty: totalQty,
        total_value: totalValue,
        in_transit_hours: hours,
        in_transit_level: level,
        status: items.some((i) => (i.status || 'Requested') === 'Requested') ? 'Requested' : (g.status || items[0]?.status),
      };
    }).filter((g) => {
      const stage = g.fulfillment_stage;
      if (tab === 'outgoing') return ['sent', 'picking', 'picking_finished'].includes(stage);
      if (tab === 'incoming') return ['sent', 'picking'].includes(stage);
      if (tab === 'dispatches') return ['picking_finished', 'in_transit', 'finished'].includes(stage);
      if (tab === 'receipts') return ['in_transit', 'finished'].includes(stage);
      return Boolean(stage);
    }).filter((g) => !subFilter || g.fulfillment_stage === subFilter);
  }, [rows, tab, subFilter]);

  useEffect(() => {
    if (!highlightRequest) return;
    const el = document.querySelector(`[data-request-number="${highlightRequest}"]`);
    if (el && typeof el.scrollIntoView === 'function') {
      el.scrollIntoView({ block: 'center', behavior: 'smooth' });
    }
  }, [highlightRequest, groups]);

  const updateDraft = (item, field, value) => setItemDrafts((p) => ({ ...p, [item.id]: { accepted_qty: item.accepted_qty ?? item.approved_qty ?? item.requested_qty, remarks: item.approval_remarks || '', ...(p[item.id] || {}), [field]: value } }));
  const draftFor = (item) => itemDrafts[item.id] || { accepted_qty: item.accepted_qty ?? item.approved_qty ?? item.requested_qty, remarks: item.approval_remarks || '' };

  const startPicking = async (item) => {
    const draft = draftFor(item);
    const acceptedQty = Number(draft.accepted_qty);
    const requestedQty = Number(item.requested_qty || 0);
    if (!Number.isFinite(acceptedQty)) return toast.error('Accepted Quantity must be numeric');
    if (acceptedQty < 0 || acceptedQty > requestedQty) return toast.error('Accepted Quantity must be between 0 and Requested Quantity');
    if (acceptedQty < requestedQty && !String(draft.remarks || '').trim()) return toast.error('Remark is required for Partial or Rejected responses');
    setLoading(true);
    try {
      await axios.post(`${API}/requests/${item.id}/approve`, { accepted_qty: acceptedQty, remarks: draft.remarks || '' });
      toast.success(acceptedQty === requestedQty ? 'Picking started' : acceptedQty === 0 ? 'Part marked not available' : 'Partial picking started');
      await load();
    } catch (e) { toast.error(e.response?.data?.detail || 'Unable to start picking'); }
    finally { setLoading(false); }
  };

  const finishPicking = async (group) => {
    setLoading(true);
    try {
      if (group.request_number) {
        await axios.post(`${API}/requests/group/${encodeURIComponent(group.request_number)}/picking-finished`);
      } else {
        const actionable = group.items.filter((i) => ['Approved', 'Partially Approved'].includes(i.status) && !i.picking_finished_at);
        await Promise.all(actionable.map((i) => axios.post(`${API}/requests/${i.id}/picking-finished`)));
      }
      toast.success('Picking finished');
      await load();
    } catch (e) { toast.error(e.response?.data?.detail || 'Unable to finish picking'); }
    finally { setLoading(false); }
  };

  const confirmReceived = async (group) => {
    setLoading(true);
    try {
      const actionable = group.items.filter((i) => i.status === 'Dispatched' && Number(i.accepted_qty ?? i.approved_qty ?? 0) > 0);
      if (!actionable.length) throw new Error('No dispatched items to confirm');
      await Promise.all(actionable.map((i) => axios.post(`${API}/requests/${i.id}/receive`, {})));
      toast.success('Receipt confirmed');
      await load();
    } catch (e) { toast.error(e.response?.data?.detail || e.message || 'Unable to confirm received'); }
    finally { setLoading(false); }
  };

  const cancelGroup = async (group) => {
    setLoading(true);
    try {
      const actionable = group.items.filter((i) => ['Requested', 'Approved', 'Partially Approved'].includes(i.status));
      await Promise.all(actionable.map((i) => axios.post(`${API}/requests/${i.id}/cancel`, { remarks: 'Request cancelled' })));
      toast.success('Request cancelled'); await load();
    } catch (e) { toast.error(e.response?.data?.detail || 'Unable to cancel request'); }
    finally { setLoading(false); }
  };

  const submitDispatch = async (group) => {
    const form = dispatchForm;
    if (!form?.document_no || !form?.document_date || !form?.document_value || !form?.file) {
      return toast.error('Please complete the document details and upload the document before dispatch.');
    }
    setLoading(true);
    try {
      const data = new FormData();
      data.append('file', form.file);
      data.append('document_no', form.document_no);
      data.append('document_date', form.document_date);
      data.append('document_value', form.document_value);
      data.append('remarks', form.remarks || '');
      if (group.request_number) {
        await axios.post(`${API}/requests/group/${encodeURIComponent(group.request_number)}/dispatch-document`, data);
      } else {
        await axios.post(`${API}/requests/${group.items[0].id}/dispatch-document`, data);
      }
      const actionable = group.items.filter((i) => ['Approved', 'Partially Approved'].includes(i.status) && Number(i.accepted_qty ?? i.approved_qty ?? 0) > 0);
      await Promise.all(actionable.map((i) => axios.post(`${API}/requests/${i.id}/dispatch`, { remarks: form.remarks || '' })));
      toast.success('Dispatched');
      setDispatchForm(null);
      await load();
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Unable to dispatch');
    } finally { setLoading(false); }
  };

  const subFilters = tab === 'outgoing'
    ? [['sent', 'Request Sent'], ['picking', 'Picking'], ['picking_finished', 'Picking Finished']]
    : tab === 'incoming'
      ? [['sent', 'Request Received'], ['picking', 'Picking']]
      : tab === 'dispatches'
        ? [['picking_finished', 'Ready to Dispatch'], ['in_transit', 'In Transit'], ['finished', 'Finished']]
        : [['in_transit', 'In Transit'], ['finished', 'Finished']];

  return <div className="space-y-6" data-testid="requests-page">
    <div className="rounded-xl border bg-white overflow-hidden shadow-sm">
      <div className="flex flex-wrap gap-2 p-4 border-b">
        <Button variant={tab==='outgoing'?'default':'outline'} onClick={()=>setTab('outgoing')}>My Requests</Button>
        <Button variant={tab==='receipts'?'default':'outline'} onClick={()=>setTab('receipts')}>My Receipts</Button>
        <Button variant={tab==='incoming'?'default':'outline'} onClick={()=>setTab('incoming')}>Requests To Me</Button>
        <Button variant={tab==='dispatches'?'default':'outline'} onClick={()=>setTab('dispatches')}>My Dispatches</Button>
        <div className="mx-2 h-9 border-l"/>
        {subFilters.map(([key, label]) => (
          <Button key={key} variant={subFilter===key?'default':'outline'} onClick={()=>setSubFilter(key)}>{label}</Button>
        ))}
        <div className="ml-auto flex gap-2"><div className="relative"><Search className="absolute left-2 top-2.5 h-4 w-4 text-slate-400"/><input value={search} onChange={e=>setSearch(e.target.value)} onKeyDown={e=>e.key==='Enter'&&load()} placeholder="Request / Order / Part No" className="h-9 rounded border pl-8 pr-3 text-sm"/></div><Button size="sm" variant="outline" onClick={load}><RefreshCw className="mr-2 h-4 w-4"/>Refresh</Button></div>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[1100px] text-sm">
          <thead className="bg-emerald-50">
            <tr>
              {['', 'Request No', 'Order No', 'Sending Dealer/Branch', 'Receiving Dealer/Branch', 'Items', 'Qty', 'Value', 'Status'].map((h) => (
                <th key={h || 'exp'} className="p-3 text-left">{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {groups.map((g) => {
              const open = !!expanded[g.key];
              const sending = `${g.supplying_dealer || '-'} / ${g.supplying_branch || '-'}`;
              const receiving = `${g.requesting_dealer || '-'} / ${g.requesting_branch || '-'}`;
              const deadline = g.response_deadline || g.items.find((i) => i.response_deadline)?.response_deadline;
              const unresolved = g.items.some((i) => (i.status || 'Requested') === 'Requested');
              const frozen = g.timer_frozen || ['responded', 'cancelled', 'timeout'].includes(g.response_status);
              const countdown = (unresolved && !frozen) ? formatDeadlineCountdown(deadline, nowMs) : '';
              const transit = inTransitLevel(g);
              const highlight = transit === '72h' ? 'bg-rose-50' : transit === '48h' ? 'bg-amber-50' : transit === '24h' ? 'bg-yellow-50' : '';
              return (
                <React.Fragment key={g.key}>
                  <tr data-request-number={g.request_number || ''} className={`border-t ${highlight} ${highlightRequest && g.request_number === highlightRequest ? 'ring-2 ring-emerald-600' : ''}`}>
                    <td className="p-3"><button onClick={()=>setExpanded((p)=>({...p,[g.key]:!open}))}>{open?<ChevronDown className="h-5 w-5"/>:<ChevronRight className="h-5 w-5"/>}</button></td>
                    <td className="p-3"><button className="text-left font-bold text-emerald-700" onClick={()=>setExpanded((p)=>({...p,[g.key]:!open}))}>{g.request_number || 'Legacy Request'}</button></td>
                    <td className="p-3">{g.order_number || '-'}</td>
                    <td className="p-3">{sending}</td>
                    <td className="p-3">{receiving}</td>
                    <td className="p-3 font-semibold">{g.total_items}</td>
                    <td className="p-3 font-semibold">{nfmt(g.total_qty)}</td>
                    <td className="p-3 font-semibold">{valueCell(g.total_value)}</td>
                    <td className="p-3"><StatusBadge status={g.status}/>{countdown && <div className="mt-1 text-xs font-semibold text-amber-800">{countdown}</div>}{transit && <div className="mt-1 text-xs font-semibold text-rose-800">In transit {transit}</div>}</td>
                  </tr>
                  {open && (
                    <tr className="bg-slate-50 border-t">
                      <td colSpan={9} className="p-4">
                        <div className="rounded-xl border bg-white p-4">
                          <div className="grid gap-4 lg:grid-cols-[1fr_48px_1fr]">
                            <ScopeCard title="Stock Source (From)" subtitle="Supplying Location" brand={g.supplying_brand || g.requesting_brand} dealer={g.supplying_dealer} branch={g.supplying_branch} users={g.receiver_users}/>
                            <div className="hidden items-center justify-center lg:flex"><ArrowRight className="h-7 w-7 text-emerald-600"/></div>
                            <ScopeCard title="Stock Destination (To)" subtitle="Requesting Location" brand={g.requesting_brand} dealer={g.requesting_dealer} branch={g.requesting_branch} requestedBy={{name:g.requested_user_name,id:g.requested_user_id}}/>
                          </div>
                          <div className="mt-5 overflow-x-auto">
                            <div className="mb-2 flex items-center gap-2 font-semibold"><Package className="h-4 w-4 text-emerald-600"/>Part details</div>
                            <table className="w-full min-w-[1600px] text-sm">
                              <thead className="bg-emerald-50">
                                <tr>{['Part No','Description','Requested Qty','Available Qty','Location','Purchase Aging','Sales Aging','Accepted Qty','Balance Qty','Part Status','Remarks','Action'].map((h)=><th key={h} className="p-3 text-left">{h}</th>)}</tr>
                              </thead>
                              <tbody>
                                {g.items.map((i) => {
                                  const d = draftFor(i);
                                  const canStart = tab === 'incoming' && i.status === 'Requested';
                                  const accepted = Number(i.accepted_qty ?? i.approved_qty ?? 0);
                                  const requested = Number(i.requested_qty || 0);
                                  const balance = i.balance_qty != null ? Number(i.balance_qty) : Math.max(0, requested - accepted);
                                  return (
                                    <tr key={i.id} className="border-t">
                                      <td className="p-3 font-semibold">{i.part_number}</td>
                                      <td className="p-3">{i.description || i.part_name || '-'}</td>
                                      <td className="p-3">{nfmt(i.requested_qty)}</td>
                                      <td className="p-3">{nfmt(i.available_qty_at_request ?? i.available_qty ?? 0)}</td>
                                      <td className="p-3">{i.loc_at_request || i.loc || i.location || '-'}</td>
                                      <td className="p-3">{i.purchase_aging_days_at_request ?? i.purchase_aging_at_request ?? '-'}</td>
                                      <td className="p-3">{i.sales_aging_days_at_request ?? i.sales_aging_at_request ?? '-'}</td>
                                      <td className="p-3">{canStart
                                        ? <input type="number" min="0" max={requested} step="any" value={d.accepted_qty} onChange={(e)=>updateDraft(i,'accepted_qty',e.target.value)} className="h-9 w-28 rounded border px-2 font-semibold"/>
                                        : <span className="font-semibold">{nfmt(accepted)}</span>}
                                      </td>
                                      <td className="p-3">{nfmt(canStart ? Math.max(0, requested - Number(d.accepted_qty || 0)) : balance)}</td>
                                      <td className="p-3"><StatusBadge status={itemPartStatus(i)}/></td>
                                      <td className="p-3">{canStart
                                        ? <input value={d.remarks} onChange={(e)=>updateDraft(i,'remarks',e.target.value)} placeholder={Number(d.accepted_qty)<requested?'Remark required':'Item remarks'} className="h-9 w-44 rounded border px-2"/>
                                        : (i.approval_remarks || i.remarks || '-')}
                                      </td>
                                      <td className="p-3">{canStart
                                        ? <Button size="sm" disabled={loading} onClick={()=>startPicking(i)}><Send className="mr-1 h-4 w-4"/>Start Picking</Button>
                                        : '—'}
                                      </td>
                                    </tr>
                                  );
                                })}
                              </tbody>
                            </table>
                          </div>
                          <div className="mt-4 flex flex-wrap justify-end gap-2">
                            <Button variant="outline" onClick={()=>openRequestCenterPdf(g)}><Printer className="mr-1 h-4 w-4"/>Print Request</Button>
                            {tab === 'incoming' && g.items.some((i)=>['Approved','Partially Approved'].includes(i.status) && !i.picking_finished_at) && (
                              <Button disabled={loading} onClick={()=>finishPicking(g)}>Picking Finished</Button>
                            )}
                            {tab === 'dispatches' && g.fulfillment_stage === 'picking_finished' && (
                              dispatchForm?.key === g.key ? (
                                <div className="flex flex-wrap items-end gap-2 rounded-lg border bg-slate-50 p-3">
                                  <label className="text-xs">Document No<input className="mt-1 h-9 rounded border px-2" value={dispatchForm.document_no} onChange={(e)=>setDispatchForm((p)=>({...p, document_no:e.target.value}))}/></label>
                                  <label className="text-xs">Document Date<input type="date" className="mt-1 h-9 rounded border px-2" value={dispatchForm.document_date} onChange={(e)=>setDispatchForm((p)=>({...p, document_date:e.target.value}))}/></label>
                                  <label className="text-xs">Document Value<input className="mt-1 h-9 rounded border px-2" value={dispatchForm.document_value} onChange={(e)=>setDispatchForm((p)=>({...p, document_value:e.target.value}))}/></label>
                                  <label className="text-xs">Document<input type="file" className="mt-1 block" onChange={(e)=>setDispatchForm((p)=>({...p, file:e.target.files?.[0] || null}))}/></label>
                                  <Button disabled={loading} onClick={()=>submitDispatch(g)}>Dispatch</Button>
                                  <Button variant="outline" onClick={()=>setDispatchForm(null)}>Cancel</Button>
                                </div>
                              ) : (
                                <Button disabled={loading} onClick={()=>setDispatchForm({ key: g.key, document_no:'', document_date:'', document_value:'', remarks:'', file:null })}>Dispatch</Button>
                              )
                            )}
                            {tab === 'receipts' && g.fulfillment_stage === 'in_transit' && (
                              <Button disabled={loading} onClick={()=>confirmReceived(g)}>Confirm Received</Button>
                            )}
                            {(tab === 'outgoing' || tab === 'incoming') && g.items.some((i)=>['Requested','Approved','Partially Approved'].includes(i.status)) && (
                              <Button variant="outline" disabled={loading} onClick={()=>cancelGroup(g)}><Ban className="mr-1 h-4 w-4"/>Cancel Request</Button>
                            )}
                          </div>
                        </div>
                      </td>
                    </tr>
                  )}
                </React.Fragment>
              );
            })}
          </tbody>
        </table>
        {!groups.length && <div className="p-12 text-center text-slate-500">{loading?'Loading requests…':'No requests found in this section.'}</div>}
      </div>
    </div>
  </div>;
}
