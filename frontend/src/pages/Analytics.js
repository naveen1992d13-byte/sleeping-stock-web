import React, { useCallback, useEffect, useMemo, useState } from 'react';
import axios from 'axios';
import { useOutletContext } from 'react-router-dom';
import { API } from '../App';
import { toast } from 'sonner';
import { Loader2 } from 'lucide-react';
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
  ComposedChart,
  PieChart,
  Pie,
  Cell,
  LabelList,
} from 'recharts';

const AGING_BUCKETS = ['0–90 Days', '91–180 Days', '181–270 Days', '271–361 Days', '>361 Days'];
const AGING_COLORS = ['#85c808', '#3b82f6', '#f59e0b', '#f97316', '#ef4444'];
const FULFILL_COLORS = { fulfilled: '#85c808', not_fulfilled: '#94a3b8', branch: '#3b82f6', dealer: '#0f766e' };
// Same Part Type options / source as Product Hub (All | OE Parts | Accessories | Others).
const PART_TYPE_OPTIONS = ['All', 'OE Parts', 'Accessories', 'Others'];

const today = new Date();
const isoLocal = (d) => {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${y}-${m}-${day}`;
};
const monthValue = (d = today) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`;

const monthBounds = (ym) => {
  const [y, m] = String(ym).split('-').map(Number);
  const from = new Date(y, m - 1, 1);
  const last = new Date(y, m, 0);
  const end = last > today ? today : last;
  return { from_date: isoLocal(from), to_date: isoLocal(end) };
};

export const formatINR = (n) =>
  new Intl.NumberFormat('en-IN', { maximumFractionDigits: 2 }).format(Number(n || 0));

export const formatINRCompact = (n) => {
  if (n === null || n === undefined) return 'N/A';
  const v = Number(n || 0);
  const abs = Math.abs(v);
  const sign = v < 0 ? '-' : '';
  if (abs >= 1e7) return `${sign}₹${(abs / 1e7).toFixed(2)} Cr`;
  if (abs >= 1e5) return `${sign}₹${(abs / 1e5).toFixed(2)} L`;
  return `${sign}₹${formatINR(abs)}`;
};

export const formatLakhs = (n) => {
  if (n === null || n === undefined) return 'NO DATA';
  return `₹${(Number(n) / 1e5).toFixed(2)} L`;
};

const displayDate = (iso) => {
  if (!iso) return '—';
  const [y, m, d] = String(iso).slice(0, 10).split('-');
  if (d && m && y) return `${d}-${m}-${y}`;
  return iso;
};

const dayLabel = (iso) => {
  if (!iso) return '';
  return String(iso).slice(8, 10);
};

const isNullMetric = (v) => v === null || v === undefined;

function scopeQuery(scopeBrand, scopeDealer, scopeBranch) {
  const q = {};
  // Never send All* as a concrete filter — omit so backend consolidates authorized scope.
  if (scopeBrand && !String(scopeBrand).startsWith('All')) q.brand = scopeBrand;
  if (scopeDealer && !String(scopeDealer).startsWith('All')) q.dealer = scopeDealer;
  if (scopeBranch && !String(scopeBranch).startsWith('All')) q.branch = scopeBranch;
  return q;
}

function MetricToggle({ value, onChange }) {
  return (
    <div className="inline-flex rounded-lg border bg-gray-50 p-0.5 text-xs font-semibold" data-testid="analytics-metric-toggle">
      {[
        { id: 'value', label: 'Value Wise (₹)' },
        { id: 'quantity', label: 'Item Wise (Qty)' },
      ].map((opt) => (
        <button
          key={opt.id}
          type="button"
          onClick={() => onChange(opt.id)}
          className={`px-3 py-1.5 rounded-md transition ${
            value === opt.id ? 'bg-[#85c808] text-gray-900 shadow-sm' : 'text-gray-600 hover:text-gray-900'
          }`}
        >
          {opt.label}
        </button>
      ))}
    </div>
  );
}

function SectionCard({ title, children }) {
  return (
    <section className="rounded-xl border bg-white p-4 shadow-sm space-y-3" data-testid={`analytics-${title}`}>
      <div className="border-b pb-2">
        <h2 className="text-sm font-bold text-gray-900">{title}</h2>
      </div>
      {children}
    </section>
  );
}

function Kpi({ label, value, accent }) {
  return (
    <div className={`rounded-lg border px-3 py-2 ${accent || 'bg-gray-50'}`}>
      <p className="text-[11px] font-medium text-gray-500 uppercase tracking-wide">{label}</p>
      <p className="mt-1 text-lg font-bold text-gray-900 tabular-nums">{value}</p>
    </div>
  );
}

function CompactTable({ columns, rows, emptyLabel = 'No data for selected filters.' }) {
  if (!rows?.length) {
    return <p className="text-sm text-gray-500 py-4 text-center">{emptyLabel}</p>;
  }
  return (
    <div className="overflow-auto max-h-72 rounded-lg border">
      <table className="min-w-full text-xs">
        <thead className="bg-gray-50 sticky top-0">
          <tr>
            {columns.map((c) => (
              <th key={c.key} className="px-2.5 py-2 text-left font-semibold text-gray-600 whitespace-nowrap">
                {c.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i} className="border-t hover:bg-gray-50/80">
              {columns.map((c) => (
                <td key={c.key} className="px-2.5 py-1.5 whitespace-nowrap tabular-nums text-gray-800">
                  {c.render ? c.render(r) : r[c.key]}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function StockTooltip({ active, payload, metricType = 'value' }) {
  if (!active || !payload?.length) return null;
  const row = payload[0]?.payload;
  if (!row) return null;
  if (row.data_status === 'NO_UPLOAD') {
    return (
      <div className="rounded-lg border bg-white p-3 text-sm shadow-lg">
        <p className="font-semibold">{displayDate(row.date)}</p>
        <p className="text-gray-600 mt-1">NO DATA — no stock upload published.</p>
      </div>
    );
  }
  return (
    <div className="rounded-lg border bg-white p-3 text-sm shadow-lg">
      <p className="font-semibold">{displayDate(row.date)}</p>
      <p className="mt-1">
        {metricType === 'value' ? formatLakhs(row.closing) : formatINR(row.closing)}
      </p>
    </div>
  );
}

export function Analytics() {
  const { scopeBrand, scopeDealer, scopeBranch } = useOutletContext() || {};
  const [month, setMonth] = useState(monthValue());
  const [partType, setPartType] = useState('All');
  const [metricType, setMetricType] = useState('value');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [stockTrend, setStockTrend] = useState(null);
  const [agingTrend, setAgingTrend] = useState(null);
  const [orderSaving, setOrderSaving] = useState(null);
  const [requestAcc, setRequestAcc] = useState(null);

  const bounds = useMemo(() => monthBounds(month), [month]);
  const scopeParams = useMemo(
    () => scopeQuery(scopeBrand, scopeDealer, scopeBranch),
    [scopeBrand, scopeDealer, scopeBranch]
  );

  const loadAll = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const common = {
        ...bounds,
        ...scopeParams,
        metric_type: metricType,
        aging_type: 'purchase',
        ...(partType && partType !== 'All' ? { category: partType } : {}),
      };
      const [st, at, os, ra] = await Promise.all([
        axios.get(`${API}/analytics/stock-trend`, { params: common }),
        axios.get(`${API}/analytics/aging-trend`, { params: common }),
        axios.get(`${API}/analytics/order-saving`, { params: common }),
        axios.get(`${API}/analytics/request-acceptance`, {
          params: { ...common, request_direction: 'received' },
        }),
      ]);
      setStockTrend(st.data);
      setAgingTrend(at.data);
      setOrderSaving(os.data);
      setRequestAcc(ra.data);
    } catch (e) {
      const msg = e.response?.data?.detail || 'Failed to load analytics';
      setError(typeof msg === 'string' ? msg : 'Failed to load analytics');
      toast.error(typeof msg === 'string' ? msg : 'Failed to load analytics');
    } finally {
      setLoading(false);
    }
  }, [bounds, scopeParams, metricType, partType]);

  useEffect(() => {
    loadAll();
  }, [loadAll]);

  const stockSeries = useMemo(
    () =>
      (stockTrend?.series || []).map((d) => ({
        ...d,
        day: dayLabel(d.date),
        closing: isNullMetric(d.closing) ? null : Number(d.closing),
        chartValue: d.data_status === 'NO_UPLOAD' ? null : Number(d.closing ?? 0),
      })),
    [stockTrend]
  );

  const stockTableRows = useMemo(
    () =>
      (stockTrend?.series || []).map((d) => ({
        date: d.date,
        label:
          d.data_status === 'NO_UPLOAD'
            ? 'NO DATA'
            : metricType === 'value'
              ? formatLakhs(d.closing)
              : formatINR(d.closing),
        raw: d.closing,
        status: d.data_status,
      })),
    [stockTrend, metricType]
  );

  const agingDaily = useMemo(
    () =>
      (agingTrend?.daily || []).map((d) => {
        const row = { ...d, day: dayLabel(d.date) };
        if (d.data_status === 'NO_UPLOAD') {
          AGING_BUCKETS.forEach((b) => {
            row[b] = null;
          });
          row.total = null;
        }
        return row;
      }),
    [agingTrend]
  );

  const agingFmt = (n) => {
    if (isNullMetric(n)) return 'NO DATA';
    return metricType === 'value' ? formatINRCompact(n) : formatINR(n);
  };

  const orderFmt = (n) => {
    if (isNullMetric(n)) return '—';
    return metricType === 'value' ? formatINRCompact(n) : formatINR(n);
  };

  const reqFmt = (n) => {
    if (isNullMetric(n)) return '—';
    return metricType === 'value' ? formatINRCompact(n) : formatINR(n);
  };

  const os = useMemo(() => orderSaving?.summary || {}, [orderSaving]);
  const orderChart = useMemo(() => {
    return (orderSaving?.series || []).map((d) => {
      const original = metricType === 'value' ? d.original_order_value : d.original_order_items;
      const reduced = metricType === 'value' ? d.reduced_value : d.reduced_items;
      const final = metricType === 'value' ? d.final_order_value : d.final_order_items;
      return {
        ...d,
        day: dayLabel(d.date),
        original: Number(original || 0),
        reduced: Number(reduced || 0),
        final: Number(final || 0),
        reduction_pct: Number(d.reduction_pct || 0),
      };
    });
  }, [orderSaving, metricType]);

  const rs = useMemo(() => requestAcc?.summary || {}, [requestAcc]);
  const requestChart = useMemo(() => {
    return (requestAcc?.series || []).map((d) => ({
      ...d,
      day: dayLabel(d.date),
      request_received: Number(
        metricType === 'value' ? d.request_received_value : d.request_received_items || 0
      ),
      total_fulfilled: Number(
        metricType === 'value' ? d.total_fulfilled_value : d.total_fulfilled_items || 0
      ),
      not_fulfilled: Number(
        metricType === 'value' ? d.not_fulfilled_value : d.not_fulfilled_items || 0
      ),
      given_to_branches: Number(
        metricType === 'value' ? d.given_to_branches_value : d.given_to_branches_items || 0
      ),
      given_to_dealers: Number(
        metricType === 'value' ? d.given_to_dealers_value : d.given_to_dealers_items || 0
      ),
    }));
  }, [requestAcc, metricType]);

  const donutData = useMemo(() => {
    const fulfilled = Number(
      metricType === 'value' ? rs.total_fulfilled_value : rs.total_fulfilled_items || 0
    );
    const notFulfilled = Number(
      metricType === 'value' ? rs.not_fulfilled_value : rs.not_fulfilled_items || 0
    );
    return [
      { name: 'Fulfilled', value: fulfilled, key: 'fulfilled' },
      { name: 'Not Fulfilled', value: notFulfilled, key: 'not_fulfilled' },
    ].filter((x) => x.value > 0);
  }, [rs, metricType]);

  const fulfillBreakdown = useMemo(() => {
    return [
      {
        name: 'Branches',
        value: Number(
          metricType === 'value' ? rs.given_to_branches_value : rs.given_to_branches_items || 0
        ),
        key: 'branch',
      },
      {
        name: 'Dealers / Co-Dealers',
        value: Number(
          metricType === 'value' ? rs.given_to_dealers_value : rs.given_to_dealers_items || 0
        ),
        key: 'dealer',
      },
    ];
  }, [rs, metricType]);

  const originalOrder = metricType === 'value' ? os.original_order_value : os.original_order_items;
  const reducedOrder = metricType === 'value' ? os.reduced_value : os.reduced_items;
  const finalOrder = metricType === 'value' ? os.final_order_value : os.final_order_items;

  return (
    <div className="space-y-4" data-testid="analytics-page">
      {/* Compact analytical toolbar — Brand/Dealer/Branch come from global dashboard scope only. */}
      <div className="rounded-xl border bg-white px-4 py-3 shadow-sm">
        <div className="flex flex-wrap items-center gap-3">
          <h1 className="text-base font-bold text-gray-900 mr-1">Analytics</h1>
          <label className="text-xs font-medium text-gray-600 flex items-center gap-1.5">
            Month
            <input
              type="month"
              value={month}
              max={monthValue()}
              onChange={(e) => setMonth(e.target.value)}
              className="rounded-lg border px-2.5 py-1.5 text-sm"
              data-testid="analytics-month"
            />
          </label>
          <label className="text-xs font-medium text-gray-600 flex items-center gap-1.5">
            Part Type
            <select
              value={partType}
              onChange={(e) => setPartType(e.target.value)}
              className="rounded-lg border px-2.5 py-1.5 text-sm min-w-[8.5rem]"
              data-testid="analytics-part-type"
              title="Same Part Type source as Product Hub"
            >
              {PART_TYPE_OPTIONS.map((opt) => (
                <option key={opt} value={opt}>
                  {opt}
                </option>
              ))}
            </select>
          </label>
          <div className="ml-auto">
            <MetricToggle value={metricType} onChange={setMetricType} />
          </div>
        </div>
        <div className="mt-2 flex flex-wrap gap-1.5 text-[11px]">
          <span className="rounded bg-gray-100 px-2 py-0.5 font-medium text-gray-700">
            Brand: {scopeBrand || '—'}
          </span>
          <span className="rounded bg-gray-100 px-2 py-0.5 font-medium text-gray-700">
            Dealer: {scopeDealer || '—'}
          </span>
          <span className="rounded bg-gray-100 px-2 py-0.5 font-medium text-gray-700">
            Branch: {scopeBranch || '—'}
          </span>
          <span className="rounded bg-gray-100 px-2 py-0.5 font-medium text-gray-700">
            Part Type: {partType}
          </span>
          <span className="rounded bg-gray-100 px-2 py-0.5 text-gray-500">
            {bounds.from_date} → {bounds.to_date}
          </span>
        </div>
      </div>

      {loading && (
        <div className="flex items-center justify-center gap-2 py-8 text-gray-500">
          <Loader2 className="h-5 w-5 animate-spin" />
          Loading analytics…
        </div>
      )}
      {error && !loading && (
        <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">{error}</div>
      )}

      {!loading && (
        <>
          {/* 1. Daily Stock Value Trend */}
          <SectionCard
            title={
              metricType === 'value' ? 'Daily Stock Value Trend' : 'Daily Stock Item Trend'
            }
          >
            <div className="h-64">
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={stockSeries} margin={{ top: 16, right: 12, left: 0, bottom: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb" />
                  <XAxis dataKey="day" tick={{ fontSize: 11 }} />
                  <YAxis
                    tick={{ fontSize: 11 }}
                    tickFormatter={(v) =>
                      metricType === 'value' ? `${(Number(v) / 1e5).toFixed(1)}L` : formatINR(v)
                    }
                  />
                  <Tooltip content={<StockTooltip metricType={metricType} />} />
                  <Line
                    type="monotone"
                    dataKey="chartValue"
                    name={metricType === 'value' ? 'Stock Value' : 'Stock Items'}
                    stroke="#85c808"
                    strokeWidth={2.5}
                    dot={{ r: 3, fill: '#85c808' }}
                    connectNulls={false}
                    isAnimationActive
                  >
                    <LabelList
                      dataKey="chartValue"
                      position="top"
                      formatter={(v) =>
                        v == null
                          ? ''
                          : metricType === 'value'
                            ? `${(Number(v) / 1e5).toFixed(2)}L`
                            : formatINR(v)
                      }
                      style={{ fontSize: 9, fill: '#374151' }}
                    />
                  </Line>
                </LineChart>
              </ResponsiveContainer>
            </div>
            <CompactTable
              columns={[
                { key: 'date', label: 'Date', render: (r) => displayDate(r.date) },
                { key: 'label', label: 'Stock Value' },
              ]}
              rows={stockTableRows}
            />
          </SectionCard>

          {/* 2. Stock Aging Analysis */}
          <SectionCard
            title="Stock Aging Analysis"
          >
            <div className="h-80">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={agingDaily} margin={{ top: 8, right: 12, left: 0, bottom: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb" />
                  <XAxis dataKey="day" tick={{ fontSize: 11 }} />
                  <YAxis
                    tick={{ fontSize: 11 }}
                    tickFormatter={(v) =>
                      metricType === 'value' ? `${(Number(v) / 1e5).toFixed(1)}L` : formatINR(v)
                    }
                  />
                  <Tooltip
                    formatter={(v, name) => [
                      isNullMetric(v) ? 'NO DATA' : metricType === 'value' ? formatINRCompact(v) : formatINR(v),
                      name,
                    ]}
                    labelFormatter={(l, items) => {
                      const d = items?.[0]?.payload?.date;
                      return d ? displayDate(d) : l;
                    }}
                  />
                  <Legend wrapperStyle={{ fontSize: 11 }} />
                  {AGING_BUCKETS.map((b, i) => (
                    <Bar
                      key={b}
                      dataKey={b}
                      stackId="aging"
                      fill={AGING_COLORS[i]}
                      name={b}
                      maxBarSize={28}
                      isAnimationActive
                    />
                  ))}
                </BarChart>
              </ResponsiveContainer>
            </div>
            <CompactTable
              columns={[
                { key: 'date', label: 'Date', render: (r) => displayDate(r.date) },
                ...AGING_BUCKETS.map((b) => ({
                  key: b,
                  label: b.replace(' Days', ''),
                  render: (r) => (r.data_status === 'NO_UPLOAD' ? 'NO DATA' : agingFmt(r[b])),
                })),
                {
                  key: 'total',
                  label: 'Total',
                  render: (r) => (r.data_status === 'NO_UPLOAD' ? 'NO DATA' : agingFmt(r.total)),
                },
              ]}
              rows={agingTrend?.daily || []}
            />
          </SectionCard>

          {/* 3. Orders & Savings Analysis */}
          <SectionCard
            title="Orders & Savings Analysis"
          >
            <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
              <Kpi label="Original / Total Order" value={orderFmt(originalOrder)} />
              <Kpi
                label="Reduced / Cut through NMTS"
                value={orderFmt(reducedOrder)}
                accent="bg-lime-50 border-lime-200"
              />
              <Kpi label="Final / Net Order" value={orderFmt(finalOrder)} />
              <Kpi
                label="Reduction %"
                value={`${formatINR(os.reduction_pct || 0)}%`}
                accent="bg-emerald-50 border-emerald-200"
              />
            </div>
            <div className="h-72">
              <ResponsiveContainer width="100%" height="100%">
                <ComposedChart data={orderChart} margin={{ top: 8, right: 12, left: 0, bottom: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb" />
                  <XAxis dataKey="day" tick={{ fontSize: 11 }} />
                  <YAxis
                    yAxisId="left"
                    tick={{ fontSize: 11 }}
                    tickFormatter={(v) =>
                      metricType === 'value' ? `${(Number(v) / 1e5).toFixed(1)}L` : formatINR(v)
                    }
                  />
                  <YAxis
                    yAxisId="right"
                    orientation="right"
                    tick={{ fontSize: 11 }}
                    domain={[0, 100]}
                    tickFormatter={(v) => `${v}%`}
                  />
                  <Tooltip
                    formatter={(v, name) => {
                      if (name === 'Reduction %') return [`${formatINR(v)}%`, name];
                      return [metricType === 'value' ? formatINRCompact(v) : formatINR(v), name];
                    }}
                    labelFormatter={(l, items) => {
                      const d = items?.[0]?.payload?.date;
                      return d ? displayDate(d) : l;
                    }}
                  />
                  <Legend wrapperStyle={{ fontSize: 11 }} />
                  <Bar yAxisId="left" dataKey="original" name="Original Order" fill="#94a3b8" maxBarSize={22} />
                  <Bar yAxisId="left" dataKey="reduced" name="Reduced / Cut" fill="#85c808" maxBarSize={22} />
                  <Bar yAxisId="left" dataKey="final" name="Final / Net Order" fill="#1e293b" maxBarSize={22} />
                  <Line
                    yAxisId="right"
                    type="monotone"
                    dataKey="reduction_pct"
                    name="Reduction %"
                    stroke="#ef4444"
                    strokeWidth={2}
                    dot={{ r: 2 }}
                  />
                </ComposedChart>
              </ResponsiveContainer>
            </div>
            <CompactTable
              columns={[
                { key: 'date', label: 'Date', render: (r) => displayDate(r.date) },
                {
                  key: 'original',
                  label: 'Original Order',
                  render: (r) =>
                    orderFmt(metricType === 'value' ? r.original_order_value : r.original_order_items),
                },
                {
                  key: 'reduced',
                  label: 'Reduced / Cut',
                  render: (r) => orderFmt(metricType === 'value' ? r.reduced_value : r.reduced_items),
                },
                {
                  key: 'final',
                  label: 'Final / Net Order',
                  render: (r) =>
                    orderFmt(metricType === 'value' ? r.final_order_value : r.final_order_items),
                },
                {
                  key: 'pct',
                  label: 'Reduction %',
                  render: (r) => `${formatINR(r.reduction_pct || 0)}%`,
                },
              ]}
              rows={orderSaving?.series || []}
            />
          </SectionCard>

          {/* 4. Request Fulfillment Analysis */}
          <SectionCard
            title="Request Fulfillment Analysis"
          >
            <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
              <Kpi
                label="Request Received"
                value={reqFmt(
                  metricType === 'value' ? rs.request_received_value : rs.request_received_items
                )}
              />
              <Kpi
                label="Total Fulfilled"
                value={reqFmt(
                  metricType === 'value' ? rs.total_fulfilled_value : rs.total_fulfilled_items
                )}
                accent="bg-lime-50 border-lime-200"
              />
              <Kpi
                label="Not Fulfilled"
                value={reqFmt(
                  metricType === 'value' ? rs.not_fulfilled_value : rs.not_fulfilled_items
                )}
              />
              <Kpi
                label="Fulfillment %"
                value={`${formatINR(rs.fulfillment_pct || 0)}%`}
                accent="bg-emerald-50 border-emerald-200"
              />
            </div>
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
              <div className="h-64">
                <p className="text-xs font-semibold text-gray-500 mb-2">Fulfilled vs Not Fulfilled</p>
                <ResponsiveContainer width="100%" height="90%">
                  <PieChart>
                    <Pie
                      data={donutData.length ? donutData : [{ name: 'No data', value: 1, key: 'not_fulfilled' }]}
                      dataKey="value"
                      nameKey="name"
                      innerRadius={55}
                      outerRadius={85}
                      paddingAngle={2}
                    >
                      {(donutData.length ? donutData : [{ key: 'not_fulfilled' }]).map((entry) => (
                        <Cell key={entry.key} fill={FULFILL_COLORS[entry.key] || '#cbd5e1'} />
                      ))}
                    </Pie>
                    <Tooltip
                      formatter={(v, name) => [
                        metricType === 'value' ? formatINRCompact(v) : formatINR(v),
                        name,
                      ]}
                    />
                    <Legend wrapperStyle={{ fontSize: 11 }} />
                  </PieChart>
                </ResponsiveContainer>
              </div>
              <div className="h-64">
                <p className="text-xs font-semibold text-gray-500 mb-2">
                  Fulfilled breakdown: Branch vs Dealer / Co-Dealer
                </p>
                <ResponsiveContainer width="100%" height="90%">
                  <BarChart data={fulfillBreakdown} layout="vertical" margin={{ left: 8, right: 16 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb" />
                    <XAxis
                      type="number"
                      tick={{ fontSize: 11 }}
                      tickFormatter={(v) =>
                        metricType === 'value' ? `${(Number(v) / 1e5).toFixed(1)}L` : formatINR(v)
                      }
                    />
                    <YAxis type="category" dataKey="name" width={120} tick={{ fontSize: 11 }} />
                    <Tooltip
                      formatter={(v) =>
                        metricType === 'value' ? formatINRCompact(v) : formatINR(v)
                      }
                    />
                    <Bar dataKey="value" maxBarSize={28}>
                      {fulfillBreakdown.map((entry) => (
                        <Cell key={entry.key} fill={FULFILL_COLORS[entry.key]} />
                      ))}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </div>
            <div className="h-56">
              <p className="text-xs font-semibold text-gray-500 mb-2">Daily fulfillment trend</p>
              <ResponsiveContainer width="100%" height="90%">
                <ComposedChart data={requestChart} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb" />
                  <XAxis dataKey="day" tick={{ fontSize: 11 }} />
                  <YAxis
                    tick={{ fontSize: 11 }}
                    tickFormatter={(v) =>
                      metricType === 'value' ? `${(Number(v) / 1e5).toFixed(1)}L` : formatINR(v)
                    }
                  />
                  <Tooltip
                    formatter={(v, name) => [
                      metricType === 'value' ? formatINRCompact(v) : formatINR(v),
                      name,
                    ]}
                    labelFormatter={(l, items) => {
                      const d = items?.[0]?.payload?.date;
                      return d ? displayDate(d) : l;
                    }}
                  />
                  <Legend wrapperStyle={{ fontSize: 11 }} />
                  <Bar dataKey="total_fulfilled" name="Fulfilled" fill="#85c808" maxBarSize={18} stackId="f" />
                  <Bar dataKey="not_fulfilled" name="Not Fulfilled" fill="#cbd5e1" maxBarSize={18} stackId="f" />
                  <Line
                    type="monotone"
                    dataKey="request_received"
                    name="Request Received"
                    stroke="#1e293b"
                    strokeWidth={2}
                    dot={false}
                  />
                </ComposedChart>
              </ResponsiveContainer>
            </div>
            <CompactTable
              columns={[
                { key: 'date', label: 'Date', render: (r) => displayDate(r.date) },
                {
                  key: 'recv',
                  label: 'Request Received',
                  render: (r) =>
                    reqFmt(
                      metricType === 'value' ? r.request_received_value : r.request_received_items
                    ),
                },
                {
                  key: 'br',
                  label: 'Given to Branches',
                  render: (r) =>
                    reqFmt(
                      metricType === 'value' ? r.given_to_branches_value : r.given_to_branches_items
                    ),
                },
                {
                  key: 'dl',
                  label: 'Given to Dealers / Co-Dealers',
                  render: (r) =>
                    reqFmt(
                      metricType === 'value' ? r.given_to_dealers_value : r.given_to_dealers_items
                    ),
                },
                {
                  key: 'ful',
                  label: 'Total Fulfilled',
                  render: (r) =>
                    reqFmt(
                      metricType === 'value' ? r.total_fulfilled_value : r.total_fulfilled_items
                    ),
                },
                {
                  key: 'nf',
                  label: 'Not Fulfilled',
                  render: (r) =>
                    reqFmt(metricType === 'value' ? r.not_fulfilled_value : r.not_fulfilled_items),
                },
                {
                  key: 'pct',
                  label: 'Fulfillment %',
                  render: (r) => `${formatINR(r.fulfillment_pct || 0)}%`,
                },
              ]}
              rows={requestAcc?.series || []}
            />
          </SectionCard>
        </>
      )}
    </div>
  );
}

export default Analytics;
