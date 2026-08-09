import React, { useCallback, useEffect, useMemo, useState } from 'react';
import axios from 'axios';
import { useOutletContext } from 'react-router-dom';
import { API } from '../App';
import { toast } from 'sonner';
import {
  Info,
  Loader2,
  Package,
  ShoppingCart,
  TrendingDown,
  Warehouse,
} from 'lucide-react';
import {
  ResponsiveContainer,
  LineChart,
  Line,
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
  BarChart,
} from 'recharts';

const AGING_BUCKETS = ['0–90 Days', '91–180 Days', '181–270 Days', '271–361 Days', '>361 Days'];
// Sample image palette: green / blue / orange / purple / red
const AGING_COLORS = ['#22c55e', '#3b82f6', '#f59e0b', '#a855f7', '#ef4444'];
const ORDER_COLORS = {
  original: '#3b82f6',
  reduced: '#ef4444',
  final: '#22c55e',
  reduction_pct: '#f59e0b',
};
const FULFILL_COLORS = {
  fulfilled: '#22c55e',
  not_fulfilled: '#ef4444',
  branch: '#22c55e',
  dealer: '#3b82f6',
};
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
  if (abs >= 1e7) return `${sign}₹ ${(abs / 1e7).toFixed(2)} Cr`;
  if (abs >= 1e5) return `${sign}₹ ${(abs / 1e5).toFixed(2)} L`;
  return `${sign}₹ ${formatINR(abs)}`;
};

