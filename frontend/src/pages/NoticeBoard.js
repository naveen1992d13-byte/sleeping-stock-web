import React, { useCallback, useEffect, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import axios from 'axios';
import { API, useAuth } from '@/App';
import { Button } from '@/components/ui/button';
import { Textarea } from '@/components/ui/textarea';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
} from '@/components/ui/sheet';
import { Megaphone, Plus, Eye, Paperclip, RefreshCw } from 'lucide-react';
import { toast } from 'sonner';

const BACKEND_ROOT = process.env.REACT_APP_BACKEND_URL || 'http://127.0.0.1:8000';

const NOTICE_TYPES = [
  'General Notice',
  'Important Alert',
  'Appreciation',
  'System Update',
  'Policy / Process Update',
  'Action Required',
];
const PRIORITIES = ['Normal', 'Important', 'Urgent'];
const MASTER_STATUSES = ['', 'Draft', 'Published', 'Cancelled', 'Expired'];

const STATUS_STYLE = {
  Draft: { bg: '#F3F4F6', fg: '#374151' },
  Published: { bg: '#D1FAE5', fg: '#065F46' },
  Cancelled: { bg: '#FEE2E2', fg: '#991B1B' },
  Expired: { bg: '#E5E7EB', fg: '#6B7280' },
};

