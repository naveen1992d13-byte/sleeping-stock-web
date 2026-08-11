import React, { useEffect, useState } from 'react';
import axios from 'axios';
import { API, useAuth } from '@/App';
import { Button } from '@/components/ui/button';
import { HardDrive, RefreshCw, AlertTriangle, RotateCcw } from 'lucide-react';
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
  const v = Number(n || 0);
  return `$${v.toFixed(4)}`;
}

export function StorageCostMonitor() {
  const { user } = useAuth();
  const [data, setData] = useState(null);
  const [migration, setMigration] = useState(null);
  const [loading, setLoading] = useState(false);
  const [month, setMonth] = useState(() => new Date().toISOString().slice(0, 7));
  const [brand, setBrand] = useState('');
  const [dealer, setDealer] = useState('');

  const load = async () => {
    setLoading(true);
    try {
      const params = new URLSearchParams();
      if (month) params.set('month', month);
      if (brand) params.set('brand', brand);
      if (dealer) params.set('dealer', dealer);
      const [mon, mig] = await Promise.all([
        axios.get(`${API}/storage/monitor?${params.toString()}`),
        axios.get(`${API}/storage/monitor/migration-report`),
      ]);
      setData(mon.data);
      setMigration(mig.data);
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
