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

function DrilldownModal({ open, title, onClose, loading, rows, columns, page, total, pageSize, onPage }) {
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

  const money = (n) => (metricType === 'value' ? formatINRCompact(n) : formatINR(n));

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
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Drill-down failed');
    } finally {
      setDrillLoading(false);
    }
  };

  const overallSeries = (overall?.series || []).map((d) => ({
    ...d,
    dateLabel: displayDate(d.date),
  }));

  const stockSeries = (stockTrend?.series || []).map((d) => ({
    ...d,
    dateLabel: displayDate(d.date),
  }));

  const movementSeries = (stockMovement?.series || []).map((d) => ({
    ...d,
    dateLabel: displayDate(d.date),
    reducedNeg: -Math.abs(d.reduced || 0),
  }));

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
            <div className="grid grid-cols-2 lg:grid-cols-5 gap-3">
              <MetricCard
                label="Current Stock Value"
                value={formatINRCompact(os.current_stock_value)}
                compareValue={os.previous_stock_value}
              />
              <MetricCard
                label="Previous Period Stock"
                value={formatINRCompact(os.previous_stock_value)}
              />
              <MetricCard
                label="Stock Added"
                value={formatINRCompact(os.stock_added_value)}
                onClick={() => openDrill('added', { focusDate: toDate })}
              />
              <MetricCard
                label="Stock Reduced"
                value={formatINRCompact(os.stock_reduced_value)}
                onClick={() => openDrill('reduced', { focusDate: toDate })}
              />
              <MetricCard
                label="Net Change"
                value={formatINRCompact(os.net_change_value)}
                compareValue={os.previous_stock_value}
              />
              <MetricCard label="Change %" value={`${formatINR(os.change_pct_value)}%`} />
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
                    <Line type="monotone" dataKey="closing_stock_value" name="Closing Stock" stroke="#059669" dot={false} />
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
            <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
              <MetricCard label="Current Total" value={money(stSum.current_total)} />
              <MetricCard label="Opening" value={money(stSum.opening)} />
              <MetricCard label="Closing" value={money(stSum.closing)} />
              <MetricCard label="Added" value={money(stSum.added)} onClick={() => openDrill('added', { focusDate: toDate })} />
              <MetricCard label="Reduced" value={money(stSum.reduced)} onClick={() => openDrill('reduced', { focusDate: toDate })} />
              <MetricCard label="Net Change" value={money(stSum.net_change)} />
              <MetricCard label="Change %" value={`${formatINR(stSum.change_pct)}%`} />
            </div>
            <ChartPanel title="Daily stock trend" empty={!stockSeries.length}>
              <ResponsiveContainer width="100%" height={300}>
                <AreaChart data={stockSeries}>
                  <CartesianGrid strokeDasharray="3 3" />
                  <XAxis dataKey="dateLabel" />
                  <YAxis tickFormatter={(v) => money(v)} width={90} />
                  <Tooltip
                    formatter={(v, name) => [money(v), name]}
                    labelFormatter={displayDate}
                  />
                  <Area type="monotone" dataKey="closing" name="Closing" stroke="#059669" fill="#a7f3d0" />
                </AreaChart>
              </ResponsiveContainer>
            </ChartPanel>
            <ChartPanel title="Category-wise stock" empty={!(categoryTrend?.categories || []).length}>
              <ResponsiveContainer width="100%" height={300}>
                <BarChart data={categoryTrend?.categories || []}>
                  <CartesianGrid strokeDasharray="3 3" />
                  <XAxis dataKey="category" fontSize={10} interval={0} angle={-20} textAnchor="end" height={70} />
                  <YAxis tickFormatter={(v) => money(v)} width={90} />
                  <Tooltip formatter={(v) => money(v)} />
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
                    <Tooltip formatter={(v) => money(v)} />
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
                    <Tooltip formatter={(v) => money(v)} />
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
                  <Tooltip formatter={(v) => money(v)} />
                  <Legend />
                  {AGING_BUCKETS.map((b, i) => (
                    <Line key={b} type="monotone" dataKey={b} stroke={['#10b981', '#34d399', '#047857', '#f59e0b', '#ef4444'][i]} dot={false} />
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
              <MetricCard label="Period Added" value={money(movSum.period_added)} onClick={() => openDrill('added')} />
              <MetricCard label="Period Reduced" value={money(movSum.period_reduced)} onClick={() => openDrill('reduced')} />
              <MetricCard label="Net Change" value={money(movSum.net_change)} />
              <MetricCard label="Closing" value={money(movSum.closing)} />
            </div>
            <ChartPanel title="Added vs reduced (daily)" empty={!movementSeries.length}>
              <ResponsiveContainer width="100%" height={320}>
                <ComposedChart data={movementSeries}>
                  <CartesianGrid strokeDasharray="3 3" />
                  <XAxis dataKey="dateLabel" />
                  <YAxis tickFormatter={(v) => money(Math.abs(v))} width={90} />
                  <Tooltip formatter={(v, name) => [money(Math.abs(v)), name]} />
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
        title={drill?.type ? `Details — ${drill.type}` : 'Details'}
        onClose={() => setDrill(null)}
        loading={drillLoading}
        rows={drillRows}
        total={drillTotal}
        page={drillPage}
        pageSize={50}
        onPage={(p) => {
          setDrillPage(p);
          fetchDrill(drill.type, p, drill);
        }}
        columns={
          drill?.type === 'order_saving'
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
