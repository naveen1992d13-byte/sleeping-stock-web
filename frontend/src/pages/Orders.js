import React, { useEffect, useMemo, useRef, useState } from 'react';
import axios from 'axios';
import * as XLSX from 'xlsx';
import { API, useAuth } from '@/App';
import { useLocation, useOutletContext } from 'react-router-dom';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { FileSpreadsheet, ClipboardPaste, Search, Send, History, RefreshCw, ChevronDown, ChevronUp, Printer, Eraser } from 'lucide-react';
import { toast } from 'sonner';

const emptyRows = [];

function formatNumber(value) {
  const n = Number(value || 0);
  return Number.isFinite(n) ? n.toLocaleString(undefined, { maximumFractionDigits: 2 }) : '0';
}

function normalizeClipboardText(value) {
  return String(value || '')
    .replace(/\u00A0/g, ' ')
    .replace(/[\u2007\u202F]/g, ' ')
    .replace(/[\u2028\u2029]/g, '\n')
    .replace(/\r\n?/g, '\n');
}

function normalizeHeaderText(value) {
  return normalizeClipboardText(value)
    .toLowerCase()
    .replace(/[_-]+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}

function isPasteHeader(line) {
  const normalized = normalizeHeaderText(line);

  return (
    normalized.includes('part number') &&
    (normalized.includes('quantity') || normalized.includes('qty')) &&
    normalized.includes('description') &&
    normalized.includes('value')
  );
}

function cleanNumericText(value) {
  return String(value ?? '')
    .replace(/[₹$€£]/g, '')
    .replace(/,/g, '')
    .trim();
}

function splitPastedRow(line) {
  const original = normalizeClipboardText(line).trimEnd();
  if (!original.trim()) return [];

  // Excel normally copies cells with TAB characters. Keep blank cells so
  // mandatory Part Number / Value validation can report the correct row.
  if (original.includes('\t')) {
    const cells = original.split('\t').map(cell => cell.trim());
    while (cells.length > 4 && cells[cells.length - 1] === '') cells.pop();
    if (cells.length >= 4) {
      return [
        cells[0] ?? '',
        cells[1] ?? '',
        cells.slice(2, -1).join(' ').trim(),
        cells[cells.length - 1] ?? '',
      ];
    }
  }

  const raw = original.trim();

  // CSV fallback. The first two values and the final value are fixed;
  // everything in between belongs to Description.
  if (raw.includes(',')) {
    const cells = raw.split(',').map(cell => cell.trim());
    if (cells.length >= 4) {
      return [
        cells[0] ?? '',
        cells[1] ?? '',
        cells.slice(2, -1).join(' ').trim(),
        cells[cells.length - 1] ?? '',
      ];
    }
  }

  // Plain text fallback: Part Number, Quantity, Description..., Value.
  // This accepts one or many spaces and descriptions such as FRONT BUMPER.
  const tokens = raw.split(/\s+/).filter(Boolean);
  if (tokens.length >= 4) {
    return [
      tokens[0] ?? '',
      tokens[1] ?? '',
      tokens.slice(2, -1).join(' ').trim(),
      tokens[tokens.length - 1] ?? '',
    ];
  }

  return [];
}

function parsePasteText(text) {
  const normalizedText = normalizeClipboardText(text);
  let lines = normalizedText
    .split('\n')
    .map(line => line.trimEnd())
    .filter(line => line.trim().length > 0);

  if (!lines.length) return { rows: [], errors: ['No Excel rows found.'] };

  // Some clipboard/browser combinations place the header and first data row
  // on one visual line. Remove the known header prefix and keep the remainder.
  if (lines.length === 1 && isPasteHeader(lines[0])) {
    const headerPattern = /^\s*part\s*number\s+(?:quantity|qty)\s+description\s+value\s*/i;
    const remainder = lines[0].replace(headerPattern, '').trim();
    lines = remainder ? [remainder] : [];
  } else if (isPasteHeader(lines[0])) {
    lines = lines.slice(1);
  }

  if (!lines.length) {
    return { rows: [], errors: ['No data rows found below the Excel header.'] };
  }

  const rows = [];
  const errors = [];

  lines.forEach((line, index) => {
    const displayRow = index + 2; // Row 1 is normally the Excel header.
    const columns = splitPastedRow(line);

    if (columns.length < 4) {
      errors.push(`Row ${displayRow}: Expected Part Number, Quantity, Description, Value.`);
      return;
    }

    const partNumber = String(columns[0] ?? '').trim();
    const quantityText = cleanNumericText(columns[1]);
    const description = String(columns[2] ?? '').trim();
    const valueText = cleanNumericText(columns[3]);

    const missing = [];
    if (!partNumber) missing.push('Part Number');
    if (!valueText) missing.push('Value');
    if (missing.length) {
      errors.push(`Row ${displayRow}: ${missing.join(' and ')} ${missing.length > 1 ? 'are' : 'is'} required.`);
      return;
    }

    const quantity = Number(quantityText);
    const value = Number(valueText);

    if (!Number.isFinite(quantity) || quantity <= 0) {
      errors.push(`Row ${displayRow}: Quantity must be greater than zero.`);
      return;
    }
    if (!Number.isFinite(value)) {
      errors.push(`Row ${displayRow}: Value must be a valid number.`);
      return;
    }

    rows.push({
      part_number: partNumber,
      quantity,
      description,
      value,
    });
  });

  const uniqueRows = [];
  const seenRows = new Set();
  rows.forEach((row) => {
    const key = [
      String(row.part_number || '').trim().toUpperCase(),
      Number(row.quantity || 0),
      String(row.description || '').trim().toLowerCase(),
      Number(row.value || 0),
    ].join('|');
    if (seenRows.has(key)) return;
    seenRows.add(key);
    uniqueRows.push(row);
  });

  return { rows: uniqueRows, errors };
}

export function Orders() {
  const { user } = useAuth();
  const { scopeBrand = 'All Brands', scopeDealer = 'All Dealers', scopeBranch = 'All Branches' } = useOutletContext() || {};
  const isAllScope = value => !value || String(value).startsWith('All ') || value === 'N/A';
  const scopeReady = !isAllScope(scopeBrand) && !isAllScope(scopeDealer) && !isAllScope(scopeBranch);
  const fileRef = useRef(null);
  const uploadInFlightRef = useRef(false);
  const location = useLocation();
  const [orders, setOrders] = useState([]);
  const [currentOrder, setCurrentOrder] = useState(null);
  const [items, setItems] = useState(emptyRows);
  const [pasteOpen, setPasteOpen] = useState(false);
  const [pasteText, setPasteText] = useState('');
  const [loading, setLoading] = useState(false);
  const [expandedItem, setExpandedItem] = useState('');
  const [sourceMode, setSourceMode] = useState({});
  const [allocations, setAllocations] = useState({});
  const [availabilityFilter, setAvailabilityFilter] = useState('all');
  const [agingType, setAgingType] = useState('purchase'); // 'purchase' | 'sales' — Purchase Aging is mandatory default
  const [agingFilter, setAgingFilter] = useState('0');
  const [sendingRequest, setSendingRequest] = useState(false);
  const [sendRequestResult, setSendRequestResult] = useState(null); // { requestNumbers, emailSent, emailError, duplicate }
  const [resendingNumber, setResendingNumber] = useState('');
  const [autoSuggestLoading, setAutoSuggestLoading] = useState(''); // '' | 'branch' | 'dealer'
  const [suggestedQtyByItem, setSuggestedQtyByItem] = useState({}); // item.id -> qty newly suggested in the last run

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

  useEffect(() => { loadHistory(); }, [scopeBrand, scopeDealer, scopeBranch]);

  const loadOrder = async (orderId, switchToDesk = true) => {
    setLoading(true);
    if (switchToDesk) setSendRequestResult(null);
    try {
      const res = await axios.get(`${API}/order-desk/orders/${orderId}`);
      setCurrentOrder(res.data.order);
      setItems(res.data.items || []);
      const next = {};
      (res.data.items || []).forEach(item => { next[item.id] = item.allocations || []; });
      setAllocations(next);
    } catch (error) {
      toast.error(error.response?.data?.detail || 'Unable to open order');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    const openOrderId = location.state?.openOrderId;
    if (openOrderId) {
      loadOrder(openOrderId, true);
      window.history.replaceState({}, document.title);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [location.state?.openOrderId]);

  const handleUpload = async (event) => {
    const file = event.target.files?.[0];
    event.target.value = '';
    if (!file || uploadInFlightRef.current) return;
    uploadInFlightRef.current = true;
    const form = new FormData();
    form.append('file', file);
    form.append('brand', isAllScope(scopeBrand) ? '' : scopeBrand);
    form.append('dealer', isAllScope(scopeDealer) ? '' : scopeDealer);
    form.append('branch', isAllScope(scopeBranch) ? '' : scopeBranch);
    setLoading(true);
    try {
      const res = await axios.post(`${API}/order-desk/upload`, form, { headers: { 'Content-Type': 'multipart/form-data' } });
      setCurrentOrder(res.data.order);
      setItems(res.data.items || []);
      setAllocations({});
      await loadHistory();
      toast.success(res.data?.duplicate
        ? `Order already created: ${res.data.order.order_number}`
        : `Order created: ${res.data.order.order_number}`);
    } catch (error) {
      toast.error(error.response?.data?.detail || 'Order upload failed');
    } finally {
      uploadInFlightRef.current = false;
      setLoading(false);
    }
  };

  const submitPaste = async () => {
    const { rows, errors } = parsePasteText(pasteText);
    if (errors.length) {
      toast.error(errors[0]);
      return;
    }
    if (!rows.length) {
      toast.error('No valid Excel rows found.');
      return;
    }
    setLoading(true);
    try {
      const res = await axios.post(`${API}/order-desk/paste`, { rows, brand: isAllScope(scopeBrand) ? '' : scopeBrand, dealer: isAllScope(scopeDealer) ? '' : scopeDealer, branch: isAllScope(scopeBranch) ? '' : scopeBranch });
      setCurrentOrder(res.data.order);
      setItems(res.data.items || []);
      setAllocations({});
      setPasteText('');
      setPasteOpen(false);
      setActiveTab('desk');
      await loadHistory();
      toast.success(`Order created: ${res.data.order.order_number}`);
    } catch (error) {
      toast.error(error.response?.data?.detail || 'Unable to create order');
    } finally {
      setLoading(false);
    }
  };

  const checkAvailability = async () => {
    if (!currentOrder?.id) return toast.error('Upload or open an order first');
    if (!scopeReady) return toast.error('Select Brand, Dealer and Branch before Check Availability');
    setLoading(true);
    try {
      const params = new URLSearchParams({ brand: scopeBrand, dealer: scopeDealer, branch: scopeBranch });
      const res = await axios.post(`${API}/order-desk/orders/${currentOrder.id}/check-availability?${params.toString()}`);
      setItems(res.data.items || []);
      setCurrentOrder(prev => ({ ...prev, status: 'Availability Checked', availability_checked: true }));
      toast.success('Availability checked');
      await loadHistory();
    } catch (error) {
      toast.error(error.response?.data?.detail || 'Availability check failed');
    } finally {
      setLoading(false);
    }
  };

  const minimumAgingDays = Number(agingFilter || 0);

  const agingValueOf = (source) => {
    if (agingType === 'sales') {
      return Number(source?.sales_aging_days ?? source?.sales_aging ?? 0);
    }
    // Purchase Aging: prefer the explicit field, then fall back to the
    // legacy single aging_days field for older/unconverted records.
    return Number(source?.purchase_aging_days ?? source?.purchase_aging ?? source?.aging_days ?? 0);
  };

  const sourceMatchesAging = (source) => {
    if (minimumAgingDays <= 0) return true;
    return agingValueOf(source) >= minimumAgingDays;
  };

  const filteredSourcesForMode = (item, mode) => {
    const sources = mode === 'dealer'
      ? (item.other_dealer_sources || [])
      : (item.same_dealer_sources || []);
    return sources.filter(sourceMatchesAging);
  };

  const sourceList = (item) => filteredSourcesForMode(
    item,
    sourceMode[item.id] === 'dealer' ? 'dealer' : 'branch',
  );

  const itemHasFilteredAvailability = (item) => (
    filteredSourcesForMode(item, 'branch').length > 0 ||
    filteredSourcesForMode(item, 'dealer').length > 0
  );

  const filteredItems = useMemo(() => items.filter(item => {
    if (availabilityFilter === 'available') return itemHasFilteredAvailability(item);
    if (availabilityFilter === 'not_available') return !itemHasFilteredAvailability(item);
    return true;
  }), [items, availabilityFilter, agingFilter, agingType]);

  const updateAllocation = (item, source, value) => {
    const requestQty = Math.max(0, Number(value || 0));
    const available = Number(source.available_qty || 0);
    const current = allocations[item.id] || [];
    const key = `${source.dealer_name}__${source.branch}`;
    const without = current.filter(x => `${x.dealer_name}__${x.branch}` !== key);
    const next = requestQty > 0 ? [...without, { ...source, request_qty: Math.min(requestQty, available) }] : without;
    const total = next.reduce((sum, x) => sum + Number(x.request_qty || 0), 0);
    if (total > Number(item.required_qty || 0)) {
      toast.error('Allocated quantity cannot exceed required quantity');
      return;
    }
    setAllocations(prev => ({ ...prev, [item.id]: next }));
  };

  const autoSuggest = (item) => {
    const same = [...filteredSourcesForMode(item, 'branch')].sort((a, b) => agingValueOf(b) - agingValueOf(a));
    const other = [...filteredSourcesForMode(item, 'dealer')].sort((a, b) => agingValueOf(b) - agingValueOf(a));
    const pool = [...same, ...other];
    let remaining = Number(item.required_qty || 0);
    const picked = [];
    for (const source of pool) {
      if (remaining <= 0) break;
      const qty = Math.min(remaining, Number(source.available_qty || 0));
      if (qty > 0) {
        picked.push({ ...source, request_qty: qty });
        remaining -= qty;
      }
    }
    setAllocations(prev => ({ ...prev, [item.id]: picked }));
    toast.success(remaining > 0 ? `Only ${formatNumber(Number(item.required_qty) - remaining)} available` : 'Full quantity suggested');
  };

  const saveSelections = async () => {
    if (!currentOrder?.id) return;
    const payload = {
      allocations: items.map(item => ({ item_id: item.id, sources: allocations[item.id] || [] })),
    };
    setLoading(true);
    try {
      await axios.post(`${API}/order-desk/orders/${currentOrder.id}/allocate`, payload);
      toast.success('Selections saved in the same order');
      await loadOrder(currentOrder.id, false);
      await loadHistory();
    } catch (error) {
      toast.error(error.response?.data?.detail || 'Unable to save selections');
    } finally {
      setLoading(false);
    }
  };

  const runAutoSuggest = async (level) => {
    if (!currentOrder?.id) return toast.error('Upload or open an order first');
    if (!currentOrder?.availability_checked) return toast.error('Run Check Availability first');
    if (autoSuggestLoading) return; // guard against double-click while a run is already in flight
    setAutoSuggestLoading(level);
    try {
      const res = await axios.post(`${API}/order-desk/orders/${currentOrder.id}/auto-suggest`, {
        level, aging_type: agingType,
      });
      const resultItems = res.data.items || [];
      setItems(resultItems);
      const nextAllocations = {};
      const nextSuggested = {};
      resultItems.forEach(item => {
        nextAllocations[item.id] = item.allocations || [];
        nextSuggested[item.id] = Number(item.auto_suggest_new_qty || 0);
      });
      setAllocations(nextAllocations);
      setSuggestedQtyByItem(nextSuggested);
      const manualSkipped = resultItems.filter(i => i.auto_suggest_skipped === 'manual_override').length;
      const suggestedCount = Object.values(nextSuggested).filter(q => q > 0).length;
      toast.success(
        `${level === 'branch' ? 'Branch' : 'Dealer'} Auto Suggest applied to ${suggestedCount} item(s)`
        + (manualSkipped ? ` — ${manualSkipped} manually-selected item(s) kept as-is` : '')
      );
      await loadHistory();
    } catch (error) {
      toast.error(error.response?.data?.detail || 'Auto Suggest failed');
    } finally {
      setAutoSuggestLoading('');
    }
  };

  const sendRequests = async () => {
    if (!currentOrder?.id || sendingRequest) return; // guard against double-clicks / repeated submits
    setSendingRequest(true);
    setSendRequestResult(null);
    try {
      await saveSelections();
      const res = await axios.post(`${API}/order-desk/orders/${currentOrder.id}/send-requests`);
      const data = res.data || {};
      const requestNumbers = data.request_numbers || [];
      const emailSent = !!data.email_sent;
      const emailError = data.email_error || null;

      setSendRequestResult({
        requestNumbers, emailSent, emailError, duplicate: !!data.duplicate,
        message: data.message || 'Requests sent',
      });

      // The request itself always succeeded here (server only reaches this
      // branch when request_created is true) — email outcome is reported
      // separately and never re-labels the whole action as failed.
      if (data.duplicate) {
        toast.success(data.message || 'Request already created for this order');
      } else if (emailSent) {
        toast.success(requestNumbers.length
          ? `Request ${requestNumbers.join(', ')} created and emailed successfully.`
          : 'Request created and emailed successfully.');
      } else {
        toast.success(requestNumbers.length
          ? `Request ${requestNumbers.join(', ')} created successfully, but email could not be sent.`
          : 'Request created successfully, but email could not be sent.');
      }

      // Refresh state after success — loadOrder/loadHistory are read-only
      // GETs, so refreshing never creates another request.
      await loadOrder(currentOrder.id, false);
      await loadHistory();
    } catch (error) {
      toast.error(error.response?.data?.detail || 'Unable to send requests');
    } finally {
      setSendingRequest(false);
    }
  };

  const resendRequestEmail = async (requestNumber) => {
    if (!requestNumber || resendingNumber) return;
    setResendingNumber(requestNumber);
    try {
      const res = await axios.post(`${API}/requests/group/${requestNumber}/resend-email`);
      if (res.data?.email_sent) {
        toast.success(`Email for ${requestNumber} sent successfully.`);
      } else {
        toast.error(res.data?.email_error || `Email for ${requestNumber} could not be sent.`);
      }
    } catch (error) {
      toast.error(error.response?.data?.detail || 'Unable to resend email');
    } finally {
      setResendingNumber('');
    }
  };

  const totals = useMemo(() => {
    const required = items.reduce((s, i) => s + Number(i.required_qty || 0), 0);
    const allocated = items.reduce((s, i) => s + (allocations[i.id] || []).reduce((x, a) => x + Number(a.request_qty || 0), 0), 0);
    return { required, allocated, balance: Math.max(0, required - allocated) };
  }, [items, allocations]);

  const exportTemplate = async () => {
    try {
      const res = await axios.get(`${API}/order-desk/template`, { responseType: 'blob' });
      const disposition = res.headers['content-disposition'] || '';
      const match = disposition.match(/filename=([^;]+)/);
      const filename = (match && match[1].trim().replace(/"/g, '')) || 'Order_Desk_Template.xlsx';
      const url = window.URL.createObjectURL(new Blob([res.data]));
      const link = document.createElement('a');
      link.href = url;
      link.setAttribute('download', filename);
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.URL.revokeObjectURL(url);
    } catch (error) {
      if (error.response?.status === 404) {
        // The brand-wise Order Template hasn't been configured by Master Admin yet.
        // Fall back to a generic 4-column sample so the user is never blocked.
        toast.error('Order Template is not configured for this brand.');
        const ws = XLSX.utils.aoa_to_sheet([
          ['Part Number', 'Quantity', 'Description', 'Value'],
          ['86511B4000', 2, 'FRONT BUMPER', 1500],
        ]);
        const wb = XLSX.utils.book_new();
        XLSX.utils.book_append_sheet(wb, ws, 'Order');
        XLSX.writeFile(wb, 'Order_Desk_Sample_Template.xlsx');
      } else {
        toast.error(error.response?.data?.detail || 'Unable to download template');
      }
    }
  };

  const clearCurrentWorkspace = () => {
    setCurrentOrder(null); setItems([]); setAllocations({}); setExpandedItem(''); setSourceMode({});
    setAvailabilityFilter('all'); setAgingFilter('0'); setSuggestedQtyByItem({}); setSendRequestResult(null);
    setPasteText(''); setPasteOpen(false);
    if (fileRef.current) fileRef.current.value = '';
    toast.success('Current Order Desk workspace cleared');
  };

  const toggleRetrySelection = (itemId) => {
    setItems(prev => prev.map(item => item.id === itemId ? { ...item, retry_selected: !item.retry_selected } : item));
  };
  return (
    <div className="space-y-3" data-testid="orders-page">
      <div className="rounded-xl border bg-white p-3 shadow-sm">
        <div className="flex flex-wrap items-center gap-2">
              <input ref={fileRef} type="file" accept=".xlsx,.xls" className="hidden" onChange={handleUpload} />
              <Button onClick={() => fileRef.current?.click()} disabled={loading}><FileSpreadsheet className="mr-2 h-4 w-4" />Upload Excel</Button>
              <Button variant="outline" onClick={() => setPasteOpen(true)} disabled={loading}><ClipboardPaste className="mr-2 h-4 w-4" />Copy From Excel</Button>
              <Button variant="outline" onClick={exportTemplate}>Download Template</Button>
              <div className="ml-auto text-sm">
                <span className="text-slate-500">Order Number: </span>
                <span className="font-bold text-emerald-700">{currentOrder?.order_number || 'Created automatically after upload'}</span>
              </div>
            </div>
          </div>

      {currentOrder && (
            <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
              {[['Items', currentOrder.item_count], ['Required Qty', totals.required], ['Allocated Qty', totals.allocated], ['Balance Qty', totals.balance], ['Status', currentOrder.status]].map(([label, value]) => (
                <div key={label} className="rounded-xl border bg-white p-3"><div className="text-xs text-slate-500">{label}</div><div className="text-lg font-bold">{typeof value === 'number' ? formatNumber(value) : value}</div></div>
              ))}
            </div>
          )}

          {sendRequestResult && (
            <div className={`rounded-xl border p-4 ${sendRequestResult.emailSent ? 'bg-emerald-50 border-emerald-200' : 'bg-amber-50 border-amber-200'}`}>
              <div className="text-sm font-semibold text-slate-800">
                {sendRequestResult.requestNumbers.length > 0
                  ? `Request Number${sendRequestResult.requestNumbers.length > 1 ? 's' : ''}: ${sendRequestResult.requestNumbers.join(', ')}`
                  : sendRequestResult.message}
              </div>
              <div className="mt-1 text-xs">
                {sendRequestResult.emailSent ? (
                  <span className="text-emerald-700">✓ Email sent to the receiving branch.</span>
                ) : (
                  <span className="text-amber-700">
                    Request created successfully, but email could not be sent{sendRequestResult.emailError ? `: ${sendRequestResult.emailError}` : '.'}
                  </span>
                )}
              </div>
              {!sendRequestResult.emailSent && sendRequestResult.requestNumbers.map((rn) => (
                <Button
                  key={rn}
                  size="sm"
                  variant="outline"
                  className="mt-2 mr-2"
                  disabled={resendingNumber === rn}
                  onClick={() => resendRequestEmail(rn)}
                >
                  {resendingNumber === rn ? 'Resending...' : `Resend Email (${rn})`}
                </Button>
              ))}
            </div>
          )}

          <div className="rounded-xl border bg-white overflow-hidden">
            <div className="flex flex-wrap items-end gap-3 p-4 border-b">
              <Button onClick={checkAvailability} disabled={!currentOrder || loading || !scopeReady} title={!scopeReady ? 'Select Brand, Dealer and Branch first' : 'Check stock availability'}><Search className="mr-2 h-4 w-4" />Check Availability</Button>
              <Button
                variant="outline"
                onClick={() => runAutoSuggest('branch')}
                disabled={!currentOrder || !currentOrder?.availability_checked || loading || !!autoSuggestLoading}
                title="Suggests allocations only from Branch Availability (your own dealer's branches), highest aging first"
              >
                {autoSuggestLoading === 'branch' ? 'Suggesting…' : 'Branch Auto Suggest'}
              </Button>
              <Button
                variant="outline"
                onClick={() => runAutoSuggest('dealer')}
                disabled={!currentOrder || !currentOrder?.availability_checked || loading || !!autoSuggestLoading}
                title="Suggests allocations for the remaining Pending Qty from other dealers — only usable after Branch Requests have been sent"
              >
                {autoSuggestLoading === 'dealer' ? 'Suggesting…' : 'Dealer Auto Suggest'}
              </Button>
              <Button variant="outline" onClick={saveSelections} disabled={!currentOrder || loading}>Save Selection</Button>
              <Button onClick={sendRequests} disabled={!currentOrder || loading || sendingRequest}>
                <Send className="mr-2 h-4 w-4" />{sendingRequest ? 'Sending Request...' : 'Send Requests'}
              </Button>

              <label className="ml-auto min-w-[170px] text-xs font-medium text-slate-600">
                Item Availability
                <select
                  value={availabilityFilter}
                  onChange={event => setAvailabilityFilter(event.target.value)}
                  className="mt-1 h-9 w-full rounded-md border bg-white px-2 text-sm text-slate-800"
                >
                  <option value="all">All Items</option>
                  <option value="available">Available Items</option>
                  <option value="not_available">Not Available Items</option>
                </select>
              </label>

              <label className="min-w-[150px] text-xs font-medium text-slate-600">
                Aging Type
                <select
                  value={agingType}
                  onChange={event => setAgingType(event.target.value)}
                  className="mt-1 h-9 w-full rounded-md border bg-white px-2 text-sm text-slate-800"
                  title="Which aging value the Aging Days filter and Auto Suggest use"
                >
                  <option value="purchase">Purchase Aging</option>
                  <option value="sales">Sales Aging</option>
                </select>
              </label>

              <label className="min-w-[160px] text-xs font-medium text-slate-600">
                Aging Days
                <select
                  value={agingFilter}
                  onChange={event => setAgingFilter(event.target.value)}
                  className="mt-1 h-9 w-full rounded-md border bg-white px-2 text-sm text-slate-800"
                >
                  <option value="0">All Aging</option>
                  <option value="30">30+ Days</option>
                  <option value="60">60+ Days</option>
                  <option value="90">90+ Days</option>
                  <option value="120">120+ Days</option>
                  <option value="180">180+ Days</option>
                  <option value="365">365+ Days</option>
                </select>
              </label>

              <Button variant="outline" onClick={clearCurrentWorkspace} className="h-9 self-end"><Eraser className="mr-2 h-4 w-4" />Clear</Button>

              <Button variant="outline" onClick={() => window.print()} className="h-9 self-end" title="Print current Order Desk results">
                <Printer className="mr-2 h-4 w-4" />Print
              </Button>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-sm min-w-[1100px]">
                <thead className="bg-emerald-50"><tr>{['Part Number','Quantity','Description','Value','Branch Availability','Dealer Availability','Allocated','Balance','Status'].map(h => <th key={h} className="p-3 text-left">{h}</th>)}</tr></thead>
                <tbody>
                  {filteredItems.map(item => {
                    const selected = allocations[item.id] || [];
                    const allocated = selected.reduce((s, a) => s + Number(a.request_qty || 0), 0);
                    const expanded = expandedItem === item.id;
                    const sameCount = filteredSourcesForMode(item, 'branch').length;
                    const dealerCount = filteredSourcesForMode(item, 'dealer').length;
                    const suggestedQty = Number(suggestedQtyByItem[item.id] || 0);
                    return (
                      <React.Fragment key={item.id}>
                        <tr className={`border-t align-top ${item.retry_required ? 'bg-amber-100 ring-1 ring-inset ring-amber-300' : suggestedQty > 0 ? 'bg-emerald-50' : ''}`}>
                          <td className="p-3 font-medium">
                            {item.retry_required && <input className="mr-2" type="checkbox" checked={!!item.retry_selected} onChange={() => toggleRetrySelection(item.id)} title="Select retry item for Auto Suggest" />}
                            {item.part_number}
                            {item.retry_required && <span className="ml-2 rounded bg-amber-500 px-1.5 py-0.5 text-[10px] font-bold text-white">RETRY REQUIRED</span>}
                            {item.allocation_source === 'manual' && <span className="ml-2 rounded bg-slate-100 px-1.5 py-0.5 text-[10px] font-semibold text-slate-500">MANUAL</span>}
                          </td>
                          <td className="p-3">{formatNumber(item.required_qty)}</td>
                          <td className="p-3">{item.description}</td>
                          <td className="p-3">{formatNumber(item.unit_value)}</td>
                          <td className="p-3">
                            <Button size="sm" variant="outline" disabled={!currentOrder?.availability_checked} onClick={() => { setSourceMode(p => ({...p,[item.id]:'branch'})); setExpandedItem(expanded && sourceMode[item.id] === 'branch' ? '' : item.id); }}>
                              {sameCount ? `Available (${sameCount})` : 'Not Available'} {expanded && sourceMode[item.id] !== 'dealer' ? <ChevronUp className="ml-1 h-3 w-3"/> : <ChevronDown className="ml-1 h-3 w-3"/>}
                            </Button>
                          </td>
                          <td className="p-3">
                            <Button size="sm" variant="outline" disabled={!currentOrder?.availability_checked} onClick={() => { setSourceMode(p => ({...p,[item.id]:'dealer'})); setExpandedItem(expanded && sourceMode[item.id] === 'dealer' ? '' : item.id); }}>
                              {dealerCount ? `Available (${dealerCount})` : 'Not Available'} {expanded && sourceMode[item.id] === 'dealer' ? <ChevronUp className="ml-1 h-3 w-3"/> : <ChevronDown className="ml-1 h-3 w-3"/>}
                            </Button>
                          </td>
                          <td className="p-3">
                            {formatNumber(allocated)}
                            {suggestedQty > 0 && <div className="text-[11px] font-medium text-emerald-700">Suggested +{formatNumber(suggestedQty)}</div>}
                          </td>
                          <td className="p-3">{formatNumber(Math.max(0, Number(item.required_qty) - allocated))}</td>
                          <td className="p-3">{item.status || item.availability_status}</td>
                        </tr>
                        {expanded && (
                          <tr className="bg-slate-50 border-t"><td colSpan={9} className="p-4">
                            <div className="flex items-center justify-between mb-3"><div className="font-semibold">{sourceMode[item.id] === 'dealer' ? 'Other Dealer Availability — Same Brand Only' : 'Branch Availability — Same Dealer Only'}</div><Button size="sm" variant="outline" onClick={() => autoSuggest(item)}>Auto Suggest</Button></div>
                            <div className="grid gap-2">
                              {sourceList(item).map(source => {
                                const key = `${source.dealer_name}__${source.branch}`;
                                const value = (selected.find(x => `${x.dealer_name}__${x.branch}` === key)?.request_qty) || '';
                                return <div key={key} className="grid grid-cols-2 md:grid-cols-7 gap-2 items-center rounded-lg border bg-white p-3">
                                  <div><div className="text-xs text-slate-500">Dealer</div>{source.dealer_name}</div>
                                  <div><div className="text-xs text-slate-500">Branch</div>{source.branch}</div>
                                  <div>
                                    <div className="text-xs text-slate-500">Available Qty</div>{formatNumber(source.available_qty)}
                                    {Number(source.reserved_qty || 0) > 0 && (
                                      <div className="text-[11px] text-amber-600">{formatNumber(source.reserved_qty)} reserved · {formatNumber(source.net_available_qty)} free</div>
                                    )}
                                  </div>
                                  <div><div className="text-xs text-slate-500">Purchase Aging Days</div>{source.purchase_aging_days ?? source.aging_days ?? '-'}</div>
                                  <div><div className="text-xs text-slate-500">Sales Aging Days</div>{source.sales_aging_days ?? '-'}</div>
                                  <div><div className="text-xs text-slate-500">Request Qty</div><input type="number" min="0" max={source.available_qty} value={value} onChange={e => updateAllocation(item, source, e.target.value)} className="h-9 w-full rounded border px-2" /></div>
                                  <div className="text-sm font-medium text-emerald-700">{Number(value) > 0 ? `Selected ${value}` : 'Enter quantity'}</div>
                                </div>;
                              })}
                              {!sourceList(item).length && <div className="text-sm text-slate-500">No stock available in this section.</div>}
                            </div>
                          </td></tr>
                        )}
                      </React.Fragment>
                    );
                  })}
                  {!items.length && <tr><td colSpan={9} className="p-10 text-center text-slate-500">Upload Excel or use Copy From Excel to create an order.</td></tr>}
                  {items.length > 0 && !filteredItems.length && <tr><td colSpan={9} className="p-10 text-center text-slate-500">No items match the selected Availability and Aging filters.</td></tr>}
                </tbody>
              </table>
            </div>
          </div>

      <Dialog open={pasteOpen} onOpenChange={setPasteOpen}>
        <DialogContent className="max-w-3xl">
          <DialogHeader><DialogTitle>Copy From Excel</DialogTitle></DialogHeader>
          <p className="text-sm text-slate-500">Copy these four columns from Excel and paste below: Part Number, Quantity, Description, Value.</p>
          <textarea value={pasteText} onChange={e => setPasteText(e.target.value)} rows={12} className="w-full rounded-lg border p-3 font-mono text-sm" placeholder={'Part Number\tQuantity\tDescription\tValue\n86511B4000\t2\tFRONT BUMPER\t1500'} />
          <div className="flex justify-end gap-2"><Button variant="outline" onClick={() => setPasteOpen(false)}>Cancel</Button><Button onClick={submitPaste} disabled={loading}>Create Order</Button></div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
