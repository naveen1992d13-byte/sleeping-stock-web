import React, { useEffect, useState } from 'react';
import { useNavigate, useOutletContext } from 'react-router-dom';
import axios from 'axios';
import { API } from '../App';
import { Button } from '../components/ui/button';
import { ChevronDown, ChevronRight, RefreshCw, FileSpreadsheet, Mail } from 'lucide-react';
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
  const [emailOrderId, setEmailOrderId] = useState('');
  const [emailTo, setEmailTo] = useState('');
  const [emailing, setEmailing] = useState(false);

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

  const downloadFulfillmentExcel = async (order) => {
    try {
      const res = await axios.get(`${API}/order-desk/orders/${order.id}/fulfillment-export`, { responseType: 'blob' });
      const url = window.URL.createObjectURL(new Blob([res.data]));
      const link = document.createElement('a');
      link.href = url;
      link.setAttribute('download', `Order_History_${order.order_number || order.id}.xlsx`);
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.URL.revokeObjectURL(url);
    } catch (error) {
      toast.error(error.response?.data?.detail || 'Unable to download Order History Excel');
    }
  };

  const sendFulfillmentEmail = async (order) => {
    const to = String(emailTo || '').trim();
    if (!to) return toast.error('Enter an email address');
    setEmailing(true);
    try {
      const res = await axios.post(`${API}/order-desk/orders/${order.id}/email-fulfillment`, { to_email: to, dry_run: false });
      if (res.data?.status === 'sent') toast.success(`Order History emailed to ${to}`);
      else toast.error(res.data?.error || 'Email was not sent');
      setEmailOrderId('');
      setEmailTo('');
    } catch (error) {
      toast.error(error.response?.data?.detail || 'Unable to email Order History');
    } finally {
      setEmailing(false);
    }
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
                          <div className="mb-3 flex flex-wrap items-center gap-2">
                            <div className="text-xs font-semibold uppercase tracking-wide text-slate-500">
                              Final fulfillment (source breakup when a part has more than one source)
                            </div>
                            <div className="ml-auto flex flex-wrap gap-2">
                              <Button size="sm" variant="outline" onClick={() => downloadFulfillmentExcel(order)}>
                                <FileSpreadsheet className="mr-1 h-4 w-4" />Download Excel
                              </Button>
                              {String(order.status || order.overall_status || '') === 'Completed' && (
                                <Button size="sm" variant="outline" onClick={() => { setEmailOrderId(order.id); setEmailTo(''); }}>
                                  <Mail className="mr-1 h-4 w-4" />Email
                                </Button>
                              )}
                            </div>
                          </div>
                          {emailOrderId === order.id && (
                            <div className="mb-3 flex flex-wrap items-end gap-2 rounded-lg border bg-white p-3">
                              <label className="text-xs text-slate-600">Send Order History to
                                <input
                                  type="email"
                                  value={emailTo}
                                  onChange={(e) => setEmailTo(e.target.value)}
                                  className="mt-1 h-9 w-64 rounded border px-2 text-sm"
                                  placeholder="name@example.com"
                                />
                              </label>
                              <Button size="sm" disabled={emailing} onClick={() => sendFulfillmentEmail(order)}>
                                {emailing ? 'Sending…' : 'Send Email'}
                              </Button>
                              <Button size="sm" variant="outline" onClick={() => setEmailOrderId('')}>Cancel</Button>
                            </div>
                          )}
                          {!lines.length ? (
                            <div className="text-sm text-slate-500">No enrichment available for this legacy order.</div>
                          ) : (
                            <div className="overflow-x-auto rounded-lg border bg-white">
                              <table className="w-full text-xs min-w-[1200px]">
                                <thead className="bg-slate-100">
                                  <tr>
                                    {[
                                      'Part Number',
                                      'Requested Qty',
                                      'Own Branch Fulfilled Qty',
                                      'Accepted Qty',
                                      'Source Dealer',
                                      'Source Branch',
                                      'Factory Qty',
                                      'Factory Order No',
                                      'Final Status',
                                    ].map((h) => (
                                      <th key={h} className="p-2 text-left">{h}</th>
                                    ))}
                                  </tr>
                                </thead>
                                <tbody>
                                  {lines.map((line, idx) => {
                                    const sources = (line.sources || []).filter((src) => src.source_type !== 'Factory');
                                    if (!sources.length) {
                                      return (
                                        <tr key={`${line.part_number}-${idx}`} className="border-t">
                                          <td className="p-2 font-medium">{line.part_number || '-'}</td>
                                          <td className="p-2">{formatNumber(line.requested_qty ?? line.ordered_qty)}</td>
                                          <td className="p-2">{formatNumber(line.own_branch_fulfilled_qty)}</td>
                                          <td className="p-2">{formatNumber(line.accepted_qty)}</td>
                                          <td className="p-2">{line.source_dealer || '-'}</td>
                                          <td className="p-2">{line.source_branch || '-'}</td>
                                          <td className="p-2">{formatNumber(line.factory_qty)}</td>
                                          <td className="p-2">{line.factory_order_no || line.system_order_number || '-'}</td>
                                          <td className="p-2">{line.final_status || line.request_status || '-'}</td>
                                        </tr>
                                      );
                                    }
                                    return sources.map((src, sidx) => (
                                      <tr key={`${line.part_number}-${idx}-${sidx}`} className="border-t">
                                        <td className="p-2 font-medium">{sidx === 0 ? (line.part_number || '-') : ''}</td>
                                        <td className="p-2">{sidx === 0 ? formatNumber(line.requested_qty ?? line.ordered_qty) : ''}</td>
                                        <td className="p-2">{sidx === 0 ? formatNumber(line.own_branch_fulfilled_qty) : ''}</td>
                                        <td className="p-2">{formatNumber(src.accepted_qty ?? line.accepted_qty)}</td>
                                        <td className="p-2">{src.source_dealer || '-'}</td>
                                        <td className="p-2">{src.source_branch || '-'}</td>
                                        <td className="p-2">{sidx === 0 ? formatNumber(line.factory_qty) : ''}</td>
                                        <td className="p-2">{sidx === 0 ? (line.factory_order_no || line.system_order_number || '-') : ''}</td>
                                        <td className="p-2">{sidx === 0 ? (line.final_status || line.request_status || '-') : src.status}</td>
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
