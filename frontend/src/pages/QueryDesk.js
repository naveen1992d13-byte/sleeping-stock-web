import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { useOutletContext } from 'react-router-dom';
import axios from 'axios';
import { API, useAuth } from '@/App';
import { Button } from '@/components/ui/button';
import { Textarea } from '@/components/ui/textarea';
import { NmtsModal } from '@/components/NmtsModal';
import {
  HelpCircle,
  Search,
  RefreshCw,
  Eye,
  Paperclip,
  Send,
  RotateCcw,
} from 'lucide-react';
import { toast } from 'sonner';

const QUERY_TYPES = ['System', 'General', 'Guidance'];
const STATUS_STYLES = {
  Open: { bg: '#DBEAFE', fg: '#1E40AF' },
  Answered: { bg: '#D1FAE5', fg: '#065F46' },
  Closed: { bg: '#E5E7EB', fg: '#374151' },
};

const ALLOWED_EXT = ['.png', '.jpg', '.jpeg', '.pdf', '.xls', '.xlsx'];
const BACKEND_ROOT = process.env.REACT_APP_BACKEND_URL || 'http://127.0.0.1:8000';

function formatIstDateTime(value) {
  if (!value) return '-';
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return '-';
  const parts = new Intl.DateTimeFormat('en-GB', {
    timeZone: 'Asia/Kolkata',
    day: '2-digit',
    month: 'short',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    hour12: true,
  }).formatToParts(d);
  const pick = (type) => parts.find((p) => p.type === type)?.value || '';
  const ampm = pick('dayPeriod').toUpperCase();
  return `${pick('day')} ${pick('month')} ${pick('year')} ${pick('hour')}:${pick('minute')} ${ampm}`;
}

function StatusBadge({ status }) {
  const s = STATUS_STYLES[status] || { bg: '#F3F4F6', fg: '#374151' };
  return (
    <span
      className="inline-block rounded-full px-2.5 py-1 text-xs font-semibold whitespace-nowrap"
      style={{ backgroundColor: s.bg, color: s.fg }}
    >
      {status || 'Open'}
    </span>
  );
}

function AttachmentLink({ attachment }) {
  if (!attachment?.file_url) return <span className="text-slate-500">—</span>;
  const href = attachment.file_url.startsWith('http')
    ? attachment.file_url
    : `${BACKEND_ROOT}${attachment.file_url}`;

  const download = async (e) => {
    e.preventDefault();
    try {
      const res = await axios.get(href, { responseType: 'blob' });
      const blobUrl = window.URL.createObjectURL(new Blob([res.data]));
      const link = document.createElement('a');
      link.href = blobUrl;
      link.setAttribute('download', attachment.file_name || 'attachment');
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.URL.revokeObjectURL(blobUrl);
    } catch {
      toast.error('Unable to download attachment');
    }
  };

  return (
    <button
      type="button"
      onClick={download}
      className="inline-flex items-center gap-1 text-sm font-medium text-emerald-700 hover:underline"
    >
      <Paperclip className="h-4 w-4" />
      {attachment.file_name || 'Download'}
    </button>
  );
}

const emptyForm = () => ({
  query_type: 'System',
  subject: '',
  description: '',
  file: null,
});