export const formatLakhs = (n) => {
  if (n === null || n === undefined) return 'NO DATA';
  return `₹ ${(Number(n) / 1e5).toFixed(2)} L`;
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

const monthLabel = (ym) => {
  if (!ym) return '';
  const [y, m] = String(ym).split('-').map(Number);
  const d = new Date(y, (m || 1) - 1, 1);
  return d.toLocaleDateString('en-IN', { month: 'short', year: 'numeric' });
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

const selectClass =
  'h-8 min-w-[118px] max-w-[160px] rounded border border-gray-300 bg-white px-2 text-[11px] font-medium text-gray-800 shadow-sm focus:border-[#85c808] focus:outline-none focus:ring-1 focus:ring-[#85c808]';

function MetricToggle({ value, onChange }) {
  return (
    <div
      className="inline-flex overflow-hidden rounded border border-gray-300 bg-white text-[11px] font-semibold shadow-sm"
      data-testid="analytics-metric-toggle"
    >
      {[
        { id: 'value', label: 'Value Wise (₹)' },
        { id: 'quantity', label: 'Item Wise (Qty)' },
      ].map((opt) => (
        <button
          key={opt.id}
          type="button"
          onClick={() => onChange(opt.id)}
          className={`px-3 py-1.5 transition ${
            value === opt.id
              ? 'bg-[#85c808] text-white'
              : 'bg-white text-gray-600 hover:bg-gray-50'
          }`}
          data-testid={`analytics-metric-${opt.id}`}
        >
          {opt.label}
        </button>
      ))}
    </div>
  );
}

function UnitBadge({ metricMode }) {
  return (
    <span className="rounded border border-gray-200 bg-gray-50 px-2 py-0.5 text-[10px] font-semibold text-gray-600">
      {metricMode === 'value' ? '₹ Lakhs' : 'Qty'}
    </span>
  );
}

function SectionCard({ title, hint, metricMode, children, footer, testId }) {
  return (
    <section
      className="flex min-h-0 flex-col overflow-hidden rounded-lg border border-gray-200 bg-white shadow-sm"
      data-testid={testId}
    >
      <div className="flex items-center justify-between gap-2 border-b border-gray-100 px-3 py-2">
        <div className="flex min-w-0 items-center gap-1.5">
          <h3 className="truncate text-[13px] font-bold text-gray-900">{title}</h3>
          {hint ? (
            <span title={hint} className="text-gray-400">
              <Info className="h-3.5 w-3.5" />
            </span>
          ) : null}
        </div>
        <UnitBadge metricMode={metricMode} />
      </div>
      <div className="flex min-h-0 flex-1 flex-col gap-2 p-2.5">{children}</div>
      {footer ? (
        <div className="border-t border-gray-200 bg-gray-50 px-3 py-1.5 text-[10px] font-semibold text-gray-700">
          {footer}
        </div>
      ) : null}
    </section>
  );
}

function CompactTable({ columns, rows, emptyText = 'No data' }) {
  if (!rows?.length) {
    return <div className="py-3 text-center text-[11px] text-gray-400">{emptyText}</div>;
  }
  return (
    <div className="max-h-[168px] overflow-auto rounded border border-gray-100">
      <table className="w-full border-collapse text-[10px]">
        <thead className="sticky top-0 z-[1] bg-gray-50">
          <tr>
            {columns.map((c) => (
              <th
                key={c.key}
                className={`whitespace-nowrap border-b border-gray-200 px-1.5 py-1 font-semibold text-gray-600 ${
                  c.align === 'left' ? 'text-left' : 'text-right'
                }`}
              >
                {c.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, idx) => (
            <tr key={row.key || idx} className={idx % 2 ? 'bg-gray-50/70' : 'bg-white'}>
              {columns.map((c) => (
                <td
                  key={c.key}
                  className={`whitespace-nowrap border-b border-gray-50 px-1.5 py-0.5 text-gray-800 ${
                    c.align === 'left' ? 'text-left' : 'text-right'
                  } ${c.mono ? 'tabular-nums' : ''}`}
                >
                  {c.render ? c.render(row) : row[c.key]}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function KpiCard({ icon: Icon, iconBg, title, value, sub, subTone = 'muted', testId }) {
  const tone =
    subTone === 'up'
      ? 'text-emerald-600'
      : subTone === 'down'
        ? 'text-red-500'
        : subTone === 'warn'
          ? 'text-amber-600'
          : subTone === 'accent'
            ? 'text-[#5a8a05]'
            : 'text-gray-500';
  return (
    <div
      className="flex min-w-0 items-start gap-2.5 rounded-lg border border-gray-200 bg-white px-3 py-2.5 shadow-sm"
      data-testid={testId}
    >
      <div className={`mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-md ${iconBg}`}>
        <Icon className="h-4 w-4 text-white" />
      </div>
      <div className="min-w-0 flex-1">
        <div className="truncate text-[10px] font-semibold uppercase tracking-wide text-gray-500">
          {title}
        </div>
        <div className="truncate text-lg font-bold leading-tight text-gray-900 tabular-nums">
          {value}
        </div>
        {sub ? <div className={`mt-0.5 truncate text-[10px] font-medium ${tone}`}>{sub}</div> : null}
      </div>
    </div>
  );
}

function ChartEmpty() {
  return (
    <div className="flex h-[200px] items-center justify-center text-[11px] text-gray-400">
      No upload data for selected filters
    </div>
  );
}

export function Analytics() {
  const ctx = useOutletContext() || {};
  const {
    scopeBrand,
    scopeDealer,
    scopeBranch,
    setScopeBrand,
    setScopeDealer,
    setScopeBranch,
    brandOptions = [],
    dealerOptions = [],
    branchOptions = [],
  } = ctx;

  const [month, setMonth] = useState(monthValue());
  const [metricMode, setMetricMode] = useState('value');
  const [partType, setPartType] = useState('All');
  const [loading, setLoading] = useState(false);
  const [stockTrend, setStockTrend] = useState(null);
  const [agingTrend, setAgingTrend] = useState(null);
  const [orderSaving, setOrderSaving] = useState(null);
  const [requestAcceptance, setRequestAcceptance] = useState(null);

  const bounds = useMemo(() => monthBounds(month), [month]);
  const isValue = metricMode === 'value';
  const scopeParams = useMemo(
    () => scopeQuery(scopeBrand, scopeDealer, scopeBranch),
    [scopeBrand, scopeDealer, scopeBranch]
  );

  const fmtMetric = useCallback(
    (value) => {
      if (isNullMetric(value)) return 'NO DATA';
      return isValue ? formatLakhs(value) : formatINR(value);
    },
    [isValue]
  );

  // Charts show ₹ Lakhs in Value Wise (sample), raw qty in Item Wise.
  const fmtChart = useCallback(
    (value) => {
      if (isNullMetric(value)) return null;
      const n = Number(value);
      if (isValue) return Number((n / 1e5).toFixed(2));
      return Number(n.toFixed(0));
    },
    [isValue]
  );

  const load = useCallback(async () => {
    // Wait until layout finishes hydrating scope (initial placeholder is "N/A").
    if (!scopeBrand || !scopeDealer || !scopeBranch) return;
    if ([scopeBrand, scopeDealer, scopeBranch].some((v) => String(v) === 'N/A')) return;
    setLoading(true);
    try {
      // Backend contract (same as previous Analytics): metric_type + category + aging_type.
      // Omit All* scope filters so authorized scope is consolidated server-side.
      const common = {
        ...bounds,
        ...scopeParams,
        metric_type: metricMode,
        aging_type: 'purchase',
        ...(partType && partType !== 'All' ? { category: partType } : {}),
      };
      const [st, ag, os, ra] = await Promise.all([
        axios.get(`${API}/analytics/stock-trend`, { params: common }),
        axios.get(`${API}/analytics/aging-trend`, { params: common }),
        axios.get(`${API}/analytics/order-saving`, { params: common }),
        axios.get(`${API}/analytics/request-acceptance`, {
          params: { ...common, request_direction: 'received' },
        }),
      ]);
      setStockTrend(st.data);
      setAgingTrend(ag.data);
      setOrderSaving(os.data);
      setRequestAcceptance(ra.data);
    } catch (err) {
      console.error(err);
      const detail = err?.response?.data?.detail;
      toast.error(typeof detail === 'string' ? detail : 'Failed to load analytics');
    } finally {
      setLoading(false);
    }
  }, [bounds, scopeParams, scopeBrand, scopeDealer, scopeBranch, partType, metricMode]);

  useEffect(() => {
    load();
  }, [load]);

  // —— Daily stock: series[].closing; hide NO_UPLOAD days (sample note) ——
  const stockPoints = useMemo(() => {
    return (stockTrend?.series || [])
      .filter((d) => d.data_status !== 'NO_UPLOAD' && !isNullMetric(d.closing))
      .map((d) => ({
        date: d.date,
        label: dayLabel(d.date),
        value: fmtChart(d.closing),
        raw: d.closing,
        data_status: d.data_status,
      }));
  }, [stockTrend, fmtChart]);

  // —— Aging: daily[] with bucket keys on the row; hide NO_UPLOAD ——
  const agingPoints = useMemo(() => {
    return (agingTrend?.daily || [])
      .filter((d) => d.data_status !== 'NO_UPLOAD')
      .map((d) => {
        const point = {
          date: d.date,
          label: dayLabel(d.date),
          totalRaw: isNullMetric(d.total) ? 0 : Number(d.total),
          data_status: d.data_status,
        };
        AGING_BUCKETS.forEach((b) => {
          const raw = d[b];
          point[b] = isNullMetric(raw) ? 0 : fmtChart(raw);
          point[`${b}__raw`] = raw;
        });
        point.totalLabel = fmtChart(point.totalRaw);
        return point;
      });
  }, [agingTrend, fmtChart]);

  // —— Orders: series + summary (items keys for Item Wise) ——
  const orderPoints = useMemo(() => {
    return (orderSaving?.series || []).map((d) => {
      const original = isValue ? d.original_order_value : d.original_order_items;
      const reduced = isValue ? d.reduced_value : d.reduced_items;
      const finalOrder = isValue ? d.final_order_value : d.final_order_items;
      const finalVal = isNullMetric(finalOrder)
        ? Number(original || 0) - Number(reduced || 0)
        : finalOrder;
      return {
        date: d.date,
        label: dayLabel(d.date),
        original: fmtChart(original) || 0,
        reduced: fmtChart(reduced) || 0,
        final: fmtChart(finalVal) || 0,
        reduction_pct: Number(d.reduction_pct || 0),
        originalRaw: original,
        reducedRaw: reduced,
        finalRaw: finalVal,
      };
    });
  }, [orderSaving, isValue, fmtChart]);

  const orderSummary = useMemo(() => {
    const s = orderSaving?.summary || {};
    const original = isValue ? s.original_order_value : s.original_order_items;
    const reduced = isValue ? s.reduced_value : s.reduced_items;
    const finalOrder = isValue ? s.final_order_value : s.final_order_items;
    const finalVal = isNullMetric(finalOrder)
      ? Number(original || 0) - Number(reduced || 0)
      : finalOrder;
    return {
      original: original || 0,
      reduced: reduced || 0,
      final: finalVal || 0,
      reduction_pct: Number(s.reduction_pct || 0),
    };
  }, [orderSaving, isValue]);

  // —— Requests: series + summary ——
  const requestPoints = useMemo(() => {
    return (requestAcceptance?.series || []).map((d) => {
      const received = isValue ? d.request_received_value : d.request_received_items;
      const branch = isValue ? d.given_to_branches_value : d.given_to_branches_items;
      const dealer = isValue ? d.given_to_dealers_value : d.given_to_dealers_items;
      const fulfilled = isValue ? d.total_fulfilled_value : d.total_fulfilled_items;
      const notFulfilled = isValue ? d.not_fulfilled_value : d.not_fulfilled_items;
      const fulfilledVal = isNullMetric(fulfilled)
        ? Number(branch || 0) + Number(dealer || 0)
        : fulfilled;
      const notVal = isNullMetric(notFulfilled)
        ? Math.max(0, Number(received || 0) - Number(fulfilledVal || 0))
        : notFulfilled;
      return {
        date: d.date,
        label: dayLabel(d.date),
        receivedRaw: received || 0,
        branchRaw: branch || 0,
        dealerRaw: dealer || 0,
        fulfilledRaw: fulfilledVal || 0,
        notFulfilledRaw: notVal || 0,
        fulfillment_pct: Number(d.fulfillment_pct || 0),
      };
    });
  }, [requestAcceptance, isValue]);

  const requestSummary = useMemo(() => {
    const s = requestAcceptance?.summary || {};
    const received = isValue ? s.request_received_value : s.request_received_items;
    const branch = isValue ? s.given_to_branches_value : s.given_to_branches_items;
    const dealer = isValue ? s.given_to_dealers_value : s.given_to_dealers_items;
    const fulfilled = isValue ? s.total_fulfilled_value : s.total_fulfilled_items;
    const notFulfilled = isValue ? s.not_fulfilled_value : s.not_fulfilled_items;
    const fulfilledVal = isNullMetric(fulfilled)
      ? Number(branch || 0) + Number(dealer || 0)
      : fulfilled;
    const notVal = isNullMetric(notFulfilled)
      ? Math.max(0, Number(received || 0) - Number(fulfilledVal || 0))
      : notFulfilled;
    const branchPct = fulfilledVal > 0 ? (Number(branch || 0) / Number(fulfilledVal)) * 100 : 0;
    const dealerPct = fulfilledVal > 0 ? (Number(dealer || 0) / Number(fulfilledVal)) * 100 : 0;
    return {
      received: received || 0,
      branch: branch || 0,
      dealer: dealer || 0,
      fulfilled: fulfilledVal || 0,
      notFulfilled: notVal || 0,
      fulfillment_pct: Number(s.fulfillment_pct || 0),
      branchPct,
      dealerPct,
    };
  }, [requestAcceptance, isValue]);

  const donutData = useMemo(
    () => [
      { name: 'Total Fulfilled', value: Math.max(0, Number(requestSummary.fulfilled) || 0) },
      { name: 'Not Fulfilled', value: Math.max(0, Number(requestSummary.notFulfilled) || 0) },
    ],
    [requestSummary]
  );

  // —— KPI row (live data, sample layout) ——
  const kpis = useMemo(() => {
    const summaryStock = stockTrend?.summary?.current_total;
    const lastStock = stockPoints.length ? stockPoints[stockPoints.length - 1] : null;
    const firstStock = stockPoints.length ? stockPoints[0] : null;
    const stockValue = !isNullMetric(summaryStock)
      ? summaryStock
      : lastStock
        ? lastStock.raw
        : null;
    let stockDelta = null;
    if (lastStock && firstStock && firstStock.raw) {
      stockDelta = ((Number(lastStock.raw) - Number(firstStock.raw)) / Number(firstStock.raw)) * 100;
    } else if (!isNullMetric(stockTrend?.summary?.change_pct)) {
      stockDelta = Number(stockTrend.summary.change_pct);
    }

    const lastAging = agingPoints.length ? agingPoints[agingPoints.length - 1] : null;
    let agingOver180 = null;
    let agingPct = null;
    if (lastAging) {
      const over = AGING_BUCKETS.slice(2).reduce(
        (sum, b) => sum + Number(lastAging[`${b}__raw`] || 0),
        0
      );
      agingOver180 = over;
      agingPct = lastAging.totalRaw > 0 ? (over / lastAging.totalRaw) * 100 : 0;
    }

    return {
      stockValue,
      stockDelta,
      ordersOriginal: orderSummary.original,
      ordersReduced: orderSummary.reduced,
      ordersReductionPct: orderSummary.reduction_pct,
      requestsReceived: requestSummary.received,
      requestsFulfillmentPct: requestSummary.fulfillment_pct,
      agingOver180,
      agingPct,
    };
  }, [stockPoints, agingPoints, orderSummary, requestSummary, stockTrend]);

  const axisTick = { fontSize: 9, fill: '#6b7280' };
  const yTickFormatter = (v) => `${v}`;

  return (
    <div className="analytics-sample mx-auto max-w-[1600px] space-y-2.5" data-testid="analytics-page">
      {/* Compact filter bar — sample pattern */}
      <div
        className="flex flex-wrap items-center gap-2 rounded-lg border border-gray-200 bg-white px-2.5 py-2 shadow-sm"
        data-testid="analytics-filters"
      >
        <select
          className={selectClass}
          value={month}
          onChange={(e) => setMonth(e.target.value)}
          data-testid="analytics-month"
          aria-label="Month"
        >
          {Array.from({ length: 18 }).map((_, i) => {
            const d = new Date(today.getFullYear(), today.getMonth() - i, 1);
            const v = monthValue(d);
            return (
              <option key={v} value={v}>
                {monthLabel(v)}
              </option>
            );
          })}
        </select>

        <select
          className={selectClass}
          value={scopeBrand || ''}
          onChange={(e) => setScopeBrand?.(e.target.value)}
          data-testid="analytics-brand"
          aria-label="Brand"
          disabled={!setScopeBrand}
        >
          {(brandOptions.length ? brandOptions : [scopeBrand || 'All Brands']).map((b) => (
            <option key={b} value={b}>
              {b}
            </option>
          ))}
        </select>

        <select
          className={selectClass}
          value={scopeDealer || ''}
          onChange={(e) => setScopeDealer?.(e.target.value)}
          data-testid="analytics-dealer"
          aria-label="Dealer"
          disabled={!setScopeDealer}
        >
          {(dealerOptions.length ? dealerOptions : [scopeDealer || 'All Dealers']).map((d) => (
            <option key={d} value={d}>
              {d}
            </option>
          ))}
        </select>

        <select
          className={selectClass}
          value={scopeBranch || ''}
          onChange={(e) => setScopeBranch?.(e.target.value)}
          data-testid="analytics-branch"
          aria-label="Branch"
          disabled={!setScopeBranch}
        >
          {(branchOptions.length ? branchOptions : [scopeBranch || 'All Branches']).map((b) => (
            <option key={b} value={b}>
              {b}
            </option>
          ))}
        </select>

        <select
          className={selectClass}
          value={partType}
          onChange={(e) => setPartType(e.target.value)}
          data-testid="analytics-part-type"
          aria-label="Part Type"
        >
          {PART_TYPE_OPTIONS.map((p) => (
            <option key={p} value={p}>
              {p === 'All' ? 'All Part Types' : p}
            </option>
          ))}
        </select>

        <div className="ml-auto">
          <MetricToggle value={metricMode} onChange={setMetricMode} />
        </div>
      </div>

      {/* KPI summary row — sample pattern */}
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 xl:grid-cols-4" data-testid="analytics-kpi-row">
        <KpiCard
          icon={Warehouse}
          iconBg="bg-emerald-500"
          title="Current Stock Value"
          value={loading ? '…' : isValue ? formatINRCompact(kpis.stockValue) : formatINR(kpis.stockValue)}
          sub={
            kpis.stockDelta == null
              ? 'vs month open —'
              : `vs month open ${kpis.stockDelta >= 0 ? '⬆' : '⬇'} ${Math.abs(kpis.stockDelta).toFixed(2)}%`
          }
          subTone={kpis.stockDelta == null ? 'muted' : kpis.stockDelta >= 0 ? 'up' : 'down'}
          testId="analytics-kpi-stock"
        />
        <KpiCard
          icon={ShoppingCart}
          iconBg="bg-blue-500"
          title="Total Orders (This Month)"
          value={loading ? '…' : fmtMetric(kpis.ordersOriginal)}
          sub={`Reduction Achieved ${fmtMetric(kpis.ordersReduced)} (${Number(kpis.ordersReductionPct || 0).toFixed(2)}%)`}
          subTone="accent"
          testId="analytics-kpi-orders"
        />
        <KpiCard
          icon={Package}
          iconBg="bg-violet-500"
          title="Total Requests (This Month)"
          value={loading ? '…' : fmtMetric(kpis.requestsReceived)}
          sub={`Fulfillment % ${Number(kpis.requestsFulfillmentPct || 0).toFixed(2)}%`}
          subTone="accent"
          testId="analytics-kpi-requests"
        />
        <KpiCard
          icon={TrendingDown}
          iconBg="bg-amber-500"
          title="Stock Aging > 180 Days"
          value={loading ? '…' : fmtMetric(kpis.agingOver180)}
          sub={
            kpis.agingPct == null
              ? '% of Total Stock —'
              : `% of Total Stock Value ${Number(kpis.agingPct).toFixed(2)}%`
          }
          subTone="warn"
          testId="analytics-kpi-aging"
        />
      </div>

      {loading && (
        <div className="flex items-center gap-2 text-xs text-gray-500" data-testid="analytics-loading">
          <Loader2 className="h-3.5 w-3.5 animate-spin" /> Refreshing analytics…
        </div>
      )}

      {/* 2×2 analytical sections — sample structure */}
      <div className="grid grid-cols-1 gap-2.5 xl:grid-cols-2" data-testid="analytics-sections-grid">
        {/* 1. Daily Stock Value Trend */}
        <SectionCard
          title="Daily Stock Value Trend"
          hint="Stock value by upload date for the selected month. Days with no upload are not shown."
          metricMode={metricMode}
          testId="analytics-section-stock-trend"
        >
          <div className="h-[210px] w-full">
            {stockPoints.length ? (
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={stockPoints} margin={{ top: 18, right: 12, left: 0, bottom: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb" />
                  <XAxis dataKey="label" tick={axisTick} />
                  <YAxis tick={axisTick} tickFormatter={yTickFormatter} width={36} />
                  <Tooltip
                    formatter={(v) => [isValue ? `₹ ${v} L` : v, isValue ? 'Stock Value' : 'Stock Qty']}
                    labelFormatter={(_, payload) => displayDate(payload?.[0]?.payload?.date)}
                    contentStyle={{ fontSize: 11 }}
                  />
                  <Line
                    type="monotone"
                    dataKey="value"
                    name={isValue ? 'Stock Value' : 'Stock Qty'}
                    stroke="#22c55e"
                    strokeWidth={2.2}
                    dot={{ r: 3, fill: '#22c55e' }}
                    activeDot={{ r: 5 }}
                    connectNulls={false}
                  >
                    <LabelList
                      dataKey="value"
                      position="top"
                      style={{ fontSize: 9, fill: '#374151', fontWeight: 600 }}
                      formatter={(v) => (v == null ? '' : v)}
                    />
                  </Line>
                </LineChart>
              </ResponsiveContainer>
            ) : (
              <ChartEmpty />
            )}
          </div>
          <CompactTable
            columns={[
              { key: 'date', label: 'Date', align: 'left', render: (r) => displayDate(r.date) },
              {
                key: 'value',
                label: isValue ? 'Stock Value' : 'Stock Qty',
                align: 'right',
                mono: true,
                render: (r) => fmtMetric(r.raw),
              },
            ]}
            rows={stockPoints}
          />
          <p className="text-[9px] text-gray-400">Note: Days with no upload are not shown.</p>
        </SectionCard>

        {/* 2. Stock Aging Analysis */}
        <SectionCard
          title="Stock Aging Analysis"
          hint="Daily stacked aging distribution across slabs."
          metricMode={metricMode}
          testId="analytics-section-aging"
        >
          <div className="h-[210px] w-full">
            {agingPoints.length ? (
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={agingPoints} margin={{ top: 18, right: 8, left: 0, bottom: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb" />
                  <XAxis dataKey="label" tick={axisTick} />
                  <YAxis tick={axisTick} tickFormatter={yTickFormatter} width={36} />
                  <Tooltip
                    formatter={(v, name) => [isValue ? `₹ ${v} L` : v, name]}
                    labelFormatter={(_, payload) => displayDate(payload?.[0]?.payload?.date)}
                    contentStyle={{ fontSize: 11 }}
                  />
                  <Legend wrapperStyle={{ fontSize: 10 }} iconSize={8} />
                  {AGING_BUCKETS.map((b, i) => (
                    <Bar key={b} dataKey={b} stackId="aging" fill={AGING_COLORS[i]} maxBarSize={28}>
                      {i === AGING_BUCKETS.length - 1 ? (
                        <LabelList
                          dataKey="totalLabel"
                          position="top"
                          style={{ fontSize: 9, fill: '#374151', fontWeight: 600 }}
                        />
                      ) : null}
                    </Bar>
                  ))}
                </BarChart>
              </ResponsiveContainer>
            ) : (
              <ChartEmpty />
            )}
          </div>
          <CompactTable
            columns={[
              { key: 'date', label: 'Date', align: 'left', render: (r) => displayDate(r.date) },
              ...AGING_BUCKETS.map((b) => ({
                key: b,
                label: b.replace(' Days', ''),
                align: 'right',
                mono: true,
                render: (r) => fmtMetric(r[`${b}__raw`]),
              })),
              {
                key: 'total',
                label: 'Total',
                align: 'right',
                mono: true,
                render: (r) => fmtMetric(r.totalRaw),
              },
            ]}
            rows={agingPoints}
          />
        </SectionCard>

        {/* 3. Orders & Savings Analysis */}
        <SectionCard
          title="Orders & Savings Analysis"
          hint="Final / Net Order = Original Order − Reduced / Cut. NMTS purchase reduction."
          metricMode={metricMode}
          testId="analytics-section-orders"
          footer={
            <div className="flex flex-wrap gap-x-4 gap-y-1">
              <span>
                Total Original: <span className="text-gray-900">{fmtMetric(orderSummary.original)}</span>
              </span>
              <span>
                Total Reduced: <span className="text-gray-900">{fmtMetric(orderSummary.reduced)}</span>
              </span>
              <span>
                Total Final Order:{' '}
                <span className="text-gray-900">{fmtMetric(orderSummary.final)}</span>
              </span>
              <span>
                Avg Reduction:{' '}
                <span className="text-gray-900">{Number(orderSummary.reduction_pct || 0).toFixed(2)}%</span>
              </span>
            </div>
          }
        >
          <div className="h-[210px] w-full">
            {orderPoints.length ? (
              <ResponsiveContainer width="100%" height="100%">
                <ComposedChart data={orderPoints} margin={{ top: 18, right: 28, left: 0, bottom: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb" />
                  <XAxis dataKey="label" tick={axisTick} />
                  <YAxis yAxisId="left" tick={axisTick} tickFormatter={yTickFormatter} width={36} />
                  <YAxis
                    yAxisId="right"
                    orientation="right"
                    tick={axisTick}
                    domain={[0, 100]}
                    tickFormatter={(v) => `${v}%`}
                    width={32}
                  />
                  <Tooltip
                    formatter={(v, name) => {
                      if (name === 'Reduction %') return [`${Number(v).toFixed(2)}%`, name];
                      return [isValue ? `₹ ${v} L` : v, name];
                    }}
                    labelFormatter={(_, payload) => displayDate(payload?.[0]?.payload?.date)}
                    contentStyle={{ fontSize: 11 }}
                  />
                  <Legend wrapperStyle={{ fontSize: 10 }} iconSize={8} />
                  <Bar
                    yAxisId="left"
                    dataKey="original"
                    name="Original Order"
                    fill={ORDER_COLORS.original}
                    maxBarSize={16}
                  />
                  <Bar
                    yAxisId="left"
                    dataKey="reduced"
                    name="Reduced / Cut"
                    fill={ORDER_COLORS.reduced}
                    maxBarSize={16}
                  />
                  <Bar
                    yAxisId="left"
                    dataKey="final"
                    name="Final / Net Order"
                    fill={ORDER_COLORS.final}
                    maxBarSize={16}
                  />
                  <Line
                    yAxisId="right"
                    type="monotone"
                    dataKey="reduction_pct"
                    name="Reduction %"
                    stroke={ORDER_COLORS.reduction_pct}
                    strokeWidth={2}
                    dot={{ r: 3, fill: ORDER_COLORS.reduction_pct }}
                  >
                    <LabelList
                      dataKey="reduction_pct"
                      position="top"
                      style={{ fontSize: 8, fill: '#b45309', fontWeight: 600 }}
                      formatter={(v) => `${Number(v).toFixed(0)}%`}
                    />
                  </Line>
                </ComposedChart>
              </ResponsiveContainer>
            ) : (
              <ChartEmpty />
            )}
          </div>
          <CompactTable
            columns={[
              { key: 'date', label: 'Date', align: 'left', render: (r) => displayDate(r.date) },
              {
                key: 'o',
                label: 'Original',
                align: 'right',
                mono: true,
                render: (r) => fmtMetric(r.originalRaw),
              },
              {
                key: 'r',
                label: 'Reduced',
                align: 'right',
                mono: true,
                render: (r) => fmtMetric(r.reducedRaw),
              },
              {
                key: 'f',
                label: 'Final',
                align: 'right',
                mono: true,
                render: (r) => fmtMetric(r.finalRaw),
              },
              {
                key: 'p',
                label: 'Red. %',
                align: 'right',
                mono: true,
                render: (r) => `${Number(r.reduction_pct || 0).toFixed(2)}%`,
              },
            ]}
            rows={orderPoints}
          />
        </SectionCard>

        {/* 4. Request Fulfillment Analysis */}
        <SectionCard
          title="Request Fulfillment Analysis"
          hint="Total Request = Fulfilled + Not Fulfilled. Fulfilled = Branches + Dealers / Co-Dealers."
          metricMode={metricMode}
          testId="analytics-section-requests"
          footer={
            <div className="flex flex-wrap gap-x-4 gap-y-1">
              <span>
                Request Received:{' '}
                <span className="text-gray-900">{fmtMetric(requestSummary.received)}</span>
              </span>
              <span>
                Given to Branches:{' '}
                <span className="text-gray-900">{fmtMetric(requestSummary.branch)}</span>
              </span>
              <span>
                Given to Dealers:{' '}
                <span className="text-gray-900">{fmtMetric(requestSummary.dealer)}</span>
              </span>
              <span>
                Total Fulfilled:{' '}
                <span className="text-gray-900">{fmtMetric(requestSummary.fulfilled)}</span>
              </span>
              <span>
                Not Fulfilled:{' '}
                <span className="text-gray-900">{fmtMetric(requestSummary.notFulfilled)}</span>
              </span>
              <span>
                Fulfillment %:{' '}
                <span className="text-gray-900">
                  {Number(requestSummary.fulfillment_pct || 0).toFixed(2)}%
                </span>
              </span>
            </div>
          }
        >
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
            <div className="relative h-[180px]">
              {Number(requestSummary.received) > 0 ? (
                <>
                  <ResponsiveContainer width="100%" height="100%">
                    <PieChart>
                      <Pie
                        data={donutData}
                        dataKey="value"
                        nameKey="name"
                        innerRadius="58%"
                        outerRadius="78%"
                        paddingAngle={2}
                        stroke="#fff"
                        strokeWidth={2}
                      >
                        {donutData.map((entry, i) => (
                          <Cell
                            key={entry.name}
                            fill={
                              i === 0 ? FULFILL_COLORS.fulfilled : FULFILL_COLORS.not_fulfilled
                            }
                          />
                        ))}
                      </Pie>
                      <Tooltip
                        formatter={(v, name) => [fmtMetric(v), name]}
                        contentStyle={{ fontSize: 11 }}
                      />
                      <Legend wrapperStyle={{ fontSize: 10 }} iconSize={8} />
                    </PieChart>
                  </ResponsiveContainer>
                  <div className="pointer-events-none absolute inset-0 flex items-center justify-center">
                    <div className="text-center leading-tight">
                      <div className="text-[9px] font-semibold uppercase text-gray-500">
                        Total Request
                      </div>
                      <div className="text-sm font-bold text-gray-900">
                        {fmtMetric(requestSummary.received)}
                      </div>
                    </div>
                  </div>
                </>
              ) : (
                <ChartEmpty />
              )}
            </div>

            <div className="flex flex-col justify-center gap-2 px-1">
              <div className="text-[11px] font-bold text-gray-800">Fulfilled Breakdown</div>
              <div className="space-y-1.5">
                <div className="flex items-center justify-between text-[10px] font-semibold">
                  <span className="text-emerald-700">Given to Branches</span>
                  <span className="tabular-nums text-gray-800">
                    {fmtMetric(requestSummary.branch)} ({requestSummary.branchPct.toFixed(2)}%)
                  </span>
                </div>
                <div className="h-3.5 overflow-hidden rounded bg-gray-100">
                  <div className="flex h-full w-full">
                    <div
                      className="h-full bg-emerald-500"
                      style={{ width: `${Math.min(100, requestSummary.branchPct)}%` }}
                    />
                    <div
                      className="h-full bg-blue-500"
                      style={{ width: `${Math.min(100, requestSummary.dealerPct)}%` }}
                    />
                  </div>
                </div>
                <div className="flex items-center justify-between text-[10px] font-semibold">
                  <span className="text-blue-700">Given to Dealers / Co-Dealers</span>
                  <span className="tabular-nums text-gray-800">
                    {fmtMetric(requestSummary.dealer)} ({requestSummary.dealerPct.toFixed(2)}%)
                  </span>
                </div>
              </div>
            </div>
          </div>

          <CompactTable
            columns={[
              { key: 'date', label: 'Date', align: 'left', render: (r) => displayDate(r.date) },
              {
                key: 'recv',
                label: 'Request',
                align: 'right',
                mono: true,
                render: (r) => fmtMetric(r.receivedRaw),
              },
              {
                key: 'br',
                label: 'Branches',
                align: 'right',
                mono: true,
                render: (r) => fmtMetric(r.branchRaw),
              },
              {
                key: 'dl',
                label: 'Dealers',
                align: 'right',
                mono: true,
                render: (r) => fmtMetric(r.dealerRaw),
              },
              {
                key: 'ful',
                label: 'Fulfilled',
                align: 'right',
                mono: true,
                render: (r) => fmtMetric(r.fulfilledRaw),
              },
              {
                key: 'nf',
                label: 'Not Fulfilled',
                align: 'right',
                mono: true,
                render: (r) => fmtMetric(r.notFulfilledRaw),
              },
              {
                key: 'pct',
                label: 'Fulfill %',
                align: 'right',
                mono: true,
                render: (r) => `${Number(r.fulfillment_pct || 0).toFixed(2)}%`,
              },
            ]}
            rows={requestPoints}
          />
        </SectionCard>
      </div>
    </div>
  );
}
