import React, { useEffect, useState } from 'react';
import axios from 'axios';
import { API, useAuth } from '@/App';
import { Button } from '@/components/ui/button';
import { HardDrive, RefreshCw, AlertTriangle, RotateCcw, ExternalLink, ShieldCheck, Trash2 } from 'lucide-react';
import { toast } from 'sonner';

const COLORS = {
  primary: '#0F766E',
  dark: '#134E4A',
  soft: '#F0FDFA',
  border: '#D1D5DB',
  muted: '#6B7280',
  warn: '#B45309',
  danger: '#B91C1C',
};

function fmtBytes(n) {
  const v = Number(n || 0);
  if (!v) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let i = 0;
  let x = v;
  while (x >= 1024 && i < units.length - 1) {
    x /= 1024;
    i += 1;
  }
  return `${x.toFixed(i === 0 ? 0 : 2)} ${units[i]}`;
}

function fmtMoney(n) {
  if (n === null || n === undefined || Number.isNaN(Number(n))) return 'Billing data unavailable';
  const v = Number(n || 0);
  return `$${v.toFixed(4)}`;
}

function ExternalServiceCard({ title, card, onOpen }) {
  return (
    <div className="rounded-xl border bg-white p-4 flex flex-col gap-3" style={{ borderColor: COLORS.border }}>
      <div className="text-sm font-semibold" style={{ color: COLORS.dark }}>{title}</div>
      <div className="grid gap-2 text-sm" style={{ color: COLORS.muted }}>
        <div>Status: <b style={{ color: COLORS.dark }}>{card?.status || '-'}</b></div>
        <div>Usage: <b style={{ color: COLORS.dark }}>{fmtBytes(card?.usage_bytes)}</b>
          <span className="ml-1 text-xs">({card?.usage_label || 'usage'})</span>
        </div>
        <div>Cost / Billing: <b style={{ color: COLORS.dark }}>
          {card?.billing_available ? fmtMoney(card?.estimated_cost) : (card?.billing_message || 'Billing data unavailable')}
        </b></div>
        {!card?.billing_available && card?.estimated_cost != null && (
          <div className="text-xs">NMTS estimate (not invoice): {fmtMoney(card.estimated_cost)}</div>
        )}
        <div className="text-xs">Last refreshed: {card?.last_refreshed || '-'}</div>
        <div className="text-xs">{card?.status_note}</div>
      </div>
      <Button
        onClick={onOpen}
        disabled={!card?.open_url}
        className="gap-2 mt-auto"
        style={{ backgroundColor: COLORS.primary, color: '#fff' }}
      >
        <ExternalLink className="h-4 w-4" />{card?.open_label || `Open ${title}`}
      </Button>
    </div>
  );
}