export function QueryDesk() {
  const { user } = useAuth();
  const { scopeBrand, scopeDealer, scopeBranch } = useOutletContext() || {};
  const isMaster = user?.role === 'master';

  const [form, setForm] = useState(emptyForm());
  const [submitting, setSubmitting] = useState(false);
  const [records, setRecords] = useState([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [pageSize] = useState(20);
  const [totalPages, setTotalPages] = useState(1);
  const [loading, setLoading] = useState(false);
  const [searchInput, setSearchInput] = useState('');
  const [search, setSearch] = useState('');
  const [filterType, setFilterType] = useState('');
  const [filterStatus, setFilterStatus] = useState('');
  const [similar, setSimilar] = useState([]);
  const [detailOpen, setDetailOpen] = useState(false);
  const [detail, setDetail] = useState(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [replyMessage, setReplyMessage] = useState('');
  const [replyFile, setReplyFile] = useState(null);
  const [replySending, setReplySending] = useState(false);

  const scopeParams = useMemo(
    () => ({
      brand: scopeBrand || undefined,
      dealer: scopeDealer || undefined,
      branch: scopeBranch || undefined,
    }),
    [scopeBrand, scopeDealer, scopeBranch],
  );

  const loadQueries = useCallback(async () => {
    setLoading(true);
    try {
      const res = await axios.get(`${API}/queries`, {
        params: {
          page,
          page_size: pageSize,
          search: search || undefined,
          query_type: filterType || undefined,
          status: filterStatus || undefined,
        },
      });
      setRecords(res.data?.records || []);
      setTotal(res.data?.total || 0);
      setTotalPages(res.data?.total_pages || 1);
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Unable to load queries');
    } finally {
      setLoading(false);
    }
  }, [page, pageSize, search, filterType, filterStatus]);

  useEffect(() => {
    loadQueries();
  }, [loadQueries]);

  useEffect(() => {
    setPage(1);
  }, [search, filterType, filterStatus, scopeBrand, scopeDealer, scopeBranch]);

  useEffect(() => {
    const subject = form.subject.trim();
    if (subject.length < 3) {
      setSimilar([]);
      return undefined;
    }
    const timer = setTimeout(async () => {
      try {
        const res = await axios.get(`${API}/queries/similar`, { params: { subject } });
        setSimilar(res.data?.records || []);
      } catch {
        setSimilar([]);
      }
    }, 350);
    return () => clearTimeout(timer);
  }, [form.subject]);

  const validateFile = (file) => {
    if (!file) return true;
    const ext = `.${file.name.split('.').pop()?.toLowerCase()}`;
    if (!ALLOWED_EXT.includes(ext)) {
      toast.error('Attachment type not allowed');
      return false;
    }
    if (file.size > 5 * 1024 * 1024) {
      toast.error('Attachment exceeds 5 MB');
      return false;
    }
    return true;
  };

  const resetForm = () => {
    setForm(emptyForm());
    setSimilar([]);
  };

  const clearAttachment = () => {
    setForm((f) => ({ ...f, file: null }));
  };

  const submitQuery = async (e) => {
    e.preventDefault();
    if (!form.subject.trim() || !form.description.trim()) {
      toast.error('Subject and description are required');
      return;
    }
    if (!validateFile(form.file)) return;

    setSubmitting(true);
    try {
      const fd = new FormData();
      fd.append('query_type', form.query_type);
      fd.append('subject', form.subject.trim());
      fd.append('description', form.description.trim());
      if (scopeParams.brand) fd.append('brand', scopeParams.brand);
      if (scopeParams.dealer) fd.append('dealer', scopeParams.dealer);
      if (scopeParams.branch) fd.append('branch', scopeParams.branch);
      if (form.file) fd.append('attachment', form.file);

      const res = await axios.post(`${API}/queries`, fd, {
        headers: { 'Content-Type': 'multipart/form-data' },
      });
      const qno = res.data?.query_no || '';
      toast.success(`Query ${qno} submitted successfully.`);
      resetForm();
      setPage(1);
      await loadQueries();
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Unable to submit query');
    } finally {
      setSubmitting(false);
    }
  };

  const openDetail = async (queryId) => {
    setDetailOpen(true);
    setDetailLoading(true);
    setReplyMessage('');
    setReplyFile(null);
    try {
      const res = await axios.get(`${API}/queries/${queryId}`);
      setDetail(res.data);
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Unable to load query details');
      setDetailOpen(false);
    } finally {
      setDetailLoading(false);
    }
  };

  const sendReply = async () => {
    if (!detail?.id) return;
    if (!replyMessage.trim()) {
      toast.error('Reply message is required');
      return;
    }
    if (!validateFile(replyFile)) return;
    setReplySending(true);
    try {
      const fd = new FormData();
      fd.append('message', replyMessage.trim());
      if (replyFile) fd.append('attachment', replyFile);
      const res = await axios.post(`${API}/queries/${detail.id}/reply`, fd, {
        headers: { 'Content-Type': 'multipart/form-data' },
      });
      setDetail(res.data);
      setReplyMessage('');
      setReplyFile(null);
      toast.success('Reply sent');
      await loadQueries();
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Unable to send reply');
    } finally {
      setReplySending(false);
    }
  };

  const changeStatus = async (status) => {
    if (!detail?.id) return;
    try {
      const res = await axios.patch(`${API}/queries/${detail.id}/status`, { status });
      setDetail(res.data);
      toast.success(`Status updated to ${status}`);
      await loadQueries();
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Unable to update status');
    }
  };

  return (
    <div className="space-y-4" data-testid="query-page">
      <form onSubmit={submitQuery} className="rounded-xl border bg-white p-4 shadow-sm space-y-3">
        <h2 className="text-base font-semibold text-slate-800">Raise New Query</h2>
        <div className="grid gap-4 lg:grid-cols-[1fr_280px]">
          <div className="space-y-3">
            <div className="grid gap-3 sm:grid-cols-2">
              <div>
                <label className="text-xs font-medium text-slate-700">Query Type *</label>
                <select
                  className="mt-1 h-9 w-full rounded-md border px-2 text-sm"
                  value={form.query_type}
                  onChange={(e) => setForm((f) => ({ ...f, query_type: e.target.value }))}
                >
                  {QUERY_TYPES.map((t) => (
                    <option key={t} value={t}>{t}</option>
                  ))}
                </select>
              </div>
              <div>
                <label className="text-xs font-medium text-slate-700">Subject *</label>
                <input
                  className="mt-1 h-9 w-full rounded-md border px-2 text-sm"
                  maxLength={200}
                  value={form.subject}
                  onChange={(e) => setForm((f) => ({ ...f, subject: e.target.value }))}
                  placeholder="Brief subject"
                />
              </div>
            </div>

            {similar.length > 0 && (
              <div className="rounded-lg border border-gray-200 bg-gray-50 p-3 text-sm">
                <p className="font-medium text-gray-800 mb-2">Similar existing queries</p>
                <ul className="space-y-1">
                  {similar.map((row) => (
                    <li key={row.id}>
                      <button
                        type="button"
                        className="text-left text-emerald-800 hover:underline w-full"
                        onClick={() => openDetail(row.id)}
                      >
                        <span className="font-semibold">{row.query_no}</span>
                        {' — '}
                        {row.subject}
                        {' '}
                        <StatusBadge status={row.status} />
                      </button>
                    </li>
                  ))}
                </ul>
              </div>
            )}

            <div>
              <label className="text-xs font-medium text-slate-700">Description *</label>
              <Textarea
                className="mt-1 min-h-[88px] text-sm"
                value={form.description}
                onChange={(e) => setForm((f) => ({ ...f, description: e.target.value }))}
                placeholder="Describe the screen, steps, and issue"
              />
            </div>
          </div>

          <div className="space-y-3 rounded-lg border border-gray-200 bg-gray-50 p-3">
            <label className="text-xs font-medium text-slate-700">Attachment (optional)</label>
            <input
              type="file"
              accept={ALLOWED_EXT.join(',')}
              className="block w-full text-xs"
              onChange={(e) => setForm((f) => ({ ...f, file: e.target.files?.[0] || null }))}
            />
            <p className="text-xs text-slate-500">PNG, JPG, PDF, XLS, XLSX — max 5 MB</p>
            {form.file && (
              <div className="nmts-file-chip">
                <span className="truncate" title={form.file.name}>{form.file.name}</span>
                <Button type="button" size="sm" variant="outline" onClick={clearAttachment}>Clear File</Button>
              </div>
            )}
            <div className="flex flex-col gap-2 pt-1">
              <Button type="button" variant="outline" size="sm" onClick={resetForm} disabled={submitting}>
                <RotateCcw className="mr-2 h-4 w-4" />
                Reset
              </Button>
              <Button type="submit" size="sm" disabled={submitting} className="nmts-btn-primary">
                {submitting ? 'Submitting…' : 'Submit Query'}
              </Button>
            </div>
          </div>
        </div>
      </form>

      <div className="rounded-xl border bg-white overflow-hidden">
        <div className="flex flex-col gap-3 border-b p-4 lg:flex-row lg:items-center">
          <h2 className="text-lg font-semibold text-slate-800">All Queries</h2>
          <div className="flex flex-1 flex-wrap gap-2 lg:justify-end">
            <div className="relative">
              <Search className="absolute left-2 top-2.5 h-4 w-4 text-slate-400" />
              <input
                value={searchInput}
                onChange={(e) => setSearchInput(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && setSearch(searchInput.trim())}
                placeholder="Query No. or Subject"
                className="h-9 rounded border pl-8 pr-3 text-sm w-full sm:w-56"
              />
            </div>
            <select
              className="h-9 rounded border px-2 text-sm"
              value={filterType}
              onChange={(e) => setFilterType(e.target.value)}
            >
              <option value="">All Types</option>
              {QUERY_TYPES.map((t) => (
                <option key={t} value={t}>{t}</option>
              ))}
            </select>
            <select
              className="h-9 rounded border px-2 text-sm"
              value={filterStatus}
              onChange={(e) => setFilterStatus(e.target.value)}
            >
              <option value="">All Statuses</option>
              <option value="Open">Open</option>
              <option value="Answered">Answered</option>
              <option value="Closed">Closed</option>
            </select>
            <Button size="sm" variant="outline" onClick={() => setSearch(searchInput.trim())}>
              Search
            </Button>
            <Button size="sm" variant="outline" onClick={loadQueries} disabled={loading}>
              <RefreshCw className={`mr-2 h-4 w-4 ${loading ? 'animate-spin' : ''}`} />
              Refresh
            </Button>
          </div>
        </div>

        <div className="overflow-x-auto">
          <table className="min-w-full text-sm">
            <thead className="bg-emerald-700 text-white">
              <tr>
                {['Query No.', 'Type', 'Subject', 'Dealer', 'Branch', 'Raised By', 'Status', 'Raised On', 'Action'].map((h) => (
                  <th key={h} className="px-3 py-2 text-left font-semibold whitespace-nowrap">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {loading && (
                <tr>
                  <td colSpan={9} className="px-3 py-8 text-center text-slate-500">Loading…</td>
                </tr>
              )}
              {!loading && records.length === 0 && (
                <tr>
                  <td colSpan={9} className="px-3 py-8 text-center text-slate-500">No queries found.</td>
                </tr>
              )}
              {!loading && records.map((row) => (
                <tr key={row.id} className="border-t hover:bg-slate-50">
                  <td className="px-3 py-2 font-medium whitespace-nowrap">{row.query_no}</td>
                  <td className="px-3 py-2 whitespace-nowrap">{row.query_type}</td>
                  <td className="px-3 py-2 max-w-xs truncate" title={row.subject}>{row.subject}</td>
                  <td className="px-3 py-2 whitespace-nowrap">{row.scope?.dealer_name || '—'}</td>
                  <td className="px-3 py-2 whitespace-nowrap">{row.scope?.branch_name || '—'}</td>
                  <td className="px-3 py-2 whitespace-nowrap">{row.raised_by?.user_name || '—'}</td>
                  <td className="px-3 py-2"><StatusBadge status={row.status} /></td>
                  <td className="px-3 py-2 whitespace-nowrap">{formatIstDateTime(row.raised_at || row.created_at)}</td>
                  <td className="px-3 py-2">
                    <Button size="sm" variant="outline" onClick={() => openDetail(row.id)}>
                      <Eye className="h-4 w-4" />
                    </Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <div className="flex flex-wrap items-center justify-between gap-2 border-t p-4 text-sm">
          <span className="text-slate-600">{total} total</span>
          <div className="flex items-center gap-2">
            <Button size="sm" variant="outline" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>Previous</Button>
            <span>Page {page} of {totalPages}</span>
            <Button size="sm" variant="outline" disabled={page >= totalPages} onClick={() => setPage((p) => p + 1)}>Next</Button>
          </div>
        </div>
      </div>

      <NmtsModal
        open={detailOpen}
        onClose={() => setDetailOpen(false)}
        title="Query Details"
        maxWidth="max-w-2xl"
      >
          {detailLoading && <p className="text-slate-500">Loading…</p>}
          {!detailLoading && detail && (
            <div className="space-y-4 pb-2">
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-lg font-bold text-slate-900">{detail.query_no}</span>
                <StatusBadge status={detail.status} />
              </div>
              <div className="grid gap-2 text-sm sm:grid-cols-2">
                <div><span className="text-slate-500">Type</span><div className="font-medium">{detail.query_type}</div></div>
                <div><span className="text-slate-500">Raised On</span><div className="font-medium">{formatIstDateTime(detail.raised_at || detail.created_at)}</div></div>
                <div className="sm:col-span-2"><span className="text-slate-500">Subject</span><div className="font-medium">{detail.subject}</div></div>
                <div className="sm:col-span-2"><span className="text-slate-500">Description</span><div className="font-medium whitespace-pre-wrap">{detail.description}</div></div>
                <div><span className="text-slate-500">Raised By</span><div className="font-medium">{detail.raised_by?.user_name}</div></div>
                <div><span className="text-slate-500">Role</span><div className="font-medium">{detail.raised_by?.role}</div></div>
                <div><span className="text-slate-500">Brand</span><div className="font-medium">{detail.scope?.brand_name || '—'}</div></div>
                <div><span className="text-slate-500">Dealer</span><div className="font-medium">{detail.scope?.dealer_name || '—'}</div></div>
                <div><span className="text-slate-500">Branch</span><div className="font-medium">{detail.scope?.branch_name || '—'}</div></div>
                <div><span className="text-slate-500">Attachment</span><div><AttachmentLink attachment={detail.attachment} /></div></div>
              </div>

              {(detail.replies || []).length > 0 && (
                <div className="space-y-3">
                  <h3 className="font-semibold text-slate-800">Software Team Replies</h3>
                  {detail.replies.map((reply) => (
                    <div key={reply.reply_id} className="rounded-lg border bg-emerald-50/50 p-3 text-sm">
                      <div className="flex flex-wrap justify-between gap-2 text-xs text-slate-600">
                        <span>{reply.replied_by_name} ({reply.replied_by_role})</span>
                        <span>{formatIstDateTime(reply.replied_at)}</span>
                      </div>
                      <p className="mt-2 whitespace-pre-wrap text-slate-800">{reply.message}</p>
                      {reply.attachment && (
                        <div className="mt-2"><AttachmentLink attachment={reply.attachment} /></div>
                      )}
                    </div>
                  ))}
                </div>
              )}

              {detail.status === 'Closed' && detail.closed_at && (
                <p className="text-sm text-slate-600">
                  Closed on {formatIstDateTime(detail.closed_at)}
                  {detail.closed_by?.user_name ? ` by ${detail.closed_by.user_name}` : ''}
                </p>
              )}

              {isMaster ? (
                <div className="rounded-lg border p-4 space-y-3">
                  <h3 className="font-semibold text-slate-800">Reply</h3>
                  <Textarea
                    value={replyMessage}
                    onChange={(e) => setReplyMessage(e.target.value)}
                    placeholder="Type your reply to the user"
                    disabled={detail.status === 'Closed'}
                  />
                  <input
                    type="file"
                    accept={ALLOWED_EXT.join(',')}
                    className="block w-full text-sm"
                    disabled={detail.status === 'Closed'}
                    onChange={(e) => setReplyFile(e.target.files?.[0] || null)}
                  />
                  <div className="flex flex-wrap gap-2">
                    <Button onClick={sendReply} disabled={replySending || detail.status === 'Closed'}>
                      <Send className="mr-2 h-4 w-4" />
                      Send Reply
                    </Button>
                    {detail.status !== 'Closed' && (
                      <Button variant="outline" onClick={() => changeStatus('Closed')}>Close Query</Button>
                    )}
                    {detail.status === 'Closed' && (
                      <Button variant="outline" onClick={() => changeStatus('Open')}>Reopen Query</Button>
                    )}
                  </div>
                </div>
              ) : (
                <p className="rounded-lg border bg-slate-50 p-3 text-sm text-slate-600">
                  Only the Software Team can reply to this query.
                </p>
              )}
            </div>
          )}
      </NmtsModal>
    </div>
  );
}
