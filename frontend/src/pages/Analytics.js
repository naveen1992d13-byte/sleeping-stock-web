import React, { useCallback, useEffect, useMemo, useState } from 'react';
import axios from 'axios';
import { useOutletContext } from 'react-router-dom';
import { API, useAuth } from '../App';
import { toast } from 'sonner';
import {
  Globe,
  TrendingUp,
  TrendingDown,
  Minus,
  X,
  Loader2,
} from 'lucide-react';
import {
  ResponsiveContainer,
  LineChart,
  Line,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  AreaChart,
  Area,
  ComposedChart,
} from 'recharts';

const today = new Date();
const isoLocal = (d) => {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${y}-${m}-${day}`;
};
const defaultFrom = isoLocal(new Date(today.getFullYear(), today.getMonth(), 1));
const defaultTo = isoLocal(today);

export const formatINR = (n) =>
  new Intl.NumberFormat('en-IN', { maximumFractionDigits: 2 }).format(Number(n || 0));

export const formatINRCompact = (n) => {
  const v = Number(n || 0);
  const abs = Math.abs(v);
  const sign = v < 0 ? '-' : '';
  if (abs >= 1e7) return `${sign}₹${(abs / 1e7).toFixed(2)} Crore`;
  if (abs >= 1e5) return `${sign}₹${(abs / 1e5).toFixed(2)} Lakh`;
  return `${sign}₹${formatINR(abs)}`;
};

const AGING_BUCKETS = ['0–30 Days', '31–90 Days', '91–180 Days', '181–360 Days', 'Above 360 Days'];

const displayDate = (iso) => {
  if (!iso) return '—';
  const [y, m, d] = String(iso).slice(0, 10).split('-');
  if (d && m && y) return `${d}-${m}-${y}`;
  return iso;
};

const isNullMetric = (v) => v === null || v === undefined;

function comparisonCaption(type) {
  if (type === 'PREVIOUS_DAY') return 'Change vs Previous Day';
  if (type === 'LAST_AVAILABLE_UPLOAD') return 'Change Since Last Upload';
  return 'No Previous Upload';
}

function StockTrendTooltip({ active, payload, metricType }) {
  if (!active || !payload?.length) return null;
  const row = payload[0]?.payload;
  if (!row) return null;
  if (row.data_status === 'NO_UPLOAD') {
    return (
      <div className="rounded-lg border bg-white p-3 text-sm shadow-lg max-w-xs">
        <p className="font-semibold">Status: No Stock Upload</p>
        <p className="text-gray-600 mt-1">
          No stock upload was published for this scope on {displayDate(row.date)}.
        </p>
      </div>
    );
  }
  const money = (v) =>
    isNullMetric(v) ? 'N/A' : metricType === 'value' ? formatINRCompact(v) : formatINR(v);
  return (
    <div className="rounded-lg border bg-white p-3 text-sm shadow-lg max-w-xs">
      <p className="font-semibold">{displayDate(row.date)}</p>
      {row.data_status === 'PARTIAL_UPLOAD' && (
        <p className="text-amber-700 text-xs mt-1">
          Partial Data: {row.uploaded_branch_count} of {row.expected_branch_count} branches uploaded
        </p>
      )}
      <p>Stock: {money(row.closing ?? row.stock_value)}</p>
      {row.comparison_date && <p>Compared with: {displayDate(row.comparison_date)}</p>}
      <p className="text-xs text-gray-500">{comparisonCaption(row.comparison_type)}</p>
      <p>Added: {money(row.added ?? row.added_value)}</p>
      <p>Reduced: {money(row.reduced ?? row.reduced_value)}</p>
      <p>Net Change: {money(row.net_change)}</p>
      <p>Change %: {isNullMetric(row.change_pct) ? 'N/A' : `${formatINR(row.change_pct)}%`}</p>
    </div>
  );
}

function scopeQuery(scopeBrand, scopeDealer, scopeBranch) {
  const q = {};
  if (scopeBrand && !String(scopeBrand).startsWith('All')) q.brand = scopeBrand;
  if (scopeDealer && !String(scopeDealer).startsWith('All')) q.dealer = scopeDealer;
  if (scopeBranch && !String(scopeBranch).startsWith('All')) q.branch = scopeBranch;
  return q;
}

function TrendBadge({ current, previous }) {
  const c = Number(current || 0);
  const p = Number(previous || 0);
  if (c > p) {
    return (
      <span className="inline-flex items-center text-emerald-600 text-xs font-semibold">
        <TrendingUp className="h-3 w-3 mr-1" /> Up
      </span>
    );
  }
  if (c < p) {
    return (
      <span className="inline-flex items-center text-red-600 text-xs font-semibold">
        <TrendingDown className="h-3 w-3 mr-1" /> Down
      </span>
    );
  }
  return (
    <span className="inline-flex items-center text-gray-500 text-xs font-semibold">
      <Minus className="h-3 w-3 mr-1" /> Flat
    </span>
  );
}

function MetricCard({ label, value, compareValue, onClick, hint }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`rounded-2xl border bg-white p-4 text-left shadow-sm transition ${onClick ? 'hover:border-emerald-400 hover:shadow-md cursor-pointer' : ''}`}
    >
      <p className="text-xs font-medium text-gray-500">{label}</p>
      <p className="mt-2 text-xl font-bold text-gray-900">{value}</p>
      {compareValue !== undefined && (
        <div className="mt-2 flex items-center justify-between">
          <TrendBadge current={value} previous={compareValue} />
          {hint && <span className="text-[10px] text-gray-400">{hint}</span>}
        </div>
      )}
    </button>
  );
}

function Section({ title, children }) {
  return (
    <section className="space-y-4">
      <h2 className="text-lg font-bold text-gray-900 border-b pb-2">{title}</h2>
      {children}
    </section>
  );
}

function ChartPanel({ title, children, empty }) {
  if (empty) {
    return (
      <div className="rounded-2xl border bg-white p-8 text-center text-gray-500">
        <p className="font-semibold">{title}</p>
        <p className="mt-2 text-sm">No data for the selected filters.</p>
      </div>
    );
  }
  return (
    <div className="rounded-2xl border bg-white p-5 shadow-sm">
      <h3 className="mb-4 font-bold text-gray-900">{title}</h3>
      {children}
    </div>
  );
}

function DrilldownModal({ open, title, onClose, loading, rows, columns, page, total, pageSize, onPage, headerNote }) {
  if (!open) return null;
  const pages = Math.max(1, Math.ceil((total || 0) / pageSize));
  return (
    <div className="fixed inset-0 z-50 flex items-end sm:items-center justify-center bg-black/40 p-2">
      <div className="bg-white rounded-2xl shadow-2xl w-full max-w-5xl max-h-[85vh] flex flex-col">
        <div className="flex items-center justify-between p-4 border-b">
          <h3 className="text-lg font-bold">{title}</h3>
          <button type="button" onClick={onClose} className="p-2 rounded-lg hover:bg-gray-100">
            <X />
          </button>
        </div>
        {headerNote && <div className="px-4 py-3 text-sm text-amber-900 bg-amber-50 border-b">{headerNote}</div>}
        <div className="overflow-auto flex-1 p-4">
          {loading ? (
            <div className="flex justify-center py-12 text-gray-500">
              <Loader2 className="animate-spin h-8 w-8" />
            </div>
          ) : (
            <table className="min-w-full text-sm">
              <thead className="bg-gray-50 sticky top-0">
                <tr>
                  {columns.map((c) => (
                    <th key={c.key} className="px-3 py-2 text-left font-semibold text-gray-600">
                      {c.label}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {(rows || []).map((r, i) => (
                  <tr key={i} className="border-t">
                    {columns.map((c) => (
                      <td key={c.key} className="px-3 py-2 whitespace-nowrap">
                        {c.render ? c.render(r) : r[c.key]}
                      </td>
                    ))}
                  </tr>
                ))}
                {!rows?.length && (
                  <tr>
                    <td colSpan={columns.length} className="p-8 text-center text-gray-500">
                      No records
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          )}
        </div>
        <div className="p-4 border-t flex items-center justify-between text-sm">
          <span>
            Page {page} / {pages} ({total} rows)
          </span>
          <div className="flex gap-2">
            <button
              type="button"
              disabled={page <= 1}
              onClick={() => onPage(page - 1)}
              className="px-3 py-1 rounded-lg border disabled:opacity-40"
            >
              Prev
            </button>
            <button
              type="button"
              disabled={page >= pages}
              onClick={() => onPage(page + 1)}
              className="px-3 py-1 rounded-lg border disabled:opacity-40"
            >
              Next
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

export function Analytics() {
  const { user } = useAuth();
  const { scopeBrand, scopeDealer, scopeBranch } = useOutletContext() || {};
  const [fromDate, setFromDate] = useState(defaultFrom);
  const [toDate, setToDate] = useState(defaultTo);
  const [category, setCategory] = useState('All Categories');
  const [agingType, setAgingType] = useState('purchase');
  const [metricType, setMetricType] = useState('value');
  const [requestDirection, setRequestDirection] = useState('raised');
  const [categories, setCategories] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [overall, setOverall] = useState(null);
  const [stockTrend, setStockTrend] = useState(null);
  const [categoryTrend, setCategoryTrend] = useState(null);
  const [agingTrend, setAgingTrend] = useState(null);
  const [orderSaving, setOrderSaving] = useState(null);
  const [requestAcc, setRequestAcc] = useState(null);
  const [stockMovement, setStockMovement] = useState(null);
  const [seriesVisible, setSeriesVisible] = useState({
    closing_stock_value: true,
    order_value: true,
    accepted_request_value: true,
  });
  const [drill, setDrill] = useState(null);
  const [drillRows, setDrillRows] = useState([]);
  const [drillTotal, setDrillTotal] = useState(0);
  const [drillPage, setDrillPage] = useState(1);
  const [drillLoading, setDrillLoading] = useState(false);
  const [drillMeta, setDrillMeta] = useState(null);

  const baseParams = useMemo(
    () => ({
      from_date: fromDate,
      to_date: toDate,
      aging_type: agingType,
      metric_type: metricType,
      category: category !== 'All Categories' ? category : undefined,
      ...scopeQuery(scopeBrand, scopeDealer, scopeBranch),
    }),
    [fromDate, toDate, agingType, metricType, category, scopeBrand, scopeDealer, scopeBranch]
  );

  const money = (n) => (isNullMetric(n) ? 'N/A' : metricType === 'value' ? formatINRCompact(n) : formatINR(n));
  const stockMoney = (n) => (isNullMetric(n) ? 'N/A' : metricType === 'value' ? formatINRCompact(n) : formatINR(n));
  const chartNum = (n) => (isNullMetric(n) ? null : Number(n));

  const loadAll = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const reqParams = { ...baseParams, request_direction: requestDirection };
      const [catRes, o, st, ct, at, os, ra, sm] = await Promise.all([
        axios.get(`${API}/analytics/categories`, { params: baseParams }),
        axios.get(`${API}/analytics/overall`, { params: baseParams }),
        axios.get(`${API}/analytics/stock-trend`, { params: baseParams }),
        axios.get(`${API}/analytics/category-trend`, { params: baseParams }),
        axios.get(`${API}/analytics/aging-trend`, { params: baseParams }),
        axios.get(`${API}/analytics/order-saving`, { params: baseParams }),
        axios.get(`${API}/analytics/request-acceptance`, { params: reqParams }),
        axios.get(`${API}/analytics/stock-movement`, { params: baseParams }),
      ]);
      setCategories(catRes.data?.categories || []);
      setOverall(o.data);
      setStockTrend(st.data);
      setCategoryTrend(ct.data);
      setAgingTrend(at.data);
      setOrderSaving(os.data);
      setRequestAcc(ra.data);
      setStockMovement(sm.data);
    } catch (e) {
      const msg = e.response?.data?.detail || 'Failed to load analytics';
      setError(msg);
      toast.error(msg);
    } finally {
      setLoading(false);
    }
  }, [baseParams, requestDirection]);

  useEffect(() => {
    loadAll();
  }, [loadAll]);

  const openDrill = async (type, extra = {}) => {
    setDrill({ type, ...extra });
    setDrillPage(1);
    await fetchDrill(type, 1, extra);
  };

  const fetchDrill = async (type, page, extra = {}) => {
    setDrillLoading(true);
    try {
      const params = {
        ...baseParams,
        drilldown_type: type,
        page,
        page_size: 50,
        request_direction: requestDirection,
        focus_date: extra.focusDate,
      };
      const res = await axios.get(`${API}/analytics/drilldown`, { params });
      setDrillRows(res.data?.records || []);
      setDrillTotal(res.data?.total || 0);
      setDrillMeta(res.data);
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Drill-down failed');
    } finally {
      setDrillLoading(false);
    }
  };

  const overallSeries = (overall?.series || []).map((d) => ({
    ...d,
    dateLabel: displayDate(d.date),
    closing_stock_value: chartNum(d.closing_stock_value),
    order_value: chartNum(d.order_value),
    accepted_request_value: chartNum(d.accepted_request_value),
  }));

  const stockSeries = (stockTrend?.series || []).map((d) => ({
    ...d,
    dateLabel: displayDate(d.date),
    closing: chartNum(d.closing),
    added: chartNum(d.added),
    reduced: chartNum(d.reduced),
  }));

  const movementSeries = (stockMovement?.series || []).map((d) => ({
    ...d,
    dateLabel: displayDate(d.date),
    added: chartNum(d.added),
    reducedNeg: isNullMetric(d.reduced) ? null : -Math.abs(Number(d.reduced)),
  }));

  const coverage = stockTrend?.data_coverage || overall?.data_coverage || {};
  const os = overall?.summary || {};
  const stSum = stockTrend?.summary || {};
  const ordSum = orderSaving?.summary || {};
  const reqSum = requestAcc?.summary || {};
  const movSum = stockMovement?.summary || {};

  return (
    <div className="space-y-8" data-testid="analytics-page">
      <div className="rounded-2xl bg-gradient-to-r from-emerald-600 to-emerald-400 p-6 text-white">
        <div className="flex items-center gap-3">
          <Globe size={32} />
          <div>
            <h1 className="text-2xl font-bold">Analytics</h1>
            <p className="text-emerald-50 text-sm">
              Consolidated insights for {scopeBrand} / {scopeDealer} / {scopeBranch}
            </p>
          </div>
        </div>
      </div>

      <div className="rounded-2xl border bg-white p-5 shadow-sm">
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-4">
          <label className="text-sm font-medium text-gray-700">
            From Date
            <input
              type="date"
              value={fromDate}
              max={defaultTo}
              onChange={(e) => setFromDate(e.target.value)}
              className="mt-1 w-full rounded-xl border px-3 py-2"
            />
          </label>
          <label className="text-sm font-medium text-gray-700">
            To Date
            <input
              type="date"
              value={toDate}
              max={defaultTo}
              onChange={(e) => setToDate(e.target.value)}
              className="mt-1 w-full rounded-xl border px-3 py-2"
            />
          </label>
          <label className="text-sm font-medium text-gray-700">
            Category
            <select
              value={category}
              onChange={(e) => setCategory(e.target.value)}
              className="mt-1 w-full rounded-xl border px-3 py-2"
            >
              <option>All Categories</option>
              {categories.map((c) => (
                <option key={c} value={c}>
                  {c}
                </option>
              ))}
            </select>
          </label>
          <label className="text-sm font-medium text-gray-700">
            Aging Type
            <select
              value={agingType}
              onChange={(e) => setAgingType(e.target.value)}
              className="mt-1 w-full rounded-xl border px-3 py-2"
            >
              <option value="purchase">Purchase Aging</option>
              <option value="sales">Sales Aging</option>
            </select>
          </label>
          <label className="text-sm font-medium text-gray-700">
            Metric
            <select
              value={metricType}
              onChange={(e) => setMetricType(e.target.value)}
              className="mt-1 w-full rounded-xl border px-3 py-2"
            >
              <option value="value">Value</option>
              <option value="quantity">Quantity</option>
            </select>
          </label>
          <label className="text-sm font-medium text-gray-700">
            Request scope
            <select
              value={requestDirection}
              onChange={(e) => setRequestDirection(e.target.value)}
              className="mt-1 w-full rounded-xl border px-3 py-2"
            >
              <option value="raised">Requests raised by selected scope</option>
              <option value="received">Requests received by selected scope</option>
            </select>
          </label>
        </div>
        <p className="mt-3 text-xs text-gray-500">
          Brand, dealer and branch follow the dashboard scope bar (role permissions enforced on the server).
        </p>
      </div>

      {loading && (
        <div className="flex items-center justify-center gap-2 text-gray-600 py-8">
          <Loader2 className="animate-spin" /> Loading analytics…
        </div>
      )}
      {error && !loading && (
        <div className="rounded-2xl border border-red-200 bg-red-50 p-6 text-red-800">{error}</div>
      )}

      {!loading && !error && (
        <>
          <Section title="1. Overall View">
            {os.data_status === 'NO_UPLOAD' && (
              <p className="text-sm text-amber-800 bg-amber-50 border border-amber-200 rounded-xl px-4 py-2">
                Status: No Stock Upload on selected end date.
                {os.last_available_upload_date && (
                  <span className="ml-2">
                    Last available upload: {displayDate(os.last_available_upload_date)} (
                    {isNullMetric(os.last_available_stock_value) ? 'N/A' : formatINRCompact(os.last_available_stock_value)})
                  </span>
                )}
              </p>
            )}
            {os.data_status === 'PARTIAL_UPLOAD' && (
              <button
                type="button"
                onClick={() => openDrill('missing_upload', { focusDate: toDate })}
                className="text-sm text-amber-900 bg-amber-100 border border-amber-300 rounded-xl px-4 py-2 text-left w-full"
              >
                Partial Stock Data — {os.uploaded_branch_count} of {os.expected_branch_count} branches uploaded (
                {formatINR(os.coverage_percentage)}% coverage). Click for branch details.
              </button>
            )}
            <div className="grid grid-cols-2 lg:grid-cols-5 gap-3">
              <MetricCard
                label="Current Stock Value"
                value={os.data_status === 'NO_UPLOAD' ? 'N/A' : formatINRCompact(os.current_stock_value)}
                compareValue={os.data_status === 'NO_UPLOAD' ? undefined : os.previous_stock_value}
                hint={os.comparison_label || comparisonCaption(os.comparison_type)}
              />
              <MetricCard
                label={`Previous (${os.comparison_label || 'Last Upload'})`}
                value={isNullMetric(os.previous_stock_value) ? 'N/A' : formatINRCompact(os.previous_stock_value)}
              />
              <MetricCard
                label="Stock Added"
                value={isNullMetric(os.stock_added_value) ? 'N/A' : formatINRCompact(os.stock_added_value)}
                onClick={() => (os.data_status === 'NO_UPLOAD' ? openDrill('missing_upload', { focusDate: toDate }) : openDrill('added', { focusDate: toDate }))}
              />
              <MetricCard
                label="Stock Reduced"
                value={isNullMetric(os.stock_reduced_value) ? 'N/A' : formatINRCompact(os.stock_reduced_value)}
                onClick={() => (os.data_status === 'NO_UPLOAD' ? openDrill('missing_upload', { focusDate: toDate }) : openDrill('reduced', { focusDate: toDate }))}
              />
              <MetricCard
                label="Net Change"
                value={isNullMetric(os.net_change_value) ? 'N/A' : formatINRCompact(os.net_change_value)}
                hint={comparisonCaption(os.comparison_type)}
              />
              <MetricCard
                label="Change %"
                value={isNullMetric(os.change_pct_value) ? 'N/A' : `${formatINR(os.change_pct_value)}%`}
              />
              <MetricCard
                label="Total Order Value"
                value={formatINRCompact(os.total_order_value)}
                onClick={() => openDrill('order_saving')}
              />
              <MetricCard label="NMTS Sourced Value" value={formatINRCompact(os.nmts_sourced_value)} />
              <MetricCard
                label="Total Requested Value"
                value={formatINRCompact(os.total_requested_value)}
                onClick={() => openDrill('request')}
              />
              <MetricCard
                label="Total Accepted Value"
                value={formatINRCompact(os.total_accepted_value)}
                onClick={() => openDrill('request')}
              />
            </div>
            <ChartPanel title="Overall trend" empty={!overallSeries.length}>
              <div className="flex flex-wrap gap-3 mb-3 text-sm">
                {[
                  ['closing_stock_value', 'Closing Stock'],
                  ['order_value', 'Order Value'],
                  ['accepted_request_value', 'Accepted Requests'],
                ].map(([key, label]) => (
                  <label key={key} className="inline-flex items-center gap-2">
                    <input
                      type="checkbox"
                      checked={seriesVisible[key]}
                      onChange={(e) => setSeriesVisible((s) => ({ ...s, [key]: e.target.checked }))}
                    />
                    {label}
                  </label>
                ))}
              </div>
              <ResponsiveContainer width="100%" height={320}>
                <LineChart data={overallSeries}>
                  <CartesianGrid strokeDasharray="3 3" />
                  <XAxis dataKey="dateLabel" fontSize={11} />
                  <YAxis tickFormatter={(v) => formatINRCompact(v)} width={90} />
                  <Tooltip formatter={(v) => formatINRCompact(v)} labelFormatter={displayDate} />
                  <Legend />
                  {seriesVisible.closing_stock_value && (
                    <Line
                      type="monotone"
                      dataKey="closing_stock_value"
                      name="Closing Stock"
                      stroke="#059669"
                      connectNulls={false}
                      dot={(props) => {
                        const { cx, cy, payload } = props;
                        if (payload?.data_status === 'NO_UPLOAD') {
                          return (
                            <text x={cx} y={cy} dy={-6} textAnchor="middle" fontSize={9} fill="#b45309">
                              No Upload
                            </text>
                          );
                        }
                        return payload?.closing_stock_value != null ? (
                          <circle cx={cx} cy={cy} r={3} fill="#059669" />
                        ) : null;
                      }}
                    />
                  )}
                  {seriesVisible.order_value && (
                    <Line type="monotone" dataKey="order_value" name="Order Value" stroke="#2563eb" dot={false} />
                  )}
                  {seriesVisible.accepted_request_value && (
                    <Line type="monotone" dataKey="accepted_request_value" name="Accepted Requests" stroke="#f59e0b" dot={false} />
                  )}
                </LineChart>
              </ResponsiveContainer>
            </ChartPanel>
          </Section>

          <Section title="2. Stock & Aging Analytics">
            <div className="rounded-xl border bg-slate-50 px-4 py-3 text-sm text-slate-700 flex flex-wrap gap-4 items-center">
              <span className="font-semibold">Data coverage</span>
              <span>Days: {coverage.total_calendar_days ?? '—'}</span>
              <span>Full: {coverage.full_upload_days ?? 0}</span>
              <span>Partial: {coverage.partial_upload_days ?? 0}</span>
              <span>No upload: {coverage.no_upload_days ?? 0}</span>
              <span>Coverage: {isNullMetric(coverage.coverage_percentage) ? 'N/A' : `${formatINR(coverage.coverage_percentage)}%`}</span>
            </div>
            {stSum.data_status === 'PARTIAL_UPLOAD' && (
              <button
                type="button"
                className="w-full text-left text-sm font-semibold text-amber-900 bg-amber-100 border border-amber-300 rounded-xl px-4 py-2"
                onClick={() => openDrill('missing_upload', { focusDate: toDate })}
              >
                Partial Stock Data — {stSum.uploaded_branch_count} of {stSum.expected_branch_count} branches (
                {formatINR(stSum.coverage_percentage)}%)
              </button>
            )}
            <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
              <MetricCard label="Current Total" value={stockMoney(stSum.current_total)} hint={stSum.data_status === 'NO_UPLOAD' ? 'No Stock Upload' : stSum.comparison_label} />
              <MetricCard label="Opening (last upload)" value={stockMoney(stSum.opening)} />
              <MetricCard label="Closing" value={stockMoney(stSum.closing)} />
              <MetricCard label="Added" value={stockMoney(stSum.added)} onClick={() => (stSum.data_status === 'NO_UPLOAD' ? openDrill('missing_upload', { focusDate: toDate }) : openDrill('added', { focusDate: toDate }))} />
              <MetricCard label="Reduced" value={stockMoney(stSum.reduced)} onClick={() => (stSum.data_status === 'NO_UPLOAD' ? openDrill('missing_upload', { focusDate: toDate }) : openDrill('reduced', { focusDate: toDate }))} />
              <MetricCard label="Net Change" value={stockMoney(stSum.net_change)} hint={comparisonCaption(stSum.comparison_type)} />
              <MetricCard label="Change %" value={isNullMetric(stSum.change_pct) ? 'N/A' : `${formatINR(stSum.change_pct)}%`} />
            </div>
            <ChartPanel title="Daily stock trend" empty={!stockSeries.length}>
              <ResponsiveContainer width="100%" height={300}>
                <AreaChart
                  data={stockSeries}
                  onClick={(e) => {
                    const p = e?.activePayload?.[0]?.payload;
                    if (p?.data_status === 'NO_UPLOAD') openDrill('missing_upload', { focusDate: p.date });
                  }}
                >
                  <CartesianGrid strokeDasharray="3 3" />
                  <XAxis dataKey="dateLabel" />
                  <YAxis tickFormatter={(v) => stockMoney(v)} width={90} />
                  <Tooltip content={<StockTrendTooltip metricType={metricType} />} />
                  <Area
                    type="monotone"
                    dataKey="closing"
                    name="Closing"
                    stroke="#059669"
                    fill="#a7f3d0"
                    connectNulls={false}
                    dot={(props) => {
                      const { cx, cy, payload } = props;
                      if (payload?.data_status === 'NO_UPLOAD') {
                        return (
                          <text x={cx} y={cy} dy={-4} textAnchor="middle" fontSize={9} fill="#b45309">
                            No Upload
                          </text>
                        );
                      }
                      return payload?.closing != null ? <circle cx={cx} cy={cy} r={3} fill="#059669" /> : null;
                    }}
                  />
                </AreaChart>
              </ResponsiveContainer>
            </ChartPanel>
            <ChartPanel title="Category-wise stock" empty={!(categoryTrend?.categories || []).length}>
              <ResponsiveContainer width="100%" height={300}>
                <BarChart data={categoryTrend?.categories || []}>
                  <CartesianGrid strokeDasharray="3 3" />
                  <XAxis dataKey="category" fontSize={10} interval={0} angle={-20} textAnchor="end" height={70} />
                  <YAxis tickFormatter={(v) => money(v)} width={90} />
                  <Tooltip formatter={(v) => (isNullMetric(v) ? 'N/A' : money(v))} />
                  <Bar
                    dataKey={metricType === 'value' ? 'current_value' : 'current_qty'}
                    fill="#059669"
                    onClick={(data) => data?.category && setCategory(data.category)}
                  />
                </BarChart>
              </ResponsiveContainer>
            </ChartPanel>
            <div className="grid grid-cols-1 xl:grid-cols-2 gap-6">
              <ChartPanel title="Aging buckets" empty={!(agingTrend?.buckets || []).length}>
                <ResponsiveContainer width="100%" height={280}>
                  <BarChart data={agingTrend?.buckets || []} layout="vertical">
                    <CartesianGrid strokeDasharray="3 3" />
                    <XAxis type="number" tickFormatter={(v) => money(v)} />
                    <YAxis type="category" dataKey="bucket" width={100} />
                    <Tooltip formatter={(v) => (isNullMetric(v) ? 'N/A' : money(v))} />
                    <Bar dataKey={metricType === 'value' ? 'value' : 'quantity'} fill="#047857" />
                  </BarChart>
                </ResponsiveContainer>
              </ChartPanel>
              <ChartPanel title="Category × aging (stacked)" empty={!(agingTrend?.stacked || []).length}>
                <ResponsiveContainer width="100%" height={280}>
                  <BarChart data={agingTrend?.stacked || []}>
                    <CartesianGrid strokeDasharray="3 3" />
                    <XAxis dataKey="category" fontSize={10} />
                    <YAxis tickFormatter={(v) => money(v)} />
                    <Tooltip formatter={(v) => (isNullMetric(v) ? 'N/A' : money(v))} />
                    <Legend />
                    {(agingTrend?.stacked || []).length > 0 &&
                      AGING_BUCKETS.map((b, i) => (
                        <Bar key={b} stackId="a" dataKey={b} fill={['#10b981', '#34d399', '#6ee7b7', '#fbbf24', '#ef4444'][i]} />
                      ))}
                  </BarChart>
                </ResponsiveContainer>
              </ChartPanel>
            </div>
            <ChartPanel title="Daily aging trend" empty={!(agingTrend?.daily || []).length}>
              <ResponsiveContainer width="100%" height={300}>
                <LineChart data={(agingTrend?.daily || []).map((d) => ({ ...d, dateLabel: displayDate(d.date) }))}>
                  <CartesianGrid strokeDasharray="3 3" />
                  <XAxis dataKey="dateLabel" />
                  <YAxis tickFormatter={(v) => money(v)} width={90} />
                  <Tooltip formatter={(v) => (isNullMetric(v) ? 'N/A' : money(v))} />
                  <Legend />
                  {AGING_BUCKETS.map((b, i) => (
                    <Line
                      key={b}
                      type="monotone"
                      dataKey={b}
                      stroke={['#10b981', '#34d399', '#047857', '#f59e0b', '#ef4444'][i]}
                      connectNulls={false}
                      dot={false}
                    />
                  ))}
                </LineChart>
              </ResponsiveContainer>
            </ChartPanel>
          </Section>

          <Section title="3. Order Saving Analytics">
            <div className="grid grid-cols-2 lg:grid-cols-3 gap-3">
              <MetricCard label="Total Orders" value={formatINR(ordSum.total_order_count)} />
              <MetricCard label="Total Order Value" value={formatINRCompact(ordSum.total_order_value)} />
              <MetricCard label="NMTS Sourced" value={formatINRCompact(ordSum.nmts_sourced_value)} onClick={() => openDrill('order_saving')} />
              <MetricCard label="External Purchase Avoided" value={formatINRCompact(ordSum.external_purchase_avoided_value)} />
              <MetricCard label="Unfulfilled" value={formatINRCompact(ordSum.unfulfilled_value)} />
              <MetricCard label="Saving %" value={`${formatINR(ordSum.saving_pct)}%`} />
            </div>
            <ChartPanel title="Order saving daily" empty={!(orderSaving?.series || []).length}>
              <ResponsiveContainer width="100%" height={300}>
                <LineChart data={(orderSaving?.series || []).map((d) => ({ ...d, dateLabel: displayDate(d.date) }))}>
                  <CartesianGrid strokeDasharray="3 3" />
                  <XAxis dataKey="dateLabel" />
                  <YAxis tickFormatter={(v) => formatINRCompact(v)} width={90} />
                  <Tooltip formatter={(v) => formatINRCompact(v)} />
                  <Legend />
                  <Line type="monotone" dataKey="order_value" name="Order Value" stroke="#059669" dot={false} />
                  <Line type="monotone" dataKey="sourced_value" name="NMTS Sourced" stroke="#2563eb" dot={false} />
                  <Line type="monotone" dataKey="unfulfilled_value" name="Unfulfilled" stroke="#ef4444" dot={false} />
                </LineChart>
              </ResponsiveContainer>
            </ChartPanel>
          </Section>

          <Section title="4. Request vs Accepted Analytics">
            <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
              <MetricCard label="Request Count" value={formatINR(reqSum.total_request_count)} />
              <MetricCard label="Requested Value" value={formatINRCompact(reqSum.total_requested_value)} onClick={() => openDrill('request')} />
              <MetricCard label="Accepted Count" value={formatINR(reqSum.accepted_request_count)} />
              <MetricCard label="Fully Accepted Value" value={formatINRCompact(reqSum.fully_accepted_value)} />
              <MetricCard label="Partial Accepted" value={formatINRCompact(reqSum.partial_accepted_value)} />
              <MetricCard label="Rejected Value" value={formatINRCompact(reqSum.rejected_value)} />
              <MetricCard label="Pending Value" value={formatINRCompact(reqSum.pending_value)} />
              <MetricCard label="Acceptance %" value={`${formatINR(reqSum.acceptance_pct)}%`} />
            </div>
            <ChartPanel title="Request vs accepted" empty={!(requestAcc?.series || []).length}>
              <ResponsiveContainer width="100%" height={300}>
                <LineChart data={(requestAcc?.series || []).map((d) => ({ ...d, dateLabel: displayDate(d.date) }))}>
                  <CartesianGrid strokeDasharray="3 3" />
                  <XAxis dataKey="dateLabel" />
                  <YAxis tickFormatter={(v) => formatINRCompact(v)} width={90} />
                  <Tooltip formatter={(v) => formatINRCompact(v)} />
                  <Legend />
                  <Line type="monotone" dataKey="requested" name="Requested" stroke="#6366f1" dot={false} />
                  <Line type="monotone" dataKey="accepted" name="Accepted" stroke="#059669" dot={false} />
                  <Line type="monotone" dataKey="partial" name="Partial" stroke="#f59e0b" dot={false} />
                  <Line type="monotone" dataKey="rejected" name="Rejected" stroke="#ef4444" dot={false} />
                  <Line type="monotone" dataKey="pending" name="Pending" stroke="#9ca3af" dot={false} />
                </LineChart>
              </ResponsiveContainer>
            </ChartPanel>
          </Section>

          <Section title="5. Daily Stock Added vs Reduced">
            <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
              <MetricCard label="Period Added" value={stockMoney(movSum.period_added)} onClick={() => openDrill('added')} />
              <MetricCard label="Period Reduced" value={stockMoney(movSum.period_reduced)} onClick={() => openDrill('reduced')} />
              <MetricCard label="Net Change" value={stockMoney(movSum.net_change)} />
              <MetricCard label="Closing" value={stockMoney(movSum.closing)} />
            </div>
            <ChartPanel title="Added vs reduced (daily)" empty={!movementSeries.length}>
              <ResponsiveContainer width="100%" height={320}>
                <ComposedChart data={movementSeries}>
                  <CartesianGrid strokeDasharray="3 3" />
                  <XAxis dataKey="dateLabel" />
                  <YAxis tickFormatter={(v) => stockMoney(Math.abs(v))} width={90} />
                  <Tooltip formatter={(v, name) => [isNullMetric(v) ? 'N/A' : stockMoney(Math.abs(v)), name]} />
                  <Legend />
                  <Bar dataKey="added" name="Added" fill="#059669" />
                  <Bar dataKey="reducedNeg" name="Reduced" fill="#ef4444" />
                </ComposedChart>
              </ResponsiveContainer>
            </ChartPanel>
          </Section>
        </>
      )}

      <DrilldownModal
        open={!!drill}
        title={
          drillMeta?.message
            ? 'Upload status'
            : drill?.type === 'missing_upload'
              ? 'Branch upload status'
              : drill?.type
                ? `Details — ${drill.type}`
                : 'Details'
        }
        onClose={() => {
          setDrill(null);
          setDrillMeta(null);
        }}
        loading={drillLoading}
        rows={drillRows}
        total={drillTotal}
        page={drillPage}
        pageSize={50}
        onPage={(p) => {
          setDrillPage(p);
          fetchDrill(drill.type, p, drill);
        }}
        headerNote={drillMeta?.message}
        columns={
          drill?.type === 'missing_upload'
            ? [
                { key: 'branch', label: 'Branch' },
                {
                  key: 'uploaded',
                  label: 'Uploaded',
                  render: (r) => (r.uploaded ? 'Yes' : 'No'),
                },
                { key: 'published_at', label: 'Published at', render: (r) => displayDate(String(r.published_at || '').slice(0, 10)) },
                {
                  key: 'total_value',
                  label: 'Branch stock value',
                  render: (r) => (isNullMetric(r.total_value) ? '—' : formatINRCompact(r.total_value)),
                },
              ]
            : drill?.type === 'order_saving'
            ? [
                { key: 'order_number', label: 'Order' },
                { key: 'part_number', label: 'Part' },
                { key: 'nmts_sourced_quantity', label: 'Sourced Qty' },
                { key: 'sourced_value', label: 'Value', render: (r) => formatINRCompact(r.sourced_value) },
                { key: 'source_branch', label: 'From' },
                { key: 'destination_branch', label: 'To' },
                { key: 'transfer_status', label: 'Status' },
              ]
            : drill?.type === 'request'
              ? [
                  { key: 'request_number', label: 'Request' },
                  { key: 'part_number', label: 'Part' },
                  { key: 'requested_quantity', label: 'Req Qty' },
                  { key: 'approved_quantity', label: 'Approved' },
                  { key: 'requested_value', label: 'Req Value', render: (r) => formatINRCompact(r.requested_value) },
                  { key: 'accepted_value', label: 'Accepted', render: (r) => formatINRCompact(r.accepted_value) },
                  { key: 'request_status', label: 'Status' },
                ]
              : [
                  { key: 'date', label: 'Date', render: (r) => displayDate(r.date) },
                  { key: 'part_number', label: 'Part' },
                  { key: 'part_name', label: 'Name' },
                  { key: 'category', label: 'Category' },
                  { key: 'previous_quantity', label: 'Prev Qty' },
                  { key: 'current_quantity', label: 'Curr Qty' },
                  { key: 'added_value', label: 'Added', render: (r) => formatINRCompact(r.added_value) },
                  { key: 'reduced_value', label: 'Reduced', render: (r) => formatINRCompact(r.reduced_value) },
                ]
        }
      />
    </div>
  );
}

export default Analytics;