export function StorageCostMonitor() {
  const { user } = useAuth();
  const [data, setData] = useState(null);
  const [migration, setMigration] = useState(null);
  const [cleanupRows, setCleanupRows] = useState([]);
  const [loading, setLoading] = useState(false);
  const [month, setMonth] = useState(() => new Date().toISOString().slice(0, 7));
  const [brand, setBrand] = useState('');
  const [dealer, setDealer] = useState('');
  const [branch, setBranch] = useState('');
  const [verifyReport, setVerifyReport] = useState(null);
  const [dryRunReport, setDryRunReport] = useState(null);
  const [deleteTarget, setDeleteTarget] = useState(null);
  const [confirmText, setConfirmText] = useState('');
  const [busyId, setBusyId] = useState('');

  const load = async () => {
    setLoading(true);
    try {
      const params = new URLSearchParams();
      if (month) params.set('month', month);
      if (brand) params.set('brand', brand);
      if (dealer) params.set('dealer', dealer);
      const cleanupParams = new URLSearchParams({ years: '3' });
      if (brand) cleanupParams.set('brand', brand);
      if (dealer) cleanupParams.set('dealer', dealer);
      if (branch) cleanupParams.set('branch', branch);
      const [mon, mig, cleanup] = await Promise.all([
        axios.get(`${API}/storage/monitor?${params.toString()}`),
        axios.get(`${API}/storage/monitor/migration-report`),
        axios.get(`${API}/storage/archives/cleanup-table?${cleanupParams.toString()}`),
      ]);
      setData(mon.data);
      setMigration(mig.data);
      setCleanupRows(cleanup.data?.rows || []);
    } catch (err) {
      const status = err?.response?.status;
      toast.error(status === 403 ? 'Master Admin only' : 'Failed to load storage monitor');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (user?.role === 'master') load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [user?.role]);

  if (user?.role !== 'master') {
    return (
      <div className="rounded-xl border bg-white p-6 text-sm" style={{ borderColor: COLORS.border, color: COLORS.muted }}>
        Storage & Cost Monitor is available to Master Admin only.
      </div>
    );
  }

  const cards = data?.cards || {};
  const dealers = data?.dealer_ranking || [];
  const archives = data?.archives?.recent_jobs || [];
  const external = data?.external_services || {};
  const schedule = data?.archive_schedule || {};

  const retry = async (archiveId) => {
    try {
      await axios.post(`${API}/storage/archives/${archiveId}/retry`);
      toast.success('Retry started');
      load();
    } catch {
      toast.error('Retry failed');
    }
  };

  const dryRunMigration = async () => {
    try {
      const res = await axios.post(`${API}/storage/migration/archive-dates?dry_run=true`);
      toast.success(`Dry-run ready: ${res.data?.dates || 0} historical dates`);
      setMigration((m) => ({ ...(m || {}), dry_run: res.data }));
    } catch {
      toast.error('Migration dry-run failed');
    }
  };

  const openExternal = (url, label) => {
    if (!url) {
      toast.error(`${label} console URL is not configured`);
      return;
    }
    window.open(url, '_blank', 'noopener,noreferrer');
  };

  const runVerify = async (archiveId) => {
    setBusyId(archiveId);
    try {
      const res = await axios.post(`${API}/storage/archives/${archiveId}/verify`, null, {
        params: { brand: brand || undefined, dealer: dealer || undefined, branch: branch || undefined },
      });
      setVerifyReport(res.data);
      setDryRunReport(null);
      toast.success(res.data?.safe_to_delete ? 'SAFE TO DELETE' : 'NOT SAFE TO DELETE');
    } catch {
      toast.error('Verify failed');
    } finally {
      setBusyId('');
    }
  };

  const runDryRun = async (archiveId) => {
    setBusyId(archiveId);
    try {
      const res = await axios.post(`${API}/storage/archives/${archiveId}/dry-run-delete`, null, {
        params: { brand: brand || undefined, dealer: dealer || undefined, branch: branch || undefined },
      });
      setDryRunReport(res.data);
      setVerifyReport(res.data);
      toast.success('Dry run complete — zero records deleted');
    } catch {
      toast.error('Dry run failed');
    } finally {
      setBusyId('');
    }
  };

  const confirmDelete = async () => {
    if (!deleteTarget?.archive_id) return;
    if (confirmText !== 'DELETE') {
      toast.error('Type DELETE exactly to confirm');
      return;
    }
    setBusyId(deleteTarget.archive_id);
    try {
      const res = await axios.post(`${API}/storage/archives/${deleteTarget.archive_id}/delete-mongo`, {
        confirm_text: confirmText,
        brand: brand || null,
        dealer: dealer || null,
        branch: branch || null,
      });
      if (res.data?.status === 'blocked') {
        toast.error(res.data?.reason || 'Delete blocked');
      } else {
        toast.success(`Deleted ${res.data?.deleted || 0} Mongo rows`);
      }
      setDeleteTarget(null);
      setConfirmText('');
      load();
    } catch (err) {
      toast.error(err?.response?.data?.detail || 'Delete failed');
    } finally {
      setBusyId('');
    }
  };

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3 rounded-xl border bg-white p-4 shadow-sm" style={{ borderColor: COLORS.border }}>
        <HardDrive className="h-5 w-5" style={{ color: COLORS.primary }} />
        <div className="mr-auto">
          <div className="text-sm font-semibold" style={{ color: COLORS.dark }}>Storage & Cost Monitor</div>
          <div className="text-xs" style={{ color: COLORS.muted }}>Master Admin only · estimated costs, not final AWS invoice</div>
        </div>
        <label className="text-xs font-semibold" style={{ color: COLORS.muted }}>
          Month
          <input type="month" value={month} onChange={(e) => setMonth(e.target.value)} className="ml-2 rounded border px-2 py-1 text-sm" />
        </label>
        <input placeholder="Brand filter" value={brand} onChange={(e) => setBrand(e.target.value)} className="rounded border px-2 py-1 text-sm" />
        <input placeholder="Dealer filter" value={dealer} onChange={(e) => setDealer(e.target.value)} className="rounded border px-2 py-1 text-sm" />
        <input placeholder="Branch filter" value={branch} onChange={(e) => setBranch(e.target.value)} className="rounded border px-2 py-1 text-sm" />
        <Button onClick={load} disabled={loading} className="gap-2" style={{ backgroundColor: COLORS.primary, color: '#fff' }}>
          <RefreshCw className="h-4 w-4" />{loading ? 'Loading…' : 'Refresh'}
        </Button>
        <Button onClick={dryRunMigration} variant="outline" className="gap-2">
          <RotateCcw className="h-4 w-4" />Migration dry-run
        </Button>
      </div>

      {data?.warning && (
        <div className="flex items-start gap-2 rounded-xl border px-4 py-3 text-sm" style={{ borderColor: '#F59E0B', background: '#FFFBEB', color: COLORS.warn }}>
          <AlertTriangle className="h-4 w-4 mt-0.5 shrink-0" />
          <div>
            <div className="font-semibold">Storage Backend: {data.storage_backend || 'LOCAL FALLBACK'}</div>
            <div>{data.warning}</div>
          </div>
        </div>
      )}

      {/* Additive: identical AWS / MongoDB external service cards */}
      <div className="grid gap-3 md:grid-cols-2">
        <ExternalServiceCard
          title="AWS / S3"
          card={external.aws}
          onOpen={() => openExternal(external.aws?.open_url, 'AWS / S3')}
        />
        <ExternalServiceCard
          title="MongoDB"
          card={external.mongodb}
          onOpen={() => openExternal(external.mongodb?.open_url, 'MongoDB')}
        />
      </div>

      {schedule?.daily_product_history && (
        <div className="rounded-xl border bg-white px-4 py-3 text-xs" style={{ borderColor: COLORS.border, color: COLORS.muted }}>
          Archive schedule (unchanged): Daily product history {schedule.daily_product_history}; Monthly orders/requests {schedule.monthly_orders_requests}
        </div>
      )}

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        {[
          ['MongoDB Used Storage', fmtBytes(cards.mongodb_used_storage)],
          ['MongoDB Data Size', fmtBytes(cards.mongodb_data_size)],
          ['MongoDB Index Size', fmtBytes(cards.mongodb_index_size)],
          ['Today Product Rows', String(cards.today_product_count ?? '-')],
          ['S3 Archived Bytes', fmtBytes(cards.s3_total_stored)],
          ['This Month Upload', fmtBytes(data?.usage_month?.upload_bytes)],
          ['This Month View/Read', fmtBytes(data?.usage_month?.view_bytes)],
          ['This Month Download', fmtBytes(data?.usage_month?.download_bytes)],
          ['PUT Requests', String(data?.usage_month?.put_requests ?? 0)],
          ['GET Requests', String(data?.usage_month?.get_requests ?? 0)],
          ['Estimated Storage Cost', fmtMoney(data?.usage_month?.estimated_storage_cost)],
          ['Estimated Request Cost', fmtMoney(data?.usage_month?.estimated_request_cost)],
          ['Estimated Transfer Cost', fmtMoney(data?.usage_month?.estimated_transfer_cost)],
          ['Estimated Total Cost', fmtMoney(cards.estimated_current_month_cost)],
          ['Last Archive Status', String(cards.last_archive_status || '-')],
          ['Last Successful Archive', String(cards.last_successful_archive_date || '-')],
          ['Failed Archive Count', String(cards.failed_archive_count ?? 0)],
          ['Storage Backend', String(data?.storage_backend || '-')],
          ['Product Hot Days', String(data?.product_mongo_hot_days ?? 1)],
        ].map(([label, value]) => (
          <div key={label} className="rounded-xl border bg-white p-4" style={{ borderColor: COLORS.border, background: COLORS.soft }}>
            <div className="text-xs font-semibold uppercase tracking-wide" style={{ color: COLORS.muted }}>{label}</div>
            <div className="mt-2 text-xl font-semibold" style={{ color: COLORS.dark }}>{value}</div>
          </div>
        ))}
      </div>

      {/* PRESERVED: Dealer-wise / branch usage ranking */}
      <div className="rounded-xl border bg-white p-4" style={{ borderColor: COLORS.border }}>
        <div className="mb-3 text-sm font-semibold" style={{ color: COLORS.dark }}>Dealer-wise Estimated Cost Ranking</div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr style={{ backgroundColor: COLORS.primary, color: '#fff' }}>
                {['Dealer', 'Branches', 'Stored GB', 'Uploaded GB', 'Viewed GB', 'Downloaded GB', 'PUT', 'GET', 'Estimated Cost'].map((h) => (
                  <th key={h} className="p-2 text-left font-medium">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {dealers.length === 0 ? (
                <tr><td colSpan={9} className="p-4 text-center" style={{ color: COLORS.muted }}>No usage attributed yet for this month</td></tr>
              ) : dealers.map((d, i) => (
                <tr key={`${d.dealer}-${i}`} className="border-b" style={{ backgroundColor: i % 2 ? '#fff' : COLORS.soft }}>
                  <td className="p-2 font-medium">{d.dealer}</td>
                  <td className="p-2">{d.branches}</td>
                  <td className="p-2">{Number(d.stored_gb || 0).toFixed(4)}</td>
                  <td className="p-2">{Number(d.uploaded_gb || 0).toFixed(4)}</td>
                  <td className="p-2">{Number(d.viewed_gb || 0).toFixed(4)}</td>
                  <td className="p-2">{Number(d.downloaded_gb || 0).toFixed(4)}</td>
                  <td className="p-2">{d.put_requests || 0}</td>
                  <td className="p-2">{d.get_requests || 0}</td>
                  <td className="p-2 font-semibold">{fmtMoney(d.estimated_cost)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* PRESERVED: Recent Archive Jobs */}
      <div className="rounded-xl border bg-white p-4" style={{ borderColor: COLORS.border }}>
        <div className="mb-3 text-sm font-semibold" style={{ color: COLORS.dark }}>Recent Archive Jobs</div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr style={{ backgroundColor: COLORS.primary, color: '#fff' }}>
                {['Date', 'Module', 'Records', 'Size', 'Status', 'Started', 'Verified', 'Pruned', 'Dealers/Branches', 'Action'].map((h) => (
                  <th key={h} className="p-2 text-left font-medium">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {archives.length === 0 ? (
                <tr><td colSpan={10} className="p-4 text-center" style={{ color: COLORS.muted }}>No archive jobs yet</td></tr>
              ) : archives.map((a, i) => (
                <tr key={a.archive_id || i} className="border-b" style={{ backgroundColor: i % 2 ? '#fff' : COLORS.soft }}>
                  <td className="p-2">{a.date || '-'}</td>
                  <td className="p-2">{a.module || '-'}</td>
                  <td className="p-2">{a.records ?? '-'}</td>
                  <td className="p-2">{fmtBytes(a.archive_size)}</td>
                  <td className="p-2 font-semibold" style={{ color: a.status === 'FAILED' ? COLORS.danger : COLORS.dark }}>{a.status}</td>
                  <td className="p-2 text-xs">{a.started || '-'}</td>
                  <td className="p-2 text-xs">{a.verified || '-'}</td>
                  <td className="p-2 text-xs">{a.pruned || '-'}</td>
                  <td className="p-2">{a.dealer_count ?? 0}/{a.branch_count ?? 0}</td>
                  <td className="p-2">
                    {a.status === 'FAILED' ? (
                      <Button size="sm" variant="outline" onClick={() => retry(a.archive_id)}>Retry</Button>
                    ) : (
                      <span className="text-xs" style={{ color: COLORS.muted }}>—</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* Additive: Archive History / Data Cleanup table (3 years) */}
      <div className="rounded-xl border bg-white p-4" style={{ borderColor: COLORS.border }}>
        <div className="mb-3 text-sm font-semibold" style={{ color: COLORS.dark }}>Archive History / Data Cleanup (up to 3 years)</div>
        <div className="mb-2 text-xs" style={{ color: COLORS.muted }}>
          Manual Mongo delete only after View/Verify + Dry Run. S3 objects are never deleted from NMTS. ARCHIVE_PRUNE_ENABLED stays false.
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr style={{ backgroundColor: COLORS.primary, color: '#fff' }}>
                {['Archive Date', 'Brand', 'Dealer', 'Branch', 'Mongo Count', 'S3 Count', 'File Size', 'Status', 'Data Changed', 'Backend', 'Mongo Data', 'SHA256', 'Actions'].map((h) => (
                  <th key={h} className="p-2 text-left font-medium whitespace-nowrap">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {cleanupRows.length === 0 ? (
                <tr><td colSpan={13} className="p-4 text-center" style={{ color: COLORS.muted }}>No archives in selected window</td></tr>
              ) : cleanupRows.map((r, i) => (
                <tr key={r.archive_id || i} className="border-b" style={{ backgroundColor: i % 2 ? '#fff' : COLORS.soft }}>
                  <td className="p-2 whitespace-nowrap">{r.archive_date || '-'}</td>
                  <td className="p-2">{r.brand || (r.brands || []).join(', ') || (r.brand_codes || []).join(', ') || '-'}</td>
                  <td className="p-2">{r.dealer || (r.dealers || []).join(', ') || '-'}</td>
                  <td className="p-2">{r.branch || (r.branches || []).join(', ') || '-'}</td>
                  <td className="p-2">{r.mongo_source_count ?? '-'}</td>
                  <td className="p-2">{r.s3_record_count ?? '-'}</td>
                  <td className="p-2">{fmtBytes(r.file_size)}</td>
                  <td className="p-2 font-semibold">{r.archive_status}</td>
                  <td className="p-2 text-xs">{r.data_changed_status || '-'}</td>
                  <td className="p-2">{r.storage_backend || '-'}</td>
                  <td className="p-2">{r.mongo_data_status || '-'}</td>
                  <td className="p-2 text-xs">{r.sha256_status || ((r.sha256 || '').slice(0, 12) || '-')}</td>
                  <td className="p-2">
                    <div className="flex flex-wrap gap-1">
                      <Button size="sm" variant="outline" className="gap-1" disabled={busyId === r.archive_id} onClick={() => runVerify(r.archive_id)}>
                        <ShieldCheck className="h-3 w-3" />View / Verify
                      </Button>
                      <Button size="sm" variant="outline" onClick={() => openExternal(external.aws?.open_url, 'AWS / S3')}>Open AWS / S3</Button>
                      <Button size="sm" variant="outline" onClick={() => openExternal(external.mongodb?.open_url, 'MongoDB')}>Open MongoDB</Button>
                      <Button size="sm" variant="outline" disabled={busyId === r.archive_id} onClick={() => runDryRun(r.archive_id)}>Dry Run</Button>
                      <Button
                        size="sm"
                        className="gap-1"
                        style={{
                          backgroundColor: COLORS.danger,
                          color: '#fff',
                          opacity: (verifyReport?.archive_id === r.archive_id && verifyReport?.safe_to_delete && dryRunReport?.archive_id === r.archive_id && dryRunReport?.safe_to_delete) ? 1 : 0.4,
                          cursor: (verifyReport?.archive_id === r.archive_id && verifyReport?.safe_to_delete && dryRunReport?.archive_id === r.archive_id && dryRunReport?.safe_to_delete) ? 'pointer' : 'not-allowed',
                        }}
                        title="Requires View/Verify SAFE and Dry Run for this archive"
                        disabled={!(verifyReport?.archive_id === r.archive_id && verifyReport?.safe_to_delete && dryRunReport?.archive_id === r.archive_id && dryRunReport?.safe_to_delete)}
                        onClick={() => { setDeleteTarget(r); setConfirmText(''); }}
                      >
                        <Trash2 className="h-3 w-3" />Delete from MongoDB
                      </Button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {(verifyReport || dryRunReport) && (
        <div className="rounded-xl border bg-white p-4 text-sm" style={{ borderColor: COLORS.border }}>
          <div className="mb-2 font-semibold" style={{ color: COLORS.dark }}>
            {dryRunReport ? 'Dry Run Result' : 'View / Verify Result'} — {verifyReport?.safe_to_delete ? 'SAFE TO DELETE' : 'NOT SAFE TO DELETE'}
          </div>
          <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3" style={{ color: COLORS.muted }}>
            <div>Date: <b style={{ color: COLORS.dark }}>{verifyReport?.archive_date}</b></div>
            <div>Brand: <b style={{ color: COLORS.dark }}>{(verifyReport?.brands || []).join(', ') || verifyReport?.brand || '-'}</b></div>
            <div>Dealer: <b style={{ color: COLORS.dark }}>{(verifyReport?.dealers || []).join(', ') || verifyReport?.dealer || '-'}</b></div>
            <div>Branch: <b style={{ color: COLORS.dark }}>{(verifyReport?.branches || []).join(', ') || verifyReport?.branch || '-'}</b></div>
            <div>Mongo count: <b style={{ color: COLORS.dark }}>{verifyReport?.mongo_count}</b></div>
            <div>S3 count: <b style={{ color: COLORS.dark }}>{verifyReport?.s3_count}</b></div>
            <div>Manifest: <b style={{ color: COLORS.dark }}>{verifyReport?.manifest_status}</b></div>
            <div>SHA256: <b style={{ color: COLORS.dark }}>{verifyReport?.sha256_status}</b></div>
            <div>S3 readable: <b style={{ color: COLORS.dark }}>{String(verifyReport?.s3_readable)}</b></div>
            <div>Source: <b style={{ color: COLORS.dark }}>{verifyReport?.source_change_status}</b></div>
            <div>Backend: <b style={{ color: COLORS.dark }}>{verifyReport?.storage_backend}</b></div>
            <div>Archive ts: <b style={{ color: COLORS.dark }}>{verifyReport?.archive_timestamp || '-'}</b></div>
            <div className="sm:col-span-2 lg:col-span-3">S3 key: <b style={{ color: COLORS.dark }}>{verifyReport?.storage_key}</b></div>
            <div className="sm:col-span-2 lg:col-span-3">Reason: <b style={{ color: verifyReport?.safe_to_delete ? COLORS.dark : COLORS.danger }}>{verifyReport?.reason}</b></div>
            {dryRunReport && (
              <>
                <div>Matching Mongo: <b style={{ color: COLORS.dark }}>{dryRunReport.mongo_matching_count}</b></div>
                <div>S3 archived: <b style={{ color: COLORS.dark }}>{dryRunReport.s3_archived_count}</b></div>
                <div>Would delete: <b style={{ color: COLORS.dark }}>{dryRunReport.would_delete_count}</b> (dry run deleted {dryRunReport.deleted})</div>
              </>
            )}
          </div>
        </div>
      )}

      {deleteTarget && (
        <div className="rounded-xl border p-4 text-sm" style={{ borderColor: COLORS.danger, background: '#FEF2F2' }}>
          <div className="font-semibold mb-2" style={{ color: COLORS.danger }}>Confirm MongoDB Delete</div>
          <div className="grid gap-1 mb-3" style={{ color: COLORS.dark }}>
            <div>Date: {deleteTarget.archive_date}</div>
            <div>Brand: {deleteTarget.brand || (deleteTarget.brands || []).join(', ') || '-'}</div>
            <div>Dealer: {deleteTarget.dealer || (deleteTarget.dealers || []).join(', ') || '-'}</div>
            <div>Branch: {deleteTarget.branch || (deleteTarget.branches || []).join(', ') || '-'}</div>
            <div>Collection: products</div>
            <div>Exact record count: {verifyReport?.mongo_count}</div>
            <div>S3 key: {deleteTarget.storage_key}</div>
            <div className="text-xs" style={{ color: COLORS.muted }}>S3 objects are NOT deleted. Type DELETE to enable confirmation.</div>
          </div>
          <input
            value={confirmText}
            onChange={(e) => setConfirmText(e.target.value)}
            placeholder="Type DELETE"
            className="rounded border px-2 py-1 text-sm mr-2"
          />
          <Button
            disabled={confirmText !== 'DELETE' || busyId === deleteTarget.archive_id}
            onClick={confirmDelete}
            style={{ backgroundColor: COLORS.danger, color: '#fff' }}
          >
            Confirm MongoDB Delete
          </Button>
          <Button variant="outline" className="ml-2" onClick={() => { setDeleteTarget(null); setConfirmText(''); }}>Cancel</Button>
        </div>
      )}

      {migration && (
        <div className="rounded-xl border bg-white p-4 text-sm" style={{ borderColor: COLORS.border }}>
          <div className="mb-2 font-semibold" style={{ color: COLORS.dark }}>Initial MongoDB Space Recovery Report</div>
          <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3" style={{ color: COLORS.muted }}>
            <div>Historical dates: <b style={{ color: COLORS.dark }}>{migration.historical_date_count ?? 0}</b></div>
            <div>Archive candidate records: <b style={{ color: COLORS.dark }}>{migration.archive_candidate_records ?? 0}</b></div>
            <div>Verified archives: <b style={{ color: COLORS.dark }}>{migration.verified_archive_count ?? 0}</b></div>
            <div>Estimated recoverable: <b style={{ color: COLORS.dark }}>{fmtBytes(migration.estimated_recoverable_bytes)}</b></div>
            <div>Today product rows: <b style={{ color: COLORS.dark }}>{migration.mongo?.today_product_count ?? '-'}</b></div>
            <div>Prune gate: <b style={{ color: COLORS.dark }}>{migration.prune_blocked_reason || 'Ready when enabled'}</b></div>
          </div>
          {migration.warning && (
            <div className="mt-3 text-sm" style={{ color: COLORS.warn }}>{migration.warning}</div>
          )}
        </div>
      )}
    </div>
  );
}
