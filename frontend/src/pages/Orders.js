import React, { useEffect, useMemo, useRef, useState } from 'react';
import axios from 'axios';
import * as XLSX from 'xlsx';
import { API, useAuth } from '@/App';
import { useLocation, useOutletContext } from 'react-router-dom';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { openOrderDeskPrint } from '@/utils/orderDeskPrint';
import { FileSpreadsheet, ClipboardPaste, Search, Send, ChevronDown, ChevronUp, Printer, Eraser, Download, Plus } from 'lucide-react';
import { toast } from 'sonner';

const emptyRows = [];

const REQUEST_STATUS_OPTIONS = [
  'All',
  'Ready to Send',
  'Request Sent',
  'Accepted',
  'Partially Accepted',
  'Rejected',
  'Cancellation Requested',
  'Cancelled',
  'Completed',
  'Remaining Qty',
  'Factory Order Pending',
  'No Further Stock Available',
];

const CANCELLATION_REASONS = [
  'Wrong Part',
  'Wrong Qty',
  'Duplicate Entry',
  'Purchased Outside',
  'No Longer Required',
  'Other',
];

const STATUS_BADGE_STYLES = {
  'Ready to Send': { bg: '#E5E7EB', fg: '#374151' },
  'Request Sent': { bg: '#DBEAFE', fg: '#1E40AF' },
  Accepted: { bg: '#D1FAE5', fg: '#065F46' },
  'Partially Accepted': { bg: '#FEF3C7', fg: '#92400E' },
  Rejected: { bg: '#FEE2E2', fg: '#991B1B' },
  'Cancellation Requested': { bg: '#FFEDD5', fg: '#9A3412' },
  Cancelled: { bg: '#4B5563', fg: '#F9FAFB' },
  Completed: { bg: '#064E3B', fg: '#ECFDF5' },
  'Remaining Qty': { bg: '#FEF3C7', fg: '#92400E' },
  'No Further Stock Available': { bg: '#E5E7EB', fg: '#111827' },
  'Factory Order Pending': { bg: '#E5E7EB', fg: '#111827' },
};

function formatNumber(value) {
  const n = Number(value || 0);
  return Number.isFinite(n) ? n.toLocaleString(undefined, { maximumFractionDigits: 2 }) : '0';
}

