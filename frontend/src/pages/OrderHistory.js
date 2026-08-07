import React, { useEffect, useState } from 'react';
import { useNavigate, useOutletContext } from 'react-router-dom';
import axios from 'axios';
import { API } from '../App';
import { Button } from '../components/ui/button';
import { RefreshCw } from 'lucide-react';
import { toast } from 'sonner';

const formatNumber = (v) => Number(v || 0).toLocaleString('en-IN');

export function OrderHistory() {
  const navigate = useNavigate();
  const { scopeBrand = 'All Brands', scopeDealer = 'All Dealers', scopeBranch = 'All Branches' } =
    useOutletContext() || {};
  const isAllScope = (value) => !value || String(value).startsWith('All ') || value === 'N/A';
  const [orders, setOrders] = useState([]);

  const loadHistory = async () => {
    try {
      const params = new URLSearchParams();
      if (!isAllScope(scopeBrand)) params.set('brand', scopeBrand);
      if (!isAllScope(scopeDealer)) params.set('dealer', scopeDealer);
      if (!isAllScope(scopeBranch)) params.set('branch', scopeBranch);
      const res = await axios.get(`${API}/order-desk/orders?${params.toString()}`);
      setOrders(res.data || []);
    } catch (error) {
      toast.error(error.response?.data?.detail || 'Unable to load order history');
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
                  <th key={h} className="p-3 text-left font-semibold text-gray-700">
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {orders.map((order) => (
                <tr key={order.id} className="border-t">
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
              ))}
              {!orders.length && (
                <tr>
                  <td colSpan={10} className="p-8 text-center text-slate-500">
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
