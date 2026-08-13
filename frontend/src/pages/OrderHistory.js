import React, { useEffect, useState } from 'react';
import { useNavigate, useOutletContext } from 'react-router-dom';
import axios from 'axios';
import { API } from '../App';
import { Button } from '../components/ui/button';
import { ChevronDown, ChevronRight, RefreshCw } from 'lucide-react';
import { toast } from 'sonner';

const formatNumber = (v) => Number(v || 0).toLocaleString('en-IN');
const dtfmt = (v) => (v ? String(v).slice(0, 16).replace('T', ' ') : '-');

export function OrderHistory() {
  const navigate = useNavigate();
  const { scopeBrand = 'All Brands', scopeDealer = 'All Dealers', scopeBranch = 'All Branches' } =
    useOutletContext() || {};
  const isAllScope = (value) => !value || String(value).startsWith('All ') || value === 'N/A';
  const [orders, setOrders] = useState([]);
  const [expanded, setExpanded] = useState({});
  const [loading, setLoading] = useState(false);

  const loadHistory = async () => {
    setLoading(true);
    try {
      const params = new URLSearchParams();
      if (!isAllScope(scopeBrand)) params.set('brand', scopeBrand);
      if (!isAllScope(scopeDealer)) params.set('dealer', scopeDealer);
      if (!isAllScope(scopeBranch)) params.set('branch', scopeBranch);
      const res = await axios.get(`${API}/order-desk/orders?${params.toString()}`, { timeout: 120000 });
      setOrders(res.data || []);
    } catch (error) {
      toast.error(error.response?.data?.detail || 'Unable to load order history');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadHistory();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scopeBrand, scopeDealer, scopeBranch]);

  const openOrder = (orderId) => {
    navigate('/orders', { state: { openOrderId: orderId } });
  };

  return (
    <div className="space-y-4" data-testid="order-history-page">
      <div className="rounded-xl border bg-white overflow-hidden shadow-sm">
        <div className="flex items-center justify-between p-4 border-b">
          <h2 className="font-semibold text-gray-900">Saved Orders</h2>
          <Button variant="outline" size="sm" onClick={loadHistory}>
            <RefreshCw className="mr-2 h-4 w-4" />
            Refresh
          </Button>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm min-w-[900px]">
            <thead className="bg-emerald-50">
              <tr>
                {[
                  '',
                  'Order No',
                  'Created Date',
                  'Dealer',
                  'Branch',
                  'Items',
                  'Required Qty',
                  'Value',
                  'Status',
                  'Fulfillment',
                  'Action',
                ].map((h) => (
                  <th key={h || 'expand'} className="p-3 text-left font-semibold text-gray-700">
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {orders.map((order) => {
                const open = !!expanded[order.id];
                const lines = order.fulfillment_lines || [];
                return (
                  <React.Fragment key={order.id}>
                    <tr className="border-t">
                      <td className="p-3">
                        <button type="button" onClick={() => setExpanded((p) => ({ ...p, [order.id]: !open }))}>
                          {open ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
                        </button>
                      </td>
                      <td className="p-3 font-semibold text-emerald-700">{order.order_number}</td>
                      <td className="p-3">{String(order.created_at || '').slice(0, 10)}</td>
                      <td className="p-3">{order.dealer_name}</td>
                      <td className="p-3">{order.branch}</td>
                      <td className="p-3">{order.item_count}</td>
                      <td className="p-3">{formatNumber(order.total_required_qty)}</td>
                      <td className="p-3">{formatNumber(order.total_order_value)}</td>
                      <td className="p-3">{order.status}</td>
                      <td className="p-3">
                        {order.overall_status || (order.status === 'Requested' ? 'Requested' : '-')}
                      </td>
                      <td className="p-3">
                        <Button size="sm" variant="outline" onClick={() => openOrder(order.id)}>
                          Open
                        </Button>
                      </td>
                    </tr>
                    {open && (
                      <tr className="bg-slate-50 border-t">
                        <td colSpan={11} className="p-4">
                          <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">
                            Item fulfillment (how each part was sourced)
                          </div>
                          {!lines.length ? (
                            <div className="text-sm text-slate-500">No enrichment available for this legacy order.</div>
                          ) : (
                            <div className="overflow-x-auto rounded-lg border bg-white">
                              <table className="w-full text-xs min-w-[1100px]">
                                <thead className="bg-slate-100">
                                  <tr>
                                    {[
                                      'Part',
                                      'Requested Qty',
                                      'Fulfilled Qty',
                                      'Remaining Qty',
                                      'Source Type',
                                      'Branch / Dealer / Factory',
                                      'Request Number',
                                      'System Order Number',
                                      'Accepted / Fulfilled By',
                                      'Accepted / Fulfilled At',
                                      'Final Sourcing Status',
                                    ].map((h) => (
                                      <th key={h} className="p-2 text-left">{h}</th>
                                    ))}
                                  </tr>
                                </thead>
                                <tbody>
                                  {lines.map((line, idx) => {
                                    const sources = line.sources || [];
                                    if (!sources.length) {
                                      return (
                                        <tr key={`${line.part_number}-${idx}`} className="border-t">
                                          <td className="p-2 font-medium">{line.part_number || '-'}</td>
                                          <td className="p-2">{formatNumber(line.ordered_qty)}</td>
                                          <td className="p-2">{formatNumber(line.fulfilled_qty)}</td>
                                          <td className="p-2">{formatNumber(line.remaining_qty)}</td>
                                          <td className="p-2">-</td>
                                          <td className="p-2">-</td>
                                          <td className="p-2">-</td>
                                          <td className="p-2">{line.system_order_number || '-'}</td>
                                          <td className="p-2">-</td>
                                          <td className="p-2">-</td>
                                          <td className="p-2">{line.request_status || '-'}</td>
                                        </tr>
                                      );
                                    }
                                    return sources.map((src, sidx) => (
                                      <tr key={`${line.part_number}-${idx}-${sidx}`} className="border-t">
                                        <td className="p-2 font-medium">{sidx === 0 ? (line.part_number || '-') : ''}</td>
                                        <td className="p-2">{sidx === 0 ? formatNumber(line.ordered_qty) : ''}</td>
                                        <td className="p-2">{formatNumber(src.accepted_qty ?? line.fulfilled_qty)}</td>
                                        <td className="p-2">{sidx === 0 ? formatNumber(line.remaining_qty) : ''}</td>
                                        <td className="p-2">{src.source_type || '-'}</td>
                                        <td className="p-2">
                                          {src.source_type === 'Factory'
                                            ? 'Factory'
                                            : [src.source_dealer, src.source_branch].filter(Boolean).join(' / ') || '-'}
                                        </td>
                                        <td className="p-2">{src.request_number || '-'}</td>
                                        <td className="p-2">
                                          {src.source_type === 'Factory'
                                            ? (src.system_order_number || line.system_order_number || '-')
                                            : '-'}
                                        </td>
                                        <td className="p-2">{src.accepted_by || '-'}</td>
                                        <td className="p-2">{dtfmt(src.accepted_at)}</td>
                                        <td className="p-2">{src.status || line.request_status || '-'}</td>
                                      </tr>
                                    ));
                                  })}
                                </tbody>
                              </table>
                            </div>
                          )}
                        </td>
                      </tr>
                    )}
                  </React.Fragment>
                );
              })}
              {loading && (
                <tr>
                  <td colSpan={11} className="p-8 text-center text-slate-500">
                    Loading order history…
                  </td>
                </tr>
              )}
              {!loading && !orders.length && (
                <tr>
                  <td colSpan={11} className="p-8 text-center text-slate-500">
                    No saved orders
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

export default OrderHistory;