function StatusBadge({ status }) {
  const key = String(status || '');
  const style = STATUS_BADGE_STYLES[key] || { bg: '#F3F4F6', fg: '#374151' };
  return (
    <span
      className="inline-block rounded-full px-2 py-0.5 text-[11px] font-semibold whitespace-nowrap"
      style={{ backgroundColor: style.bg, color: style.fg }}
      title={key}
    >
      {key || '—'}
    </span>
  );
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
    const displayRow = index + 2;
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

function compactRequestedFrom(item, selectedAllocations = []) {
  if (item.requested_from) return item.requested_from;
  const parts = [];
  const history = item.request_history || [];
  history.forEach((row) => {
    const qty = Number(row.requested_qty || 0);
    if (!qty) return;
    if (String(row.source_type || row.level || '').toLowerCase().includes('dealer')) {
      parts.push(`${row.dealer_name || row.source_dealer || '-'} / ${row.branch_name || row.source_branch || '-'} - ${qty}`);
    } else {
      parts.push(`${row.branch_name || row.source_branch || row.source_name || '-'} - ${qty}`);
    }
  });
  (selectedAllocations || []).forEach((alloc) => {
    if (alloc.request_no || alloc.request_number) return;
    const qty = Number(alloc.request_qty || 0);
    if (!qty) return;
    const level = String(alloc.level || alloc.source_type || '').toLowerCase();
    if (level === 'dealer') {
      parts.push(`${alloc.dealer_name || '-'} / ${alloc.branch || '-'} - ${qty}`);
    } else {
      parts.push(`${alloc.branch || '-'} - ${qty}`);
    }
  });
  return parts.join(' | ') || '—';
}

function displayRequestStatus(item) {
  const raw = item.request_status || item.status || item.availability_status || '—';
  if (raw === 'No Further Stock Available' || Number(item.factory_order_qty || 0) > 0 && Number(item.remaining_qty || 0) > 0) {
    if (raw === 'No Further Stock Available') return 'Factory Order Pending';
  }
  return raw;
}

export function Orders() {
  useAuth();
  const { scopeBrand = 'All Brands', scopeDealer = 'All Dealers', scopeBranch = 'All Branches' } = useOutletContext() || {};
  const isAllScope = value => !value || String(value).startsWith('All ') || value === 'N/A';
  const scopeReady = !isAllScope(scopeBrand) && !isAllScope(scopeDealer) && !isAllScope(scopeBranch);
  const fileRef = useRef(null);
  const uploadInFlightRef = useRef(false);
  const location = useLocation();
  const [currentOrder, setCurrentOrder] = useState(null);
  const [items, setItems] = useState(emptyRows);
  const [pasteOpen, setPasteOpen] = useState(false);
  const [pasteText, setPasteText] = useState('');
  const [addItemsOpen, setAddItemsOpen] = useState(false);
  const [addItemsText, setAddItemsText] = useState('');
  const [cancelOpen, setCancelOpen] = useState(false);
  const [cancelItem, setCancelItem] = useState(null);
  const [cancelReason, setCancelReason] = useState('');
  const [cancelRemarks, setCancelRemarks] = useState('');
  const [loading, setLoading] = useState(false);
  const [expandedItem, setExpandedItem] = useState('');
  const [sourceMode, setSourceMode] = useState({});
  const [allocations, setAllocations] = useState({});
  const [availabilityFilter, setAvailabilityFilter] = useState('all');
  const [agingType, setAgingType] = useState('purchase');
  const [agingFilter, setAgingFilter] = useState('0');
  const [partSearch, setPartSearch] = useState('');
  const [requestStatusFilter, setRequestStatusFilter] = useState('All');
  const [sourceTypeFilter, setSourceTypeFilter] = useState('All');
  const [sendingRequest, setSendingRequest] = useState(''); // '' | 'branch' | 'dealer'
  const [sendRequestResult, setSendRequestResult] = useState(null);
  const [resendingNumber, setResendingNumber] = useState('');
  const [autoSuggestLoading, setAutoSuggestLoading] = useState('');
  const [suggestedQtyByItem, setSuggestedQtyByItem] = useState({});
  const [selectedIds, setSelectedIds] = useState({});

  const applyItems = (nextItems) => {
    setItems(nextItems || []);
    const next = {};
    (nextItems || []).forEach(item => { next[item.id] = item.allocations || []; });
    setAllocations(next);
  };

  const loadHistory = async () => {
    try {
      const params = new URLSearchParams();
      if (!isAllScope(scopeBrand)) params.set('brand', scopeBrand);
      if (!isAllScope(scopeDealer)) params.set('dealer', scopeDealer);
      if (!isAllScope(scopeBranch)) params.set('branch', scopeBranch);
      await axios.get(`${API}/order-desk/orders?${params.toString()}`);
    } catch (error) {
      // History page owns the list; Order Desk only needs refresh side-effect.
    }
  };

  const loadOrder = async (orderId, switchToDesk = true) => {
    setLoading(true);
    if (switchToDesk) setSendRequestResult(null);
    try {
      const res = await axios.get(`${API}/order-desk/orders/${orderId}`);
      setCurrentOrder(res.data.order);
      applyItems(res.data.items || []);
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
      applyItems(res.data.items || []);
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
      const res = await axios.post(`${API}/order-desk/paste`, {
        rows,
        brand: isAllScope(scopeBrand) ? '' : scopeBrand,
        dealer: isAllScope(scopeDealer) ? '' : scopeDealer,
        branch: isAllScope(scopeBranch) ? '' : scopeBranch,
      });
      setCurrentOrder(res.data.order);
      applyItems(res.data.items || []);
      setPasteText('');
      setPasteOpen(false);
      await loadHistory();
      toast.success(`Order created: ${res.data.order.order_number}`);
    } catch (error) {
      toast.error(error.response?.data?.detail || 'Unable to create order');
    } finally {
      setLoading(false);
    }
  };

  const submitAddItems = async () => {
    if (!currentOrder?.id) return;
    const { rows, errors } = parsePasteText(addItemsText);
    if (errors.length) return toast.error(errors[0]);
    if (!rows.length) return toast.error('No valid rows to add.');
    setLoading(true);
    try {
      const res = await axios.post(`${API}/order-desk/orders/${currentOrder.id}/add-items`, { rows });
      setCurrentOrder(res.data.order);
      applyItems(res.data.items || []);
      setAddItemsOpen(false);
      setAddItemsText('');
      toast.success(res.data.message || 'Items added');
    } catch (error) {
      toast.error(error.response?.data?.detail || 'Unable to add items');
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
      applyItems(res.data.items || []);
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
    if (availabilityFilter === 'available' && !itemHasFilteredAvailability(item)) return false;
    if (availabilityFilter === 'not_available' && itemHasFilteredAvailability(item)) return false;

    if (partSearch.trim()) {
      const q = partSearch.trim().toLowerCase();
      const hay = `${item.part_number || ''} ${item.description || ''}`.toLowerCase();
      if (!hay.includes(q)) return false;
    }

    if (requestStatusFilter && requestStatusFilter !== 'All') {
      const status = displayRequestStatus(item);
      if (requestStatusFilter === 'Factory Order Pending') {
        if (!(status === 'Factory Order Pending' || status === 'No Further Stock Available' || Number(item.factory_order_qty || 0) > 0)) {
          return false;
        }
      } else if (requestStatusFilter === 'Remaining Qty') {
        if (!(Number(item.remaining_qty || 0) > 0 || status === 'Remaining Qty' || item.retry_required)) return false;
      } else if (status !== requestStatusFilter) {
        return false;
      }
    }

    if (sourceTypeFilter && sourceTypeFilter !== 'All') {
      const summary = String(item.source_type_summary || '');
      const historyTypes = (item.request_history || []).map(h => String(h.source_type || h.level || '').toLowerCase());
      const allocTypes = (allocations[item.id] || item.allocations || []).map(a => String(a.level || a.source_type || '').toLowerCase());
      const allTypes = [...historyTypes, ...allocTypes, summary.toLowerCase()];
      if (sourceTypeFilter === 'Branch') {
        if (!allTypes.some(t => t.includes('branch'))) return false;
      }
      if (sourceTypeFilter === 'Dealer') {
        if (!allTypes.some(t => t.includes('dealer'))) return false;
      }
    }

    return true;
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }), [items, availabilityFilter, agingFilter, agingType, partSearch, requestStatusFilter, sourceTypeFilter, allocations]);

  const updateAllocation = (item, source, value, levelHint) => {
    const requestQty = Math.max(0, Number(value || 0));
    const available = Number((source.net_available_qty ?? source.available_qty) || 0);
    const current = allocations[item.id] || [];
    const key = `${source.dealer_name}__${source.branch}`;
    const without = current.filter(x => `${x.dealer_name}__${x.branch}` !== key);
    const level = levelHint || sourceMode[item.id] || 'branch';
    const next = requestQty > 0
      ? [...without, {
        ...source,
        request_qty: Math.min(requestQty, available || Number(source.available_qty || 0)),
        level,
        source_type: level,
      }]
      : without;
    const accepted = Number(item.accepted_qty || 0);
    const total = next.reduce((sum, x) => sum + Number(x.request_qty || 0), 0);
    if (total + accepted > Number(item.required_qty || 0) + 1e-9) {
      toast.error('Allocated quantity cannot exceed remaining requirement');
      return;
    }
    setAllocations(prev => ({ ...prev, [item.id]: next }));
  };

  const openManualSelect = (mode) => {
    if (!currentOrder?.availability_checked) return toast.error('Run Check Availability first');
    const first = filteredItems[0];
    if (!first) return toast.error('No items to select');
    setSourceMode(p => ({ ...p, [first.id]: mode }));
    setExpandedItem(first.id);
    toast.success(mode === 'dealer' ? 'Dealer selection open — enter qty per dealer source' : 'Branch selection open — enter qty per branch');
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
    if (autoSuggestLoading) return;
    setAutoSuggestLoading(level);
    try {
      const itemIds = Object.keys(selectedIds).filter(id => selectedIds[id]);
      const res = await axios.post(`${API}/order-desk/orders/${currentOrder.id}/auto-suggest`, {
        level,
        aging_type: agingType,
        ...(itemIds.length ? { item_ids: itemIds } : {}),
      });
      const resultItems = res.data.items || [];
      applyItems(resultItems);
      const nextSuggested = {};
      resultItems.forEach(item => {
        nextSuggested[item.id] = Number(item.auto_suggest_new_qty || 0);
      });
      setSuggestedQtyByItem(nextSuggested);
      const manualSkipped = resultItems.filter(i => i.auto_suggest_skipped === 'manual_override').length;
      const suggestedCount = Object.values(nextSuggested).filter(q => q > 0).length;
      toast.success(
        `${level === 'branch' ? 'Branch' : 'Dealer'} Auto Suggest applied to ${suggestedCount} item(s)`
        + (manualSkipped ? ` — ${manualSkipped} manually-selected item(s) kept as-is` : '')
        + '. Review, then click Send Request.',
      );
      await loadHistory();
    } catch (error) {
      toast.error(error.response?.data?.detail || 'Auto Suggest failed');
    } finally {
      setAutoSuggestLoading('');
    }
  };

  const sendRequests = async (level) => {
    if (!currentOrder?.id || sendingRequest) return;
    setSendingRequest(level);
    setSendRequestResult(null);
    try {
      await saveSelections();
      const res = await axios.post(`${API}/order-desk/orders/${currentOrder.id}/send-requests`, { level });
      const data = res.data || {};
      const requestNumbers = data.request_numbers || [];
      const emailSent = !!data.email_sent;
      const emailError = data.email_error || null;

      setSendRequestResult({
        requestNumbers, emailSent, emailError, duplicate: !!data.duplicate,
        message: data.message || 'Requests sent',
        level,
      });

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

      await loadOrder(currentOrder.id, false);
      await loadHistory();
    } catch (error) {
      toast.error(error.response?.data?.detail || 'Unable to send requests');
    } finally {
      setSendingRequest('');
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

  const reEnquire = async (itemIds) => {
    if (!currentOrder?.id) return;
    setLoading(true);
    try {
      const res = await axios.post(`${API}/order-desk/orders/${currentOrder.id}/re-enquire`, {
        item_ids: itemIds || [],
        select: true,
      });
      setCurrentOrder(res.data.order);
      applyItems(res.data.items || []);
      toast.success(res.data.message || 'Marked for re-enquiry — run Auto Suggest, then Send Request manually');
    } catch (error) {
      toast.error(error.response?.data?.detail || 'Unable to re-enquire');
    } finally {
      setLoading(false);
    }
  };

  const submitCancellation = async () => {
    if (!currentOrder?.id || !cancelItem?.id) return;
    if (!cancelReason) return toast.error('Cancellation reason is required');
    if (cancelReason === 'Other' && !cancelRemarks.trim()) return toast.error('Remarks required for Other');
    setLoading(true);
    try {
      const res = await axios.post(
        `${API}/order-desk/orders/${currentOrder.id}/items/${cancelItem.id}/request-cancellation`,
        { reason: cancelReason, remarks: cancelRemarks },
      );
      toast.success(res.data.message || 'Cancellation submitted');
      setCancelOpen(false);
      setCancelItem(null);
      setCancelReason('');
      setCancelRemarks('');
      await loadOrder(currentOrder.id, false);
    } catch (error) {
      toast.error(error.response?.data?.detail || 'Unable to request cancellation');
    } finally {
      setLoading(false);
    }
  };

  const totals = useMemo(() => {
    const required = items.reduce((s, i) => s + Number(i.required_qty || 0), 0);
    const accepted = items.reduce((s, i) => s + Number(i.accepted_qty || 0), 0);
    const remaining = items.reduce((s, i) => s + Number(i.remaining_qty != null ? i.remaining_qty : Math.max(0, Number(i.required_qty || 0) - Number(i.accepted_qty || 0))), 0);
    const allocated = items.reduce((s, i) => s + (allocations[i.id] || []).reduce((x, a) => x + Number(a.request_qty || 0), 0), 0);
    return { required, accepted, remaining, allocated };
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

  const exportOrderExcel = async () => {
    if (!currentOrder?.id) return toast.error('Upload or open an order first');
    try {
      const res = await axios.get(`${API}/order-desk/orders/${currentOrder.id}/export`, { responseType: 'blob' });
      const url = window.URL.createObjectURL(new Blob([res.data]));
      const link = document.createElement('a');
      link.href = url;
      link.setAttribute('download', `Order_Desk_${currentOrder.order_number || currentOrder.id}.xlsx`);
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.URL.revokeObjectURL(url);
      toast.success('Order exported to Excel');
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Order export failed');
    }
  };

  const printOrderDesk = () => {
    if (!currentOrder) return toast.error('Upload or open an order first');
    const enriched = items.map((item) => ({
      ...item,
      selected_sources: allocations[item.id] || item.selected_sources || [],
    }));
    openOrderDeskPrint({ order: currentOrder, items: enriched });
  };

  const clearCurrentWorkspace = () => {
    setCurrentOrder(null); setItems([]); setAllocations({}); setExpandedItem(''); setSourceMode({});
    setAvailabilityFilter('all'); setAgingFilter('0'); setSuggestedQtyByItem({}); setSendRequestResult(null);
    setPasteText(''); setPasteOpen(false); setPartSearch(''); setRequestStatusFilter('All'); setSourceTypeFilter('All');
    setSelectedIds({});
    if (fileRef.current) fileRef.current.value = '';
    toast.success('Current Order Desk workspace cleared');
  };

  const toggleSelect = (itemId) => {
    setSelectedIds(prev => ({ ...prev, [itemId]: !prev[itemId] }));
  };

  const selectedRemainingIds = filteredItems
    .filter(i => selectedIds[i.id] && (Number(i.remaining_qty || 0) > 0 || i.retry_required || ['Rejected', 'Partially Accepted', 'Remaining Qty'].includes(displayRequestStatus(i))))
    .map(i => i.id);

  const dealerStageReady = items.some(i =>
    (i.request_history || []).some(h => String(h.source_type || h.level || '').toLowerCase().includes('branch'))
    || ['Request Sent', 'Accepted', 'Partially Accepted', 'Rejected', 'Remaining Qty'].includes(displayRequestStatus(i))
    || currentOrder?.status === 'Requested'
  );

  return (
    <div className="space-y-3" data-testid="orders-page">
      <div className="rounded-xl border bg-white p-3 shadow-sm">
        <div className="flex flex-wrap items-center gap-2">
          <input ref={fileRef} type="file" accept=".xlsx,.xls" className="hidden" onChange={handleUpload} />
          <Button onClick={() => fileRef.current?.click()} disabled={loading}><FileSpreadsheet className="mr-2 h-4 w-4" />Upload Excel</Button>
          <Button variant="outline" onClick={() => setPasteOpen(true)} disabled={loading}><ClipboardPaste className="mr-2 h-4 w-4" />Copy From Excel</Button>
          <Button variant="outline" onClick={exportTemplate}>Download Template</Button>
          <Button variant="outline" onClick={() => setAddItemsOpen(true)} disabled={!currentOrder || loading}><Plus className="mr-2 h-4 w-4" />Add Items</Button>
          <div className="ml-auto text-sm">
            <span className="text-slate-500">Order Number: </span>
            <span className="font-bold text-emerald-700">{currentOrder?.order_number || 'Created automatically after upload'}</span>
          </div>
        </div>
      </div>

      {currentOrder && (
        <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
          {[['Items', currentOrder.item_count], ['Required Qty', totals.required], ['Accepted Qty', totals.accepted], ['Remaining Qty', totals.remaining], ['Status', currentOrder.status]].map(([label, value]) => (
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
              <span className="text-emerald-700">Email Sent</span>
            ) : (
              <span className="text-amber-700">
                Email Failed{sendRequestResult.emailError ? `: ${sendRequestResult.emailError}` : '.'} Request remains valid.
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
              {resendingNumber === rn ? 'Retrying…' : `Retry Email (${rn})`}
            </Button>
          ))}
        </div>
      )}

      <div className="rounded-xl border bg-white overflow-hidden">
        <div className="flex flex-wrap items-end gap-2 p-3 border-b bg-slate-50/60">
          <Button onClick={checkAvailability} disabled={!currentOrder || loading || !scopeReady} title={!scopeReady ? 'Select Brand, Dealer and Branch first' : 'Check stock availability'}><Search className="mr-2 h-4 w-4" />Check Availability</Button>

          <div className="h-9 border-l mx-1" />
          <Button variant="outline" onClick={() => runAutoSuggest('branch')} disabled={!currentOrder || !currentOrder?.availability_checked || loading || !!autoSuggestLoading}>
            {autoSuggestLoading === 'branch' ? 'Suggesting…' : 'Auto Suggest Branch'}
          </Button>
          <Button variant="outline" onClick={() => openManualSelect('branch')} disabled={!currentOrder || !currentOrder?.availability_checked || loading}>Select Branch Manually</Button>
          <Button onClick={() => sendRequests('branch')} disabled={!currentOrder || loading || !!sendingRequest}>
            <Send className="mr-2 h-4 w-4" />{sendingRequest === 'branch' ? 'Sending…' : 'Send Branch Request'}
          </Button>

          <div className="h-9 border-l mx-1" />
          <Button variant="outline" onClick={() => openManualSelect('dealer')} disabled={!currentOrder || !currentOrder?.availability_checked || loading || !dealerStageReady} title={!dealerStageReady ? 'Complete Branch stage first' : 'Open dealer sources'}>Search Dealers</Button>
          <Button variant="outline" onClick={() => runAutoSuggest('dealer')} disabled={!currentOrder || !currentOrder?.availability_checked || loading || !!autoSuggestLoading || !dealerStageReady} title="Dealer Auto Suggest — never auto-sends">
            {autoSuggestLoading === 'dealer' ? 'Suggesting…' : 'Auto Suggest Dealer'}
          </Button>
          <Button variant="outline" onClick={() => openManualSelect('dealer')} disabled={!currentOrder || !currentOrder?.availability_checked || loading || !dealerStageReady}>Select Dealer Manually</Button>
          <Button onClick={() => sendRequests('dealer')} disabled={!currentOrder || loading || !!sendingRequest || !dealerStageReady}>
            <Send className="mr-2 h-4 w-4" />{sendingRequest === 'dealer' ? 'Sending…' : 'Send Dealer Request'}
          </Button>

          <div className="h-9 border-l mx-1" />
          <Button variant="outline" onClick={() => reEnquire([])} disabled={!currentOrder || loading}>Re-Enquire Remaining Qty</Button>
          <Button variant="outline" onClick={() => reEnquire(selectedRemainingIds)} disabled={!currentOrder || loading || !selectedRemainingIds.length}>Re-Enquire Selected</Button>
          <Button variant="outline" onClick={saveSelections} disabled={!currentOrder || loading}>Save Selection</Button>
        </div>

        <div className="flex flex-wrap items-end gap-3 p-3 border-b">
          <label className="min-w-[160px] text-xs font-medium text-slate-600">
            Part Search
            <input value={partSearch} onChange={e => setPartSearch(e.target.value)} className="mt-1 h-9 w-full rounded-md border bg-white px-2 text-sm" placeholder="Part no / name" />
          </label>
          <label className="min-w-[180px] text-xs font-medium text-slate-600">
            Request Status
            <select value={requestStatusFilter} onChange={e => setRequestStatusFilter(e.target.value)} className="mt-1 h-9 w-full rounded-md border bg-white px-2 text-sm">
              {REQUEST_STATUS_OPTIONS.map(opt => <option key={opt} value={opt}>{opt}</option>)}
            </select>
          </label>
          <label className="min-w-[120px] text-xs font-medium text-slate-600">
            Source Type
            <select value={sourceTypeFilter} onChange={e => setSourceTypeFilter(e.target.value)} className="mt-1 h-9 w-full rounded-md border bg-white px-2 text-sm">
              <option value="All">All</option>
              <option value="Branch">Branch</option>
              <option value="Dealer">Dealer</option>
            </select>
          </label>
          <label className="min-w-[140px] text-xs font-medium text-slate-600">
            Item Availability
            <select value={availabilityFilter} onChange={e => setAvailabilityFilter(e.target.value)} className="mt-1 h-9 w-full rounded-md border bg-white px-2 text-sm">
              <option value="all">All Items</option>
              <option value="available">Available Items</option>
              <option value="not_available">Not Available Items</option>
            </select>
          </label>
          <label className="min-w-[130px] text-xs font-medium text-slate-600">
            Aging Type
            <select value={agingType} onChange={e => setAgingType(e.target.value)} className="mt-1 h-9 w-full rounded-md border bg-white px-2 text-sm">
              <option value="purchase">Purchase Aging</option>
              <option value="sales">Sales Aging</option>
            </select>
          </label>
          <label className="min-w-[130px] text-xs font-medium text-slate-600">
            Aging Days
            <select value={agingFilter} onChange={e => setAgingFilter(e.target.value)} className="mt-1 h-9 w-full rounded-md border bg-white px-2 text-sm">
              <option value="0">All Aging</option>
              <option value="30">30+ Days</option>
              <option value="60">60+ Days</option>
              <option value="90">90+ Days</option>
              <option value="120">120+ Days</option>
              <option value="180">180+ Days</option>
              <option value="365">365+ Days</option>
            </select>
          </label>
          <Button variant="outline" className="h-9" onClick={() => { setPartSearch(''); setRequestStatusFilter('All'); setSourceTypeFilter('All'); setAvailabilityFilter('all'); setAgingFilter('0'); setSelectedIds({}); }}>Clear Filters</Button>
          <Button variant="outline" onClick={clearCurrentWorkspace} className="h-9"><Eraser className="mr-2 h-4 w-4" />Clear</Button>
          <Button variant="outline" onClick={exportOrderExcel} className="h-9" title="Download order details as Excel"><Download className="mr-2 h-4 w-4" />Excel</Button>
          <Button variant="outline" onClick={printOrderDesk} className="h-9" title="Print dedicated Order Desk document"><Printer className="mr-2 h-4 w-4" />Print</Button>
        </div>

        <div className="overflow-x-auto">
          <table className="w-full text-sm min-w-[980px]">
            <thead className="bg-emerald-50">
              <tr>
                {['', 'Part No', 'Part Name', 'Required', 'Allocated', 'Remaining', 'Requested From', 'Status', 'Action'].map(h => (
                  <th key={h || 'sel'} className="p-3 text-left">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {filteredItems.map(item => {
                const selected = allocations[item.id] || [];
                const allocated = Number(item.accepted_qty || 0) + selected.reduce((s, a) => s + Number(a.request_qty || 0), 0);
                const remaining = item.remaining_qty != null
                  ? Number(item.remaining_qty)
                  : Math.max(0, Number(item.required_qty || 0) - Number(item.accepted_qty || 0));
                const expanded = expandedItem === item.id;
                const status = displayRequestStatus(item);
                const suggestedQty = Number(suggestedQtyByItem[item.id] || 0);
                const requestedFrom = compactRequestedFrom(item, selected);
                const sameCount = filteredSourcesForMode(item, 'branch').length;
                const dealerCount = filteredSourcesForMode(item, 'dealer').length;
                return (
                  <React.Fragment key={item.id}>
                    <tr className={`border-t align-top ${item.retry_required || status === 'Remaining Qty' ? 'bg-amber-50' : suggestedQty > 0 ? 'bg-emerald-50/70' : ''}`}>
                      <td className="p-3">
                        <input
                          type="checkbox"
                          checked={!!selectedIds[item.id]}
                          onChange={() => toggleSelect(item.id)}
                          title="Select for Re-Enquire Selected"
                        />
                      </td>
                      <td className="p-3 font-medium">
                        <button type="button" className="text-left hover:underline" onClick={() => setExpandedItem(expanded ? '' : item.id)}>
                          {item.part_number}
                        </button>
                        {item.added_after_order_creation && <div className="text-[10px] text-slate-500">Added after order</div>}
                        {item.factory_order_qty > 0 && <div className="text-[10px] font-semibold text-slate-700">Factory Qty: {formatNumber(item.factory_order_qty)}</div>}
                      </td>
                      <td className="p-3">{item.description}</td>
                      <td className="p-3">{formatNumber(item.required_qty)}</td>
                      <td className="p-3">
                        {formatNumber(item.accepted_qty || allocated)}
                        {suggestedQty > 0 && <div className="text-[11px] font-medium text-emerald-700">Suggested +{formatNumber(suggestedQty)}</div>}
                      </td>
                      <td className="p-3">{formatNumber(remaining)}</td>
                      <td className="p-3 text-xs leading-snug max-w-[240px]">{requestedFrom}</td>
                      <td className="p-3"><StatusBadge status={status} /></td>
                      <td className="p-3">
                        <div className="flex flex-col gap-1">
                          <Button size="sm" variant="outline" disabled={!currentOrder?.availability_checked} onClick={() => { setSourceMode(p => ({ ...p, [item.id]: 'branch' })); setExpandedItem(expanded && sourceMode[item.id] === 'branch' ? '' : item.id); }}>
                            Branches ({sameCount}) {expanded && sourceMode[item.id] !== 'dealer' ? <ChevronUp className="ml-1 h-3 w-3" /> : <ChevronDown className="ml-1 h-3 w-3" />}
                          </Button>
                          <Button size="sm" variant="outline" disabled={!currentOrder?.availability_checked} onClick={() => { setSourceMode(p => ({ ...p, [item.id]: 'dealer' })); setExpandedItem(expanded && sourceMode[item.id] === 'dealer' ? '' : item.id); }}>
                            Dealers ({dealerCount}) {expanded && sourceMode[item.id] === 'dealer' ? <ChevronUp className="ml-1 h-3 w-3" /> : <ChevronDown className="ml-1 h-3 w-3" />}
                          </Button>
                          {(remaining > 0 || item.retry_required) && (
                            <Button size="sm" variant="outline" onClick={() => reEnquire([item.id])}>Re-Enquire Remaining Qty</Button>
                          )}
                          <Button
                            size="sm"
                            variant="outline"
                            className="text-amber-800"
                            disabled={['Cancelled', 'Cancellation Requested'].includes(status)}
                            onClick={() => { setCancelItem(item); setCancelOpen(true); setCancelReason(''); setCancelRemarks(''); }}
                          >
                            Request Cancellation
                          </Button>
                        </div>
                      </td>
                    </tr>
                    {expanded && (
                      <tr className="bg-slate-50 border-t">
                        <td colSpan={9} className="p-4 space-y-5">
                          <div>
                            <div className="flex items-center justify-between mb-3">
                              <div className="font-semibold">
                                A. Existing Availability — {sourceMode[item.id] === 'dealer' ? 'Other Dealer Availability (Same Brand)' : 'Branch Availability (Same Dealer)'}
                              </div>
                            </div>
                            <div className="grid gap-2">
                              {sourceList(item).map(source => {
                                const key = `${source.dealer_name}__${source.branch}`;
                                const value = (selected.find(x => `${x.dealer_name}__${x.branch}` === key)?.request_qty) || '';
                                return (
                                  <div key={key} className="grid grid-cols-2 md:grid-cols-8 gap-2 items-center rounded-lg border bg-white p-3">
                                    <div><div className="text-xs text-slate-500">Dealer</div>{source.dealer_name}</div>
                                    <div><div className="text-xs text-slate-500">Branch</div>{source.branch}</div>
                                    <div>
                                      <div className="text-xs text-slate-500">Available Qty</div>{formatNumber(source.available_qty)}
                                      {Number(source.reserved_qty || 0) > 0 && (
                                        <div className="text-[11px] text-amber-600">{formatNumber(source.reserved_qty)} reserved · {formatNumber(source.net_available_qty)} free</div>
                                      )}
                                    </div>
                                    <div><div className="text-xs text-slate-500">Purchase Aging</div>{source.purchase_aging_days ?? source.aging_days ?? '-'}</div>
                                    <div><div className="text-xs text-slate-500">Sales Aging</div>{source.sales_aging_days ?? '-'}</div>
                                    <div><div className="text-xs text-slate-500">LOC</div>{source.loc || '-'}</div>
                                    <div><div className="text-xs text-slate-500">Part Value</div>{formatNumber(source.unit_value)}</div>
                                    <div>
                                      <div className="text-xs text-slate-500">Request Qty</div>
                                      <input
                                        type="number"
                                        min="0"
                                        max={source.net_available_qty ?? source.available_qty}
                                        value={value}
                                        onChange={e => updateAllocation(item, source, e.target.value, sourceMode[item.id] === 'dealer' ? 'dealer' : 'branch')}
                                        className="h-9 w-full rounded border px-2"
                                      />
                                    </div>
                                  </div>
                                );
                              })}
                              {!sourceList(item).length && <div className="text-sm text-slate-500">No stock available in this section.</div>}
                            </div>
                          </div>

                          <div>
                            <div className="font-semibold mb-3">B. Allocation / Request History</div>
                            <div className="overflow-x-auto rounded-lg border bg-white">
                              <table className="w-full text-xs min-w-[900px]">
                                <thead className="bg-slate-100">
                                  <tr>
                                    {['Source Type', 'Source Name', 'Dealer', 'Branch', 'Requested', 'Accepted', 'Remaining', 'Request No', 'Request Status', 'Email Status', 'Remarks'].map(h => (
                                      <th key={h} className="p-2 text-left">{h}</th>
                                    ))}
                                  </tr>
                                </thead>
                                <tbody>
                                  {(item.request_history || []).map((row, idx) => (
                                    <tr key={`${row.request_id || row.request_no || idx}`} className="border-t">
                                      <td className="p-2">{row.source_type || row.level || '-'}</td>
                                      <td className="p-2">{row.source_name || '-'}</td>
                                      <td className="p-2">{row.dealer_name || row.source_dealer || '-'}</td>
                                      <td className="p-2">{row.branch_name || row.source_branch || '-'}</td>
                                      <td className="p-2">{formatNumber(row.requested_qty)}</td>
                                      <td className="p-2">{formatNumber(row.accepted_qty)}</td>
                                      <td className="p-2">{formatNumber(row.remaining_qty)}</td>
                                      <td className="p-2 font-medium text-emerald-700">{row.request_no || '-'}</td>
                                      <td className="p-2"><StatusBadge status={row.request_status} /></td>
                                      <td className="p-2">{row.email_status || '—'}</td>
                                      <td className="p-2">{row.remarks || '—'}</td>
                                    </tr>
                                  ))}
                                  {!(item.request_history || []).length && (
                                    <tr><td colSpan={11} className="p-4 text-center text-slate-500">No requests sent yet for this part.</td></tr>
                                  )}
                                </tbody>
                              </table>
                            </div>
                          </div>
                        </td>
                      </tr>
                    )}
                  </React.Fragment>
                );
              })}
              {!items.length && <tr><td colSpan={9} className="p-10 text-center text-slate-500">Upload Excel or use Copy From Excel to create an order.</td></tr>}
              {items.length > 0 && !filteredItems.length && <tr><td colSpan={9} className="p-10 text-center text-slate-500">No items match the selected filters.</td></tr>}
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

      <Dialog open={addItemsOpen} onOpenChange={setAddItemsOpen}>
        <DialogContent className="max-w-3xl">
          <DialogHeader><DialogTitle>Add Items to {currentOrder?.order_number || 'Order'}</DialogTitle></DialogHeader>
          <p className="text-sm text-slate-500">New parts stay under the same Order Number and follow the same Branch → Dealer → Factory workflow.</p>
          <textarea value={addItemsText} onChange={e => setAddItemsText(e.target.value)} rows={10} className="w-full rounded-lg border p-3 font-mono text-sm" placeholder={'Part Number\tQuantity\tDescription\tValue'} />
          <div className="flex justify-end gap-2"><Button variant="outline" onClick={() => setAddItemsOpen(false)}>Cancel</Button><Button onClick={submitAddItems} disabled={loading}>Add Items</Button></div>
        </DialogContent>
      </Dialog>

      <Dialog open={cancelOpen} onOpenChange={setCancelOpen}>
        <DialogContent className="max-w-md">
          <DialogHeader><DialogTitle>Request Cancellation</DialogTitle></DialogHeader>
          <p className="text-sm text-slate-500">Part {cancelItem?.part_number} — records are never deleted.</p>
          <label className="block text-xs font-medium text-slate-600 mt-2">
            Reason (required)
            <select value={cancelReason} onChange={e => setCancelReason(e.target.value)} className="mt-1 h-9 w-full rounded-md border px-2 text-sm">
              <option value="">Select reason</option>
              {CANCELLATION_REASONS.map(r => <option key={r} value={r}>{r}</option>)}
            </select>
          </label>
          <label className="block text-xs font-medium text-slate-600 mt-3">
            Remarks {cancelReason === 'Other' ? '(required)' : '(optional)'}
            <textarea value={cancelRemarks} onChange={e => setCancelRemarks(e.target.value)} rows={3} className="mt-1 w-full rounded-md border p-2 text-sm" />
          </label>
          <div className="flex justify-end gap-2 mt-3">
            <Button variant="outline" onClick={() => setCancelOpen(false)}>Close</Button>
            <Button onClick={submitCancellation} disabled={loading}>Submit Cancellation</Button>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
