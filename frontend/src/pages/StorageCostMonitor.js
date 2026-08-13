import React, { useEffect, useState } from 'react';
import axios from 'axios';
import { API, useAuth } from '@/App';
import { Button } from '@/components/ui/button';
import { HardDrive, RefreshCw, AlertTriangle, RotateCcw, ExternalLink, ShieldCheck, Trash2, Download } from 'lucide-react';
import { toast } from 'sonner';

const COLORS = {
  primary: '#0F766E',
  dark: '#134E4A',
  soft: '#F0FDFA',
  border: '#D1D5DB',
  muted: '#6B7280',
  warn: '#B45309',
  danger: '#B91C1C',
  green: '#15803D',
  orange: '#C2410C',
  grey: '#6B7280',
};

function fmtBytes(n) {
  if (n === null || n === undefined || n === 'Unavailable') return 'Unavailable';
  const v = Number(n);
  if (Number.isNaN(v)) return String(n);
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

function statusColor(display) {
  if (display === 'TRANSFERRED & VERIFIED' || display === 'PRUNED') return COLORS.green;
  if (display === 'NO ELIGIBLE DATA' || display === 'NO ELIGIBLE ORDERS') return COLORS.muted;
  if (display === 'VERIFICATION FAILED') return COLORS.orange;
  if (display === 'PENDING' || display === 'RUNNING') return COLORS.grey;
  return COLORS.danger;
}

function ExternalServiceCard({ title, card, onOpen, extra }) {
  return (
    <div className="rounded-xl border bg-white p-4 flex flex-col gap-3" style={{ borderColor: COLORS.border }}>
      <div className="text-sm font-semibold" style={{ color: COLORS.dark }}>{title}</div>
      <div className="grid gap-2 text-sm" style={{ color: COLORS.muted }}>
        <div>Status: <b style={{ color: COLORS.dark }}>{card?.status || '-'}</b></div>
        <div>Usage: <b style={{ color: COLORS.dark }}>{fmtBytes(card?.usage_bytes)}</b>
          <span className="ml-1 text-xs">({card?.usage_label || 'usage'})</span>
        </div>
        {extra}
        <div>Cost / Billing: <b style={{ color: COLORS.dark }}>
          {card?.billing_available ? fmtMoney(card?.estimated_cost) : (card?.billing_message || 'Billing data unavailable')}
        </b></div>
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
  const [expandedDealer, setExpandedDealer] = useState('');
  const [verifyReport, setVerifyReport] = useState(null);
  const [dryRunReport, setDryRunReport] = useState(null);
  const [deleteTarget, setDeleteTarget] = useState(null);
  const [confirmText, setConfirmText] = useState('');
  const [busyId, setBusyId] = useState('');

  const load = async () => {
    setLoading(true);
    try {
      // Intentionally ignore Brand/Dealer/Branch header filters — overall storage only.
      const [mon, mig, cleanup] = await Promise.all([
        axios.get(`${API}/storage/monitor`),
        axios.get(`${API}/storage/monitor/migration-report`),
        axios.get(`${API}/storage/archives/cleanup-table?years=3`),
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
        Storage & Data Cleanup is available to Master Admin only.
      </div>
    );
  }

  const cards = data?.cards || {};
  const dealers = data?.dealer_storage || [];
  const archives = data?.archives?.recent_jobs || cleanupRows;
  const health = data?.archive_health || {};
  const external = data?.external_services || {};
  const schedule = data?.archive_schedule || {};
  const totals = data?.storage_totals || {};
  const s3 = data?.s3 || {};

  const retry = async (archiveId) => {
    setBusyId(archiveId);
    try {
      const res = await axios.post(`${API}/storage/archives/${archiveId}/retry`);
      if (res.data?.status === 'ok' || res.data?.status === 'reconciled') {
        toast.success('Retry Archive — TRANSFERRED & VERIFIED');
      } else {
        toast.error(res.data?.reason || 'Retry Archive failed');
      }
      load();
    } catch (err) {
      toast.error(err?.response?.data?.detail || 'Retry Archive failed');
    } finally {
      setBusyId('');
    }
  };

  const downloadArchive = async (archiveId) => {
    try {
      const res = await axios.get(`${API}/storage/archives/${archiveId}/download`, {
        responseType: 'blob',
        maxRedirects: 5,
      });
      const blob = new Blob([res.data]);
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `archive-${archiveId}.jsonl.gz`;
      a.click();
      window.URL.revokeObjectURL(url);
      toast.success('Download started');
    } catch (err) {
      // Prefer opening API URL directly for S3 redirect
      const token = localStorage.getItem('token');
      window.open(`${API}/storage/archives/${archiveId}/download?token=${encodeURIComponent(token || '')}`, '_blank');
      toast.message(err?.response?.data?.detail || 'Opening archive download…');
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
      const res = await axios.post(`${API}/storage/archives/${archiveId}/verify`);
      setVerifyReport(res.data);
      setDryRunReport(null);
      toast.success(res.data?.safe_to_delete ? 'SAFE TO DELETE' : (res.data?.lock_reason || 'NOT SAFE TO DELETE'));
    } catch {
      toast.error('Verify failed');
    } finally {
      setBusyId('');
    }
  };

  const runDryRun = async (archiveId) => {
    setBusyId(archiveId);
    try {
      const res = await axios.post(`${API}/storage/archives/${archiveId}/dry-run-delete`);
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

  const canUnlockDelete = (r) =>
    verifyReport?.archive_id === r.archive_id
    && verifyReport?.safe_to_delete
    && dryRunReport?.archive_id === r.archive_id
    && dryRunReport?.safe_to_delete;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3 rounded-xl border bg-white p-4 shadow-sm" style={{ borderColor: COLORS.border }}>
        <HardDrive className="h-5 w-5" style={{ color: COLORS.primary }} />
        <div className="mr-auto">
          <div className="text-sm font-semibold" style={{ color: COLORS.dark }}>Storage & Data Cleanup</div>
          <div className="text-xs" style={{ color: COLORS.muted }}>
            Master Admin only · overall storage health (Brand/Dealer/Branch filters do not apply on this page)
          </div>
        </div>
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

      <div className="grid gap-3 md:grid-cols-2">
        <ExternalServiceCard
          title="AWS / S3"
          card={external.aws}
          onOpen={() => openExternal(external.aws?.open_url, 'AWS / S3')}
          extra={(
            <>
              <div>Actual S3 Used: <b style={{ color: COLORS.dark }}>{s3.actual_s3_available ? fmtBytes(s3.actual_s3_used_bytes) : 'Unavailable'}</b></div>
              <div>Dealer-attributed Verified Archive Usage: <b style={{ color: COLORS.dark }}>{fmtBytes(totals.s3_dealer_attributed_bytes ?? s3.manifest_recorded_bytes)}</b></div>
              <div>Manifest Recorded Size: <b style={{ color: COLORS.dark }}>{fmtBytes(s3.manifest_recorded_bytes)}</b></div>
              <div className="text-xs">Actual S3 and dealer-attributed archive usage are different metrics — not necessarily identical.</div>
            </>
          )}
        />
        <ExternalServiceCard
          title="MongoDB"
          card={external.mongodb}
          onOpen={() => openExternal(external.mongodb?.open_url, 'MongoDB')}
          extra={(
            <>
              <div>Allocated Mongo Usage: <b style={{ color: COLORS.dark }}>{fmtBytes(cards.mongodb_allocated_usage ?? totals.mongodb_allocated_usage_bytes)}</b></div>
              <div>Physical MongoDB Storage: <b style={{ color: COLORS.dark }}>{fmtBytes(cards.mongodb_physical_storage ?? cards.mongodb_used_storage)}</b></div>
              <div>Data Size: <b style={{ color: COLORS.dark }}>{fmtBytes(cards.mongodb_data_size)}</b></div>
              <div>Index Size: <b style={{ color: COLORS.dark }}>{fmtBytes(cards.mongodb_index_size)}</b></div>
              <div className="text-xs">{cards.mongodb_allocated_note || cards.mongodb_capacity_reason || 'Allocated ≠ physical dbStats by design.'}</div>
            </>
          )}
        />
      </div>

      {schedule?.daily_coordinated_batch && (
        <div className="rounded-xl border bg-white px-4 py-3 text-xs" style={{ borderColor: COLORS.border, color: COLORS.muted }}>
          Archive schedule: Daily coordinated batch {schedule.daily_coordinated_batch}; Monthly safety-net {schedule.monthly_orders_requests}
        </div>
      )}

      {/* Tonight's Archive — frozen date / run ledger */}
      {(() => {
        const tonight = data?.tonight_archive || {};
        const modules = tonight.modules || {};
        const maint = data?.maintenance || {};
        return (
          <div className="rounded-xl border bg-white p-4" style={{ borderColor: COLORS.border }} data-testid="tonight-archive-card">
            <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
              <div className="text-sm font-semibold" style={{ color: COLORS.dark }}>Tonight&apos;s Archive</div>
              <div className="text-xs" style={{ color: maint.maintenance_active ? COLORS.warn : COLORS.muted }}>
                {maint.maintenance_active ? 'Maintenance window active (23:00–04:00 IST)' : 'Outside maintenance window'}
              </div>
            </div>
            <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4 mb-3">
              {[
                ['Frozen Archive Date', tonight.archive_date || '-'],
                ['Run ID', tonight.run_id || '-'],
                ['Started Time', tonight.started_at ? String(tonight.started_at).slice(0, 19).replace('T', ' ') : '-'],
                ['Current Status', tonight.overall_status || '-'],
              ].map(([label, value]) => (
                <div key={label} className="rounded-lg border p-3" style={{ borderColor: COLORS.border, background: COLORS.soft }}>
                  <div className="text-xs uppercase tracking-wide" style={{ color: COLORS.muted }}>{label}</div>
                  <div className="mt-1 text-sm font-semibold break-all" style={{ color: COLORS.dark }}>{value}</div>
                </div>
              ))}
            </div>
            <div className="mb-2 text-xs font-semibold uppercase tracking-wide" style={{ color: COLORS.muted }}>Per-module result</div>
            <div className="overflow-x-auto rounded-lg border" style={{ borderColor: COLORS.border }}>
              <table className="w-full text-xs">
                <thead style={{ background: COLORS.soft }}>
                  <tr>
                    {['Module', 'Status', 'Retries', 'Error / note'].map((h) => (
                      <th key={h} className="p-2 text-left">{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {Object.keys(modules).length === 0 ? (
                    <tr><td colSpan={4} className="p-3" style={{ color: COLORS.muted }}>No run ledger yet for tonight.</td></tr>
                  ) : Object.entries(modules).map(([name, mod]) => (
                    <tr key={name} className="border-t" style={{ borderColor: COLORS.border }}>
                      <td className="p-2 font-medium">{name}</td>
                      <td className="p-2" style={{ color: statusColor(String(mod?.status || '').toUpperCase() === 'VERIFIED' ? 'TRANSFERRED & VERIFIED' : String(mod?.status || '')) }}>
                        {mod?.status || '-'}
                      </td>
                      <td className="p-2">{mod?.retries ?? 0}</td>
                      <td className="p-2" style={{ color: COLORS.danger }}>{mod?.error || (mod?.result?.status ? String(mod.result.status) : '-')}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="mt-3 grid gap-2 text-sm" style={{ color: COLORS.muted }}>
              <div>Verification result: <b style={{ color: COLORS.dark }}>{tonight.overall_status || '-'}</b>
                {tonight.completed_at ? ` · completed ${String(tonight.completed_at).slice(0, 19).replace('T', ' ')}` : ''}
              </div>
              <div>Retry count: <b style={{ color: COLORS.dark }}>{tonight.retry_count ?? 0}</b></div>
              {tonight.last_error && (
                <div className="rounded-lg border px-3 py-2 text-xs" style={{ borderColor: '#FECACA', background: '#FEF2F2', color: COLORS.danger }}>
                  Failure / retry info: {tonight.last_error}
                </div>
              )}
            </div>
          </div>
        );
      })()}

      {/* Archive Health */}
      <div className="rounded-xl border bg-white p-4" style={{ borderColor: COLORS.border }}>
        <div className="mb-3 text-sm font-semibold" style={{ color: COLORS.dark }}>Archive Health</div>
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          {[
            ['Total archive datasets', health.total_archive_datasets ?? 0],
            ['Transferred & Verified', health.transferred_and_verified ?? 0],
            ['Pending', health.pending ?? 0],
            ['Failed', health.failed ?? 0],
            ['Verification failed', health.verification_failed ?? 0],
            ['Safe to Delete', health.safe_to_delete ?? 0],
            ['Overall verified %', `${health.overall_verified_percent ?? 0}%`],
          ].map(([label, value]) => (
            <div key={label} className="rounded-lg border p-3" style={{ borderColor: COLORS.border, background: COLORS.soft }}>
              <div className="text-xs uppercase tracking-wide" style={{ color: COLORS.muted }}>{label}</div>
              <div className="mt-1 text-lg font-semibold" style={{ color: COLORS.dark }}>{value}</div>
            </div>
          ))}
        </div>
      </div>

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        {[
          ['Allocated Mongo Usage', fmtBytes(cards.mongodb_allocated_usage ?? totals.mongodb_allocated_usage_bytes)],
          ['Physical MongoDB Storage', fmtBytes(cards.mongodb_physical_storage ?? cards.mongodb_used_storage)],
          ['MongoDB Data Size', fmtBytes(cards.mongodb_data_size)],
          ['MongoDB Index Size', fmtBytes(cards.mongodb_index_size)],
          ['MongoDB Plan/Capacity', 'Unavailable'],
          ['MongoDB Available/Balance', 'Unavailable'],
          ['Actual S3 Used Storage', s3.actual_s3_available ? fmtBytes(s3.actual_s3_used_bytes) : 'Unavailable'],
          ['Dealer-attributed Verified Archive Usage', fmtBytes(totals.s3_dealer_attributed_bytes ?? cards.s3_dealer_attributed)],
          ['Manifest Recorded Size', fmtBytes(s3.manifest_recorded_bytes ?? cards.s3_manifest_recorded)],
          ['Today Product Rows', String(cards.today_product_count ?? '-')],
          ['Last Archive Status', String(cards.last_archive_status || '-')],
          ['Last Successful Archive', String(cards.last_successful_archive_date || '-')],
          ['Failed Archive Count', String(cards.failed_archive_count ?? 0)],
          ['Storage Backend', String(data?.storage_backend || '-')],
        ].map(([label, value]) => (
          <div key={label} className="rounded-xl border bg-white p-4" style={{ borderColor: COLORS.border, background: COLORS.soft }}>
            <div className="text-xs font-semibold uppercase tracking-wide" style={{ color: COLORS.muted }}>{label}</div>
            <div className="mt-2 text-xl font-semibold" style={{ color: COLORS.dark }}>{value}</div>
          </div>
        ))}
      </div>

      {/* Dealer-wise Storage Usage — compulsory; uses same canonical snapshot as top totals */}
      <div className="rounded-xl border bg-white p-4" style={{ borderColor: COLORS.border }}>
        <div className="mb-1 text-sm font-semibold" style={{ color: COLORS.dark }}>Dealer-wise Storage Usage</div>
        <div className="mb-3 text-xs" style={{ color: COLORS.muted }}>
          {data?.mongodb_allocation_note || 'Allocated Mongo Usage matches this dealer table exactly (logical allocation). Physical MongoDB Storage is separate dbStats.'}
          {totals?.reconciliation?.s3_note ? ` ${totals.reconciliation.s3_note}` : ''}
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr style={{ backgroundColor: COLORS.primary, color: '#fff' }}>
                {['Dealer', 'Branches', 'Allocated Mongo', 'Verified Archive S3', 'Combined', 'Archive Verified %', ''].map((h) => (
                  <th key={h || 'x'} className="p-2 text-left font-medium">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {dealers.length === 0 ? (
                <tr><td colSpan={7} className="p-4 text-center" style={{ color: COLORS.muted }}>No dealer usage yet</td></tr>
              ) : dealers.map((d, i) => (
                <React.Fragment key={`${d.dealer}-${i}`}>
                  <tr className="border-b" style={{ backgroundColor: i % 2 ? '#fff' : COLORS.soft }}>
                    <td className="p-2 font-medium">{d.dealer}</td>
                    <td className="p-2">{d.branches}</td>
                    <td className="p-2">{fmtBytes(d.mongodb_used_bytes)}</td>
                    <td className="p-2">{fmtBytes(d.s3_archive_used_bytes)}</td>
                    <td className="p-2 font-semibold">{fmtBytes(d.combined_used_bytes)}</td>
                    <td className="p-2">{Number(d.archive_verified_percent || 0).toFixed(2)}%</td>
                    <td className="p-2">
                      <Button size="sm" variant="outline" onClick={() => setExpandedDealer(expandedDealer === d.dealer ? '' : d.dealer)}>
                        {expandedDealer === d.dealer ? 'Hide' : 'Branches'}
                      </Button>
                    </td>
                  </tr>
                  {expandedDealer === d.dealer && (
                    <tr>
                      <td colSpan={7} className="p-3 text-xs" style={{ color: COLORS.muted, background: '#fff' }}>
                        Branches: {(d.branch_names || []).join(', ') || '—'}
                      </td>
                    </tr>
                  )}
                </React.Fragment>
              ))}
            </tbody>
            <tfoot>
              <tr style={{ background: COLORS.soft }}>
                <td className="p-2 font-semibold" colSpan={2}>Totals (canonical snapshot)</td>
                <td className="p-2 font-semibold">{fmtBytes(totals.mongodb_dealer_allocated_bytes ?? totals.mongodb_allocated_usage_bytes)}</td>
                <td className="p-2 font-semibold">{fmtBytes(totals.s3_dealer_attributed_bytes)}</td>
                <td className="p-2 font-semibold" colSpan={3}>
                  Allocated Mongo: {fmtBytes(totals.mongodb_allocated_usage_bytes ?? totals.mongodb_dealer_allocated_bytes)}
                  {' · '}Physical Mongo: {fmtBytes(totals.mongodb_physical_storage_bytes ?? totals.mongodb_used_bytes)}
                  {' · '}Actual S3: {fmtBytes(totals.s3_actual_used_bytes ?? totals.s3_top_card_bytes)}
                </td>
              </tr>
            </tfoot>
          </table>
        </div>
      </div>

      {/* Archive Transfer Status */}
      <div className="rounded-xl border bg-white p-4" style={{ borderColor: COLORS.border }}>
        <div className="mb-3 text-sm font-semibold" style={{ color: COLORS.dark }}>Archive Transfer Status</div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr style={{ backgroundColor: COLORS.primary, color: '#fff' }}>
                {[
                  'Dataset', 'Archive date', 'Source count', 'Archived count', 'File size',
                  'S3 object', 'Readable', 'SHA256', 'Transferred', 'Verified', 'S3 path',
                  'Status', 'Failure reason', 'Actions',
                ].map((h) => (
                  <th key={h} className="p-2 text-left font-medium whitespace-nowrap">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {(cleanupRows.length ? cleanupRows : archives).length === 0 ? (
                <tr><td colSpan={14} className="p-4 text-center" style={{ color: COLORS.muted }}>No archive jobs yet</td></tr>
              ) : (cleanupRows.length ? cleanupRows : archives).map((a, i) => {
                const display = a.display_status || a.status;
                const locked = a.delete_locked !== false && !canUnlockDelete(a);
                return (
                  <tr key={a.archive_id || i} className="border-b" style={{ backgroundColor: i % 2 ? '#fff' : COLORS.soft }}>
                    <td className="p-2">{a.dataset || a.module || '-'}</td>
                    <td className="p-2 whitespace-nowrap">{a.archive_date || a.date || '-'}</td>
                    <td className="p-2">{a.source_record_count ?? a.mongo_source_count ?? a.records ?? '-'}</td>
                    <td className="p-2">{a.archived_record_count ?? a.s3_record_count ?? a.records ?? '-'}</td>
                    <td className="p-2">{fmtBytes(a.file_size ?? a.archive_size)}</td>
                    <td className="p-2">{a.s3_object_status || '-'}</td>
                    <td className="p-2">{a.s3_readable == null ? '-' : (a.s3_readable ? 'Yes' : 'No')}</td>
                    <td className="p-2">{a.sha256_match || a.sha256_status || '-'}</td>
                    <td className="p-2 text-xs">{a.transferred_at || a.verified || a.verified_at || '-'}</td>
                    <td className="p-2 text-xs">{a.verified_at || a.verified || '-'}</td>
                    <td className="p-2 text-xs max-w-[180px] truncate" title={a.storage_key || ''}>{a.storage_key || '-'}</td>
                    <td className="p-2 font-semibold whitespace-nowrap" style={{ color: statusColor(display) }}>{display}</td>
                    <td className="p-2 text-xs" style={{ color: COLORS.danger }}>{a.failure_reason || a.error || '—'}</td>
                    <td className="p-2">
                      <div className="flex flex-wrap gap-1">
                        {(a.retryable || display === 'NOT TRANSFERRED' || display === 'VERIFICATION FAILED' || a.status === 'FAILED') && (
                          <Button size="sm" variant="outline" disabled={busyId === a.archive_id} onClick={() => retry(a.archive_id)}>
                            Retry Archive
                          </Button>
                        )}
                        <Button size="sm" variant="outline" className="gap-1" onClick={() => downloadArchive(a.archive_id)}>
                          <Download className="h-3 w-3" />Download Archive
                        </Button>
                        {a.module === 'product-history' || a.dataset === 'product-history' ? (
                          <>
                            <Button size="sm" variant="outline" className="gap-1" disabled={busyId === a.archive_id} onClick={() => runVerify(a.archive_id)}>
                              <ShieldCheck className="h-3 w-3" />View / Verify
                            </Button>
                            <Button size="sm" variant="outline" disabled={busyId === a.archive_id} onClick={() => runDryRun(a.archive_id)}>Dry Run</Button>
                            <Button
                              size="sm"
                              className="gap-1"
                              style={{
                                backgroundColor: COLORS.danger,
                                color: '#fff',
                                opacity: canUnlockDelete(a) ? 1 : 0.45,
                                cursor: canUnlockDelete(a) ? 'pointer' : 'not-allowed',
                              }}
                              title={canUnlockDelete(a) ? 'Delete archived Mongo rows' : (a.lock_reason || 'Locked — not fully verified')}
                              disabled={!canUnlockDelete(a)}
                              onClick={() => { setDeleteTarget(a); setConfirmText(''); }}
                            >
                              <Trash2 className="h-3 w-3" />Delete from MongoDB
                            </Button>
                          </>
                        ) : null}
                        {locked && (a.module === 'product-history' || a.dataset === 'product-history') && !canUnlockDelete(a) && (
                          <div className="text-xs w-full" style={{ color: COLORS.warn }}>{a.lock_reason || 'Locked — not verified'}</div>
                        )}
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>

      {(verifyReport || dryRunReport) && (
        <div className="rounded-xl border bg-white p-4 text-sm" style={{ borderColor: COLORS.border }}>
          <div className="mb-2 font-semibold" style={{ color: COLORS.dark }}>
            {dryRunReport ? 'Dry Run Result' : 'View / Verify Result'} — {verifyReport?.safe_to_delete ? 'SAFE TO DELETE' : (verifyReport?.lock_reason || 'NOT SAFE TO DELETE')}
          </div>
          <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3" style={{ color: COLORS.muted }}>
            <div>Date: <b style={{ color: COLORS.dark }}>{verifyReport?.archive_date}</b></div>
            <div>Display: <b style={{ color: statusColor(verifyReport?.display_status) }}>{verifyReport?.display_status}</b></div>
            <div>Mongo count: <b style={{ color: COLORS.dark }}>{verifyReport?.mongo_count}</b></div>
            <div>S3 count: <b style={{ color: COLORS.dark }}>{verifyReport?.s3_count}</b></div>
            <div>SHA256: <b style={{ color: COLORS.dark }}>{verifyReport?.sha256_status}</b></div>
            <div>S3 readable: <b style={{ color: COLORS.dark }}>{String(verifyReport?.s3_readable)}</b></div>
            <div className="sm:col-span-2 lg:col-span-3">S3 key: <b style={{ color: COLORS.dark }}>{verifyReport?.storage_key}</b></div>
            <div className="sm:col-span-2 lg:col-span-3">Reason: <b style={{ color: verifyReport?.safe_to_delete ? COLORS.dark : COLORS.danger }}>{verifyReport?.reason}</b></div>
          </div>
        </div>
      )}

      {deleteTarget && (
        <div className="rounded-xl border p-4 text-sm" style={{ borderColor: COLORS.danger, background: '#FEF2F2' }}>
          <div className="font-semibold mb-2" style={{ color: COLORS.danger }}>Confirm MongoDB Delete</div>
          <div className="grid gap-1 mb-3" style={{ color: COLORS.dark }}>
            <div>Date: {deleteTarget.archive_date}</div>
            <div>Collection: products (exact archived historical rows only)</div>
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
            <div>Prune gate: <b style={{ color: COLORS.dark }}>{migration.prune_blocked_reason || 'Ready when enabled'}</b></div>
          </div>
        </div>
      )}
    </div>
  );
}