function fmtIst(value) {
  if (!value) return '-';
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return '-';
  return d.toLocaleString('en-GB', {
    timeZone: 'Asia/Kolkata',
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).replace(',', '');
}

function Badge({ status }) {
  const s = STATUS_STYLE[status] || STATUS_STYLE.Draft;
  return (
    <span className="inline-block rounded-full px-2.5 py-1 text-xs font-semibold" style={{ backgroundColor: s.bg, color: s.fg }}>
      {status}
    </span>
  );
}

function emptyCreateForm() {
  return {
    subject: '',
    content: '',
    notice_type: 'General Notice',
    priority: 'Normal',
    audience_type: 'all_brands',
    brand_name: '',
    popup_required: true,
    acknowledgement_required: false,
    publish_date: '',
    expiry_date: '',
    pdf: null,
  };
}

export function NoticeBoard() {
  const { user } = useAuth();
  const isMaster = user?.role === 'master';
  const [params, setParams] = useSearchParams();

  const [records, setRecords] = useState([]);
  const [loading, setLoading] = useState(false);
  const [page, setPage] = useState(1);
  const [totalPages, setTotalPages] = useState(1);
  const [statusFilter, setStatusFilter] = useState(isMaster ? '' : 'Published');
  const [readFilter, setReadFilter] = useState('');
  const [typeFilter, setTypeFilter] = useState('');
  const [search, setSearch] = useState('');

  const [createOpen, setCreateOpen] = useState(false);
  const [createForm, setCreateForm] = useState(emptyCreateForm());
  const [creating, setCreating] = useState(false);

  const [detail, setDetail] = useState(null);
  const [detailOpen, setDetailOpen] = useState(false);
  const [tracking, setTracking] = useState(null);
  const [trackingOpen, setTrackingOpen] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await axios.get(`${API}/notice-board/notices`, {
        params: {
          page,
          page_size: 20,
          status: statusFilter || undefined,
          search: search || undefined,
          read_filter: !isMaster && readFilter ? readFilter : undefined,
          notice_type: typeFilter || undefined,
        },
      });
      setRecords(res.data?.records || []);
      setTotalPages(res.data?.total_pages || 1);
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Unable to load notices');
    } finally {
      setLoading(false);
    }
  }, [page, statusFilter, search, readFilter, typeFilter, isMaster]);

  useEffect(() => {
    load();
  }, [load]);

  const openDetail = async (id, markRead = false) => {
    try {
      const res = await axios.get(`${API}/notice-board/notices/${id}`);
      setDetail(res.data);
      setDetailOpen(true);
      if (!isMaster && markRead) {
        await axios.post(`${API}/notice-board/notices/${id}/read`);
        load();
      }
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Notice not available');
    }
  };

  useEffect(() => {
    const nid = params.get('notice');
    if (nid) {
      openDetail(nid, true);
      params.delete('notice');
      setParams(params, { replace: true });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [params.get('notice')]);

  const downloadPdf = async (attachment) => {
    if (!attachment?.file_url) return;
    const href = attachment.file_url.startsWith('http') ? attachment.file_url : `${BACKEND_ROOT}${attachment.file_url}`;
    try {
      const res = await axios.get(href, { responseType: 'blob' });
      const url = window.URL.createObjectURL(new Blob([res.data], { type: 'application/pdf' }));
      const a = document.createElement('a');
      a.href = url;
      a.download = attachment.file_name || 'notice.pdf';
      a.click();
      window.URL.revokeObjectURL(url);
      if (!isMaster && detail?.id) {
        await axios.post(`${API}/notice-board/notices/${detail.id}/read`);
        load();
      }
    } catch {
      toast.error('Unable to download PDF');
    }
  };

  const submitCreate = async (e) => {
    e.preventDefault();
    if (!createForm.subject.trim() || !createForm.content.trim()) {
      toast.error('Subject and content are required');
      return;
    }
    if (createForm.audience_type === 'selected_brand' && !createForm.brand_name.trim()) {
      toast.error('Select a brand audience');
      return;
    }
    if (createForm.pdf && createForm.pdf.type !== 'application/pdf' && !createForm.pdf.name.toLowerCase().endsWith('.pdf')) {
      toast.error('Only PDF attachments are allowed');
      return;
    }
    setCreating(true);
    try {
      const payload = {
        subject: createForm.subject.trim(),
        content: createForm.content.trim(),
        notice_type: createForm.notice_type,
        priority: createForm.priority,
        audience_type: createForm.audience_type,
        brand_name: createForm.audience_type === 'selected_brand' ? createForm.brand_name : null,
        popup_required: createForm.popup_required,
        acknowledgement_required: createForm.acknowledgement_required,
        publish_date: createForm.publish_date || null,
        expiry_date: createForm.expiry_date || null,
      };
      const res = await axios.post(`${API}/notice-board/notices`, payload);
      const id = res.data?.id;
      if (createForm.pdf && id) {
        const fd = new FormData();
        fd.append('file', createForm.pdf);
        await axios.post(`${API}/notice-board/notices/${id}/pdf`, fd, {
          headers: { 'Content-Type': 'multipart/form-data' },
        });
      }
      toast.success('Notice draft created');
      setCreateOpen(false);
      setCreateForm(emptyCreateForm());
      setStatusFilter('Draft');
      setPage(1);
      await load();
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Unable to create notice');
    } finally {
      setCreating(false);
    }
  };

  const publishNotice = async (id) => {
    if (!window.confirm('Publish this notice to eligible users?')) return;
    try {
      await axios.post(`${API}/notice-board/notices/${id}/publish`);
      toast.success('Notice published');
      load();
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Publish failed');
    }
  };

  const cancelNotice = async (id) => {
    const reason = window.prompt('Cancellation reason (optional):') ?? '';
    if (!window.confirm('Cancel this notice?')) return;
    try {
      await axios.post(`${API}/notice-board/notices/${id}/cancel`, { reason });
      toast.success('Notice cancelled');
      load();
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Cancel failed');
    }
  };

  const expireNotice = async (id) => {
    if (!window.confirm('Mark this notice as expired?')) return;
    try {
      await axios.post(`${API}/notice-board/notices/${id}/expire`);
      toast.success('Notice expired');
      load();
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Expire failed');
    }
  };

  const openTracking = async (id) => {
    try {
      const res = await axios.get(`${API}/notice-board/notices/${id}/tracking`);
      setTracking(res.data);
      setTrackingOpen(true);
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Unable to load tracking');
    }
  };

  const acknowledge = async () => {
    if (!detail?.id) return;
    try {
      await axios.post(`${API}/notice-board/notices/${detail.id}/acknowledge`);
      toast.success('Notice acknowledged');
      const res = await axios.get(`${API}/notice-board/notices/${detail.id}`);
      setDetail(res.data);
      load();
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Acknowledgement failed');
    }
  };

  const summary = isMaster
    ? {
        active: records.filter((r) => r.status === 'Published').length,
        draft: records.filter((r) => r.status === 'Draft').length,
      }
    : null;

  return (
    <div className="space-y-6" data-testid="notice-board-page">
      <div className="nmts-module-header">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-3">
            <Megaphone className="h-8 w-8 nmts-module-header-icon" />
            <div>
              <h1>Notice Board</h1>
              <p>
                {isMaster ? 'Create and manage brand-wise notices' : `Notices for ${user?.brand || 'your brand'}`}
              </p>
            </div>
          </div>
          {isMaster && (
            <Button onClick={() => setCreateOpen(true)} className="nmts-btn-primary">
              <Plus className="h-4 w-4 mr-2" />
              Create Notice
            </Button>
          )}
        </div>
      </div>

      {isMaster && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          {MASTER_STATUSES.filter(Boolean).map((s) => (
            <button
              key={s}
              type="button"
              onClick={() => { setStatusFilter(s); setPage(1); }}
              className={`rounded-xl border p-3 text-left ${statusFilter === s ? 'border-emerald-600 bg-emerald-50' : 'bg-white'}`}
            >
              <div className="text-xs text-slate-500">{s}</div>
              <div className="font-semibold">{s === statusFilter ? records.length : '—'}</div>
            </button>
          ))}
        </div>
      )}

      <div className="rounded-xl border bg-white p-4 space-y-3">
        <div className="flex flex-wrap gap-2">
          {!isMaster && (
            <>
              <select className="h-9 rounded border px-2 text-sm" value={readFilter} onChange={(e) => setReadFilter(e.target.value)}>
                <option value="">All</option>
                <option value="unread">Unread</option>
              </select>
              <select className="h-9 rounded border px-2 text-sm" value={typeFilter} onChange={(e) => setTypeFilter(e.target.value)}>
                <option value="">All types</option>
                {NOTICE_TYPES.map((t) => <option key={t} value={t}>{t}</option>)}
              </select>
            </>
          )}
          {isMaster && (
            <select className="h-9 rounded border px-2 text-sm" value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}>
              <option value="">All statuses</option>
              {MASTER_STATUSES.filter(Boolean).map((s) => <option key={s} value={s}>{s}</option>)}
            </select>
          )}
          <input
            className="h-9 rounded border px-2 text-sm flex-1 min-w-[160px]"
            placeholder="Search subject…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && load()}
          />
          <Button size="sm" variant="outline" onClick={load} disabled={loading}>
            <RefreshCw className={`h-4 w-4 ${loading ? 'animate-spin' : ''}`} />
          </Button>
        </div>

        <div className="overflow-x-auto">
          <table className="min-w-full text-sm">
            <thead className="bg-emerald-700 text-white">
              <tr>
                {(isMaster
                  ? ['Published', 'Subject', 'Type', 'Brand', 'Priority', 'Status', 'Read', 'Ack', 'Actions']
                  : ['Published', 'Subject', 'Type', 'Priority', 'Description', 'Read', 'Ack', 'Actions']
                ).map((h) => (
                  <th key={h} className="px-3 py-2 text-left whitespace-nowrap">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {loading && (
                <tr><td colSpan={9} className="py-8 text-center text-slate-500">Loading…</td></tr>
              )}
              {!loading && records.length === 0 && (
                <tr><td colSpan={9} className="py-8 text-center text-slate-500">No notices found.</td></tr>
              )}
              {!loading && records.map((row) => (
                <tr key={row.id} className="border-t hover:bg-slate-50">
                  <td className="px-3 py-2 whitespace-nowrap">{fmtIst(row.publish_date || row.published_at)}</td>
                  <td className="px-3 py-2 font-medium max-w-xs truncate" title={row.subject}>{row.subject}</td>
                  <td className="px-3 py-2">{row.notice_type}</td>
                  {isMaster && (
                    <td className="px-3 py-2">{row.audience_type === 'all_brands' ? 'All Brands' : row.brand_name}</td>
                  )}
                  <td className="px-3 py-2">{row.priority}</td>
                  {!isMaster && (
                    <td className="px-3 py-2 max-w-sm truncate text-slate-600">{row.content}</td>
                  )}
                  {isMaster && <td className="px-3 py-2"><Badge status={row.status} /></td>}
                  {isMaster && (
                    <>
                      <td className="px-3 py-2">{row.tracking_summary?.read_users ?? 0}/{row.tracking_summary?.eligible_users ?? 0}</td>
                      <td className="px-3 py-2">{row.tracking_summary?.acknowledged_users ?? 0}</td>
                    </>
                  )}
                  {!isMaster && (
                    <>
                      <td className="px-3 py-2">{row.user_read_status || 'Unread'}</td>
                      <td className="px-3 py-2">{row.acknowledgement_required ? row.user_ack_status : '—'}</td>
                    </>
                  )}
                  <td className="px-3 py-2 whitespace-nowrap space-x-1">
                    <Button size="sm" variant="outline" onClick={() => openDetail(row.id, !isMaster)}>
                      <Eye className="h-4 w-4" />
                    </Button>
                    {isMaster && row.status === 'Draft' && (
                      <Button size="sm" onClick={() => publishNotice(row.id)}>Publish</Button>
                    )}
                    {isMaster && row.status === 'Published' && (
                      <>
                        <Button size="sm" variant="outline" onClick={() => openTracking(row.id)}>Track</Button>
                        <Button size="sm" variant="outline" onClick={() => cancelNotice(row.id)}>Cancel</Button>
                        <Button size="sm" variant="outline" onClick={() => expireNotice(row.id)}>Expire</Button>
                      </>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="flex justify-between items-center text-sm">
          <Button size="sm" variant="outline" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>Previous</Button>
          <span>Page {page} of {totalPages}</span>
          <Button size="sm" variant="outline" disabled={page >= totalPages} onClick={() => setPage((p) => p + 1)}>Next</Button>
        </div>
      </div>

      <Dialog open={createOpen} onOpenChange={setCreateOpen}>
        <DialogContent className="max-w-lg max-h-[90vh] overflow-y-auto">
          <DialogHeader><DialogTitle>Create Notice</DialogTitle></DialogHeader>
          <form onSubmit={submitCreate} className="space-y-3">
            <input className="w-full border rounded px-3 py-2 text-sm" placeholder="Subject *" maxLength={300} value={createForm.subject} onChange={(e) => setCreateForm({ ...createForm, subject: e.target.value })} required />
            <Textarea placeholder="Short content / description *" value={createForm.content} onChange={(e) => setCreateForm({ ...createForm, content: e.target.value })} required />
            <select className="w-full border rounded px-3 py-2 text-sm" value={createForm.notice_type} onChange={(e) => setCreateForm({ ...createForm, notice_type: e.target.value })}>
              {NOTICE_TYPES.map((t) => <option key={t}>{t}</option>)}
            </select>
            <select className="w-full border rounded px-3 py-2 text-sm" value={createForm.priority} onChange={(e) => setCreateForm({ ...createForm, priority: e.target.value })}>
              {PRIORITIES.map((p) => <option key={p}>{p}</option>)}
            </select>
            <select className="w-full border rounded px-3 py-2 text-sm" value={createForm.audience_type} onChange={(e) => setCreateForm({ ...createForm, audience_type: e.target.value })}>
              <option value="all_brands">All Brands</option>
              <option value="selected_brand">Selected Brand</option>
            </select>
            {createForm.audience_type === 'selected_brand' && (
              <input className="w-full border rounded px-3 py-2 text-sm" placeholder="Brand name (e.g. Hyundai)" value={createForm.brand_name} onChange={(e) => setCreateForm({ ...createForm, brand_name: e.target.value })} />
            )}
            <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={createForm.popup_required} onChange={(e) => setCreateForm({ ...createForm, popup_required: e.target.checked })} /> Popup required</label>
            <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={createForm.acknowledgement_required} onChange={(e) => setCreateForm({ ...createForm, acknowledgement_required: e.target.checked })} /> Acknowledgement required</label>
            <input type="datetime-local" className="w-full border rounded px-3 py-2 text-sm" value={createForm.publish_date} onChange={(e) => setCreateForm({ ...createForm, publish_date: e.target.value })} />
            <input type="datetime-local" className="w-full border rounded px-3 py-2 text-sm" value={createForm.expiry_date} onChange={(e) => setCreateForm({ ...createForm, expiry_date: e.target.value })} />
            <input type="file" accept="application/pdf,.pdf" className="text-sm" onChange={(e) => setCreateForm({ ...createForm, pdf: e.target.files?.[0] || null })} />
            <Button type="submit" disabled={creating}>{creating ? 'Saving…' : 'Save as Draft'}</Button>
          </form>
        </DialogContent>
      </Dialog>

      <Sheet open={detailOpen} onOpenChange={setDetailOpen}>
        <SheetContent className="w-full max-w-full sm:max-w-xl overflow-y-auto">
          <SheetHeader><SheetTitle>{detail?.subject || 'Notice'}</SheetTitle></SheetHeader>
          {detail && (
            <div className="mt-4 space-y-3 text-sm">
              <Badge status={detail.status} />
              <p><span className="text-slate-500">Type:</span> {detail.notice_type}</p>
              <p><span className="text-slate-500">Priority:</span> {detail.priority}</p>
              <p><span className="text-slate-500">Published:</span> {fmtIst(detail.publish_date || detail.published_at)}</p>
              <p className="whitespace-pre-wrap">{detail.content}</p>
              {detail.attachment && (
                <Button variant="outline" size="sm" onClick={() => downloadPdf(detail.attachment)}>
                  <Paperclip className="h-4 w-4 mr-2" /> View PDF
                </Button>
              )}
              {!isMaster && detail.acknowledgement_required && (
                <div className="rounded-lg border p-3 bg-slate-50">
                  <p className="mb-2">I have read and understood this notice.</p>
                  <Button onClick={acknowledge} disabled={detail.user_ack_status === 'Acknowledged'}>Acknowledge</Button>
                </div>
              )}
            </div>
          )}
        </SheetContent>
      </Sheet>

      <Sheet open={trackingOpen} onOpenChange={setTrackingOpen}>
        <SheetContent className="w-full max-w-full sm:max-w-2xl overflow-y-auto">
          <SheetHeader><SheetTitle>Notice tracking</SheetTitle></SheetHeader>
          {tracking?.summary && (
            <div className="mt-4 grid grid-cols-2 gap-2 text-sm">
              <div className="rounded border p-2">Eligible: <b>{tracking.summary.eligible_users}</b></div>
              <div className="rounded border p-2">Read: <b>{tracking.summary.read_users}</b></div>
              <div className="rounded border p-2">Unread: <b>{tracking.summary.unread_users}</b></div>
              <div className="rounded border p-2">Acknowledged: <b>{tracking.summary.acknowledged_users}</b></div>
            </div>
          )}
          <div className="mt-4 overflow-x-auto">
            <table className="min-w-full text-xs">
              <thead><tr className="bg-slate-100"><th className="p-2 text-left">User</th><th className="p-2">Read</th><th className="p-2">Ack</th></tr></thead>
              <tbody>
                {(tracking?.records || []).map((r) => (
                  <tr key={r.user_id} className="border-t">
                    <td className="p-2">{r.user_name}</td>
                    <td className="p-2">{r.read_status} {r.read_at ? fmtIst(r.read_at) : ''}</td>
                    <td className="p-2">{r.acknowledgement_status}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </SheetContent>
      </Sheet>
    </div>
  );
}

export default NoticeBoard;
