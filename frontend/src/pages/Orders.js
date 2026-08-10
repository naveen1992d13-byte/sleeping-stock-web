import React, { useEffect, useMemo, useRef, useState } from 'react';
import axios from 'axios';
import * as XLSX from 'xlsx';
import { API, useAuth } from '@/App';
import { useLocation, useOutletContext } from 'react-router-dom';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { openOrderDeskPrint } from '@/utils/orderDeskPrint';
import { FileSpreadsheet, ClipboardPaste, Search, Send, ChevronDown, ChevronUp, Printer, Eraser, Plus, Lock } from 'lucide-react';
import { toast } from 'sonner';

const emptyRows = [];
const PRIMARY_STATUS = ['All', 'To Process', 'Request Sent', 'Accepted', 'Rejected', 'Completed'];
const STAGES = ['branch', 'dealer', 'factory'];
const CANCELLATION_REASONS = [
  'Wrong Part', 'Wrong Qty', 'Duplicate Entry', 'Purchased Outside', 'No Longer Required', 'Other',
];
const STATUS_STYLES = {
  'To Process': { bg: '#E5E7EB', fg: '#374151' },
  'Ready to Send': { bg: '#E5E7EB', fg: '#374151' },
  'Request Sent': { bg: '#DBEAFE', fg: '#1E40AF' },
  'Awaiting Response': { bg: '#DBEAFE', fg: '#1E40AF' },
  Accepted: { bg: '#D1FAE5', fg: '#065F46' },
  'Partially Accepted': { bg: '#FEF3C7', fg: '#92400E' },
  Rejected: { bg: '#FEE2E2', fg: '#991B1B' },
  'Rejected Today': { bg: '#FEE2E2', fg: '#991B1B' },
  'Response Time Expired': { bg: '#FFEDD5', fg: '#9A3412' },
  'Cancelled – No Response': { bg: '#4B5563', fg: '#F9FAFB' },
  Cancelled: { bg: '#4B5563', fg: '#F9FAFB' },
  Completed: { bg: '#064E3B', fg: '#ECFDF5' },
  'No Further Stock Available': { bg: '#E5E7EB', fg: '#111827' },
  'Branch Exhausted': { bg: '#FEF3C7', fg: '#92400E' },
  'Dealer Exhausted': { bg: '#FEF3C7', fg: '#92400E' },
};

function formatNumber(value) {
  const n = Number(value || 0);
  return Number.isFinite(n) ? n.toLocaleString(undefined, { maximumFractionDigits: 2 }) : '0';
}

function StatusBadge({ status }) {
  const key = String(status || '');
  const base = key.split('–')[0].split('-')[0].trim();
  let style = STATUS_STYLES[key];
  if (!style) {
    if (key.startsWith('Awaiting')) style = STATUS_STYLES['Awaiting Response'];
    else if (key.startsWith('Accepted')) style = STATUS_STYLES.Accepted;
    else style = STATUS_STYLES[base] || { bg: '#F3F4F6', fg: '#374151' };
  }
  return (
    <span className="inline-block rounded-full px-2 py-0.5 text-[11px] font-semibold whitespace-nowrap" style={{ backgroundColor: style.bg, color: style.fg }} title={key}>
      {key || '—'}
    </span>
  );
}

function normalizeClipboardText(value) {
  return String(value || '').replace(/\u00A0/g, ' ').replace(/[\u2007\u202F]/g, ' ').replace(/[\u2028\u2029]/g, '\n').replace(/\r\n?/g, '\n');
}
function normalizeHeaderText(value) {
  return normalizeClipboardText(value).toLowerCase().replace(/[_-]+/g, ' ').replace(/\s+/g, ' ').trim();
}
function isPasteHeader(line) {
  const n = normalizeHeaderText(line);
  return n.includes('part number') && (n.includes('quantity') || n.includes('qty')) && n.includes('description') && n.includes('value');
}
function cleanNumericText(value) {
  return String(value ?? '').replace(/[₹$€£]/g, '').replace(/,/g, '').trim();
}
function splitPastedRow(line) {
  const original = normalizeClipboardText(line).trimEnd();
  if (!original.trim()) return [];
  if (original.includes('\t')) {
    const cells = original.split('\t').map(c => c.trim());
    while (cells.length > 4 && cells[cells.length - 1] === '') cells.pop();
    if (cells.length >= 4) return [cells[0] ?? '', cells[1] ?? '', cells.slice(2, -1).join(' ').trim(), cells[cells.length - 1] ?? ''];
  }
  const raw = original.trim();
  if (raw.includes(',')) {
    const cells = raw.split(',').map(c => c.trim());
    if (cells.length >= 4) return [cells[0] ?? '', cells[1] ?? '', cells.slice(2, -1).join(' ').trim(), cells[cells.length - 1] ?? ''];
  }
  const tokens = raw.split(/\s+/).filter(Boolean);
  if (tokens.length >= 4) return [tokens[0] ?? '', tokens[1] ?? '', tokens.slice(2, -1).join(' ').trim(), tokens[tokens.length - 1] ?? ''];
  return [];
}
function parsePasteText(text) {
  const normalizedText = normalizeClipboardText(text);
  let lines = normalizedText.split('\n').map(l => l.trimEnd()).filter(l => l.trim().length > 0);
  if (!lines.length) return { rows: [], errors: ['No Excel rows found.'] };
  if (lines.length === 1 && isPasteHeader(lines[0])) {
    const remainder = lines[0].replace(/^\s*part\s*number\s+(?:quantity|qty)\s+description\s+value\s*/i, '').trim();
    lines = remainder ? [remainder] : [];
  } else if (isPasteHeader(lines[0])) lines = lines.slice(1);
  if (!lines.length) return { rows: [], errors: ['No data rows found below the Excel header.'] };
  const rows = []; const errors = [];
  lines.forEach((line, index) => {
    const displayRow = index + 2;
    const columns = splitPastedRow(line);
    if (columns.length < 4) { errors.push(`Row ${displayRow}: Expected Part Number, Quantity, Description, Value.`); return; }
    const partNumber = String(columns[0] ?? '').trim();
    const quantityText = cleanNumericText(columns[1]);
    const description = String(columns[2] ?? '').trim();
    const valueText = cleanNumericText(columns[3]);
    const missing = [];
    if (!partNumber) missing.push('Part Number');
    if (!valueText) missing.push('Value');
    if (missing.length) { errors.push(`Row ${displayRow}: ${missing.join(' and ')} ${missing.length > 1 ? 'are' : 'is'} required.`); return; }
    const quantity = Number(quantityText); const value = Number(valueText);
    if (!Number.isFinite(quantity) || quantity <= 0) { errors.push(`Row ${displayRow}: Quantity must be greater than zero.`); return; }
    if (!Number.isFinite(value)) { errors.push(`Row ${displayRow}: Value must be a valid number.`); return; }
    rows.push({ part_number: partNumber, quantity, description, value });
  });
  const uniqueRows = []; const seenRows = new Set();
  rows.forEach((row) => {
    const key = [String(row.part_number || '').trim().toUpperCase(), Number(row.quantity || 0), String(row.description || '').trim().toLowerCase(), Number(row.value || 0)].join('|');
    if (seenRows.has(key)) return;
    seenRows.add(key); uniqueRows.push(row);
  });
  return { rows: uniqueRows, errors };
}

function compactRequestedFrom(item, selectedAllocations = []) {
  if (item.requested_from) return item.requested_from;
  const parts = [];
  (item.request_history || []).forEach((row) => {
    const qty = Number(row.requested_qty || 0); if (!qty) return;
    if (String(row.source_type || row.level || '').toLowerCase().includes('dealer')) {
      parts.push(`${row.dealer_name || '-'} / ${row.branch_name || '-'} - ${qty}`);
    } else parts.push(`${row.branch_name || row.source_name || '-'} - ${qty}`);
  });
  (selectedAllocations || []).forEach((alloc) => {
    if (alloc.request_no || alloc.request_number) return;
    const qty = Number(alloc.request_qty || 0); if (!qty) return;
    const level = String(alloc.level || alloc.source_type || '').toLowerCase();
    if (level === 'dealer') parts.push(`${alloc.dealer_name || '-'} / ${alloc.branch || '-'} - ${qty}`);
    else parts.push(`${alloc.branch || '-'} - ${qty}`);
  });
  return parts.join(' | ') || '—';
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
  const [allocations, setAllocations] = useState({});
  const [loading, setLoading] = useState(false);
  const [expandedItem, setExpandedItem] = useState('');
  const [activeStage, setActiveStage] = useState('branch'); // branch | dealer | factory
  const [orderStage, setOrderStage] = useState({ active_stage: 'branch', dealer_stage_status: 'locked', factory_stage_status: 'locked' });

  // Per-stage aging
  const [branchAgingType, setBranchAgingType] = useState('purchase');
  const [branchAgingMin, setBranchAgingMin] = useState('0');
  const [dealerAgingType, setDealerAgingType] = useState('purchase');
  const [dealerAgingMin, setDealerAgingMin] = useState('0');

  const [partSearch, setPartSearch] = useState('');
  const [statusFilter, setStatusFilter] = useState('All');
  const [stageFilter, setStageFilter] = useState('All');

  const [pasteOpen, setPasteOpen] = useState(false);
  const [pasteText, setPasteText] = useState('');
  const [addItemsOpen, setAddItemsOpen] = useState(false);
  const [addItemsText, setAddItemsText] = useState('');
  const [cancelOpen, setCancelOpen] = useState(false);
  const [cancelItem, setCancelItem] = useState(null);
  const [cancelReason, setCancelReason] = useState('');
  const [cancelRemarks, setCancelRemarks] = useState('');

  const [sendingRequest, setSendingRequest] = useState('');
  const [sendRequestResult, setSendRequestResult] = useState(null);
  const [resendingNumber, setResendingNumber] = useState('');
  const [autoSuggestLoading, setAutoSuggestLoading] = useState('');
  const [tick, setTick] = useState(0);

  // Countdown tick for awaiting timers
  useEffect(() => {
    const id = setInterval(() => setTick(t => t + 1), 15000);
    return () => clearInterval(id);
  }, []);

  const agingParams = () => ({
    branch_aging_type: branchAgingType,
    branch_min_aging: Number(branchAgingMin || 0),
    dealer_aging_type: dealerAgingType,
    dealer_min_aging: Number(dealerAgingMin || 0),
  });

  const applyItems = (nextItems, stageMeta) => {
    setItems(nextItems || []);
    const next = {};
    (nextItems || []).forEach(item => { next[item.id] = item.allocations || []; });
    setAllocations(next);
    if (stageMeta) {
      setOrderStage(stageMeta);
      if (STAGES.includes(stageMeta.active_stage)) setActiveStage(stageMeta.active_stage);
    } else if (nextItems?.[0]?.order_active_stage) {
      const meta = {
        active_stage: nextItems[0].order_active_stage,
        branch_stage_status: nextItems[0].order_branch_stage_status,
        dealer_stage_status: nextItems[0].order_dealer_stage_status,
        factory_stage_status: nextItems[0].order_factory_stage_status,
      };
      setOrderStage(meta);
      if (STAGES.includes(meta.active_stage)) setActiveStage(meta.active_stage);
    }
  };

  const loadOrder = async (orderId, switchToDesk = true) => {
    setLoading(true);
    if (switchToDesk) setSendRequestResult(null);
    try {
      const params = new URLSearchParams(agingParams());
      const res = await axios.get(`${API}/order-desk/orders/${orderId}?${params}`);
      setCurrentOrder(res.data.order);
      applyItems(res.data.items || [], res.data.stage);
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

  // Re-enrich when aging changes
  useEffect(() => {
    if (currentOrder?.id) loadOrder(currentOrder.id, false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [branchAgingType, branchAgingMin, dealerAgingType, dealerAgingMin, tick]);

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
      setActiveStage('branch');
      toast.success(res.data?.duplicate ? `Order already created: ${res.data.order.order_number}` : `Order created: ${res.data.order.order_number}`);
    } catch (error) {
      toast.error(error.response?.data?.detail || 'Order upload failed');
    } finally {
      uploadInFlightRef.current = false;
      setLoading(false);
    }
  };

  const submitPaste = async () => {
    const { rows, errors } = parsePasteText(pasteText);
    if (errors.length) return toast.error(errors[0]);
    if (!rows.length) return toast.error('No valid Excel rows found.');
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
      setPasteText(''); setPasteOpen(false); setActiveStage('branch');
      toast.success(`Order created: ${res.data.order.order_number}`);
    } catch (error) {
      toast.error(error.response?.data?.detail || 'Unable to create order');
    } finally { setLoading(false); }
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
      applyItems(res.data.items || [], res.data.stage);
      setAddItemsOpen(false); setAddItemsText('');
      toast.success(res.data.message || 'Items added');
    } catch (error) {
      toast.error(error.response?.data?.detail || 'Unable to add items');
    } finally { setLoading(false); }
  };

  const checkAvailability = async () => {
    if (!currentOrder?.id) return toast.error('Upload or open an order first');
    if (!scopeReady) return toast.error('Select Brand, Dealer and Branch before Check Availability');
    setLoading(true);
    try {
      const params = new URLSearchParams({ brand: scopeBrand, dealer: scopeDealer, branch: scopeBranch });
      await axios.post(`${API}/order-desk/orders/${currentOrder.id}/check-availability?${params}`);
      await loadOrder(currentOrder.id, false);
      toast.success(`${activeStage === 'dealer' ? 'Dealer' : 'Branch'} availability checked`);
    } catch (error) {
      toast.error(error.response?.data?.detail || 'Availability check failed');
    } finally { setLoading(false); }
  };

  const saveSelectionsSilent = async () => {
    if (!currentOrder?.id) return;
    await axios.post(`${API}/order-desk/orders/${currentOrder.id}/allocate`, {
      allocations: items.map(item => ({ item_id: item.id, sources: allocations[item.id] || [] })),
    });
  };

  const runAutoSuggest = async (level) => {
    if (!currentOrder?.id) return toast.error('Upload or open an order first');
    if (!currentOrder?.availability_checked) return toast.error('Run Check Availability first');
    if (autoSuggestLoading) return;
    setAutoSuggestLoading(level);
    try {
      const aging_type = level === 'dealer' ? dealerAgingType : branchAgingType;
      const min_aging_days = Number(level === 'dealer' ? dealerAgingMin : branchAgingMin) || 0;
      const res = await axios.post(`${API}/order-desk/orders/${currentOrder.id}/auto-suggest`, {
        level, aging_type, min_aging_days,
      });
      applyItems(res.data.items || []);
      const suggestedCount = (res.data.items || []).filter(i => Number(i.auto_suggest_new_qty || 0) > 0).length;
      toast.success(`${level === 'branch' ? 'Branch' : 'Dealer'} Auto Suggest applied to ${suggestedCount} item(s). Review, then Send Request.`);
    } catch (error) {
      toast.error(error.response?.data?.detail || 'Auto Suggest failed');
    } finally { setAutoSuggestLoading(''); }
  };

  const sendRequests = async (level) => {
    if (!currentOrder?.id || sendingRequest) return;
    setSendingRequest(level);
    setSendRequestResult(null);
    try {
      await saveSelectionsSilent();
      const res = await axios.post(`${API}/order-desk/orders/${currentOrder.id}/send-requests`, { level });
      const data = res.data || {};
      setSendRequestResult({
        requestNumbers: data.request_numbers || [],
        emailSent: !!data.email_sent,
        emailError: data.email_error || null,
        duplicate: !!data.duplicate,
        message: data.message || 'Requests sent',
        level,
      });
      if (data.email_sent) toast.success('Request created and emailed.');
      else toast.success('Request created. Email pending/failed — use Retry Email if needed.');
      await loadOrder(currentOrder.id, false);
    } catch (error) {
      toast.error(error.response?.data?.detail || 'Unable to send requests');
    } finally { setSendingRequest(''); }
  };

  const resendRequestEmail = async (requestNumber) => {
    if (!requestNumber || resendingNumber) return;
    setResendingNumber(requestNumber);
    try {
      const res = await axios.post(`${API}/requests/group/${requestNumber}/resend-email`);
      if (res.data?.email_sent) toast.success(`Email for ${requestNumber} sent.`);
      else toast.error(res.data?.email_error || 'Email could not be sent.');
    } catch (error) {
      toast.error(error.response?.data?.detail || 'Unable to resend email');
    } finally { setResendingNumber(''); }
  };

  const cancelTimeout = async (requestNumber) => {
    if (!requestNumber) return;
    setLoading(true);
    try {
      const res = await axios.post(`${API}/requests/group/${requestNumber}/cancel-timeout`, {});
      toast.success(res.data.message || 'Cancelled – No Response');
      await loadOrder(currentOrder.id, false);
    } catch (error) {
      toast.error(error.response?.data?.detail || 'Unable to cancel');
    } finally { setLoading(false); }
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
      setCancelOpen(false); setCancelItem(null); setCancelReason(''); setCancelRemarks('');
      await loadOrder(currentOrder.id, false);
    } catch (error) {
      toast.error(error.response?.data?.detail || 'Unable to request cancellation');
    } finally { setLoading(false); }
  };

  const updateAllocation = (item, source, value, level) => {
    if (item.qty_locked && Number(item.remaining_qty || 0) <= 0) {
      return toast.error(item.lock_reason || 'Quantity is locked');
    }
    if (source.source_frozen_today || source.rejected_today) {
      return toast.error('Rejected Today — this source is frozen for this Part');
    }
    if (source.selection_disabled) {
      return toast.error('This source is not selectable');
    }
    const requestQty = Math.max(0, Number(value || 0));
    const available = Number((source.net_available_qty ?? source.available_qty) || 0);
    const current = allocations[item.id] || [];
    const key = `${source.dealer_name}__${source.branch}`;
    // Preserve locked/sent allocations
    const preserved = current.filter(x => x.request_no || x.request_number || x.locked);
    const drafts = current.filter(x => !(x.request_no || x.request_number || x.locked) && `${x.dealer_name}__${x.branch}` !== key);
    const nextDraft = requestQty > 0
      ? [...drafts, { ...source, request_qty: Math.min(requestQty, available || Number(source.available_qty || 0)), level, source_type: level }]
      : drafts;
    const next = [...preserved, ...nextDraft];
    const draftTotal = nextDraft.reduce((s, x) => s + Number(x.request_qty || 0), 0);
    if (draftTotal > Number(item.remaining_qty != null ? item.remaining_qty : item.required_qty || 0) + 1e-9) {
      return toast.error('Cannot exceed remaining quantity');
    }
    setAllocations(prev => ({ ...prev, [item.id]: next }));
  };

  const filteredItems = useMemo(() => items.filter(item => {
    if (partSearch.trim()) {
      const q = partSearch.trim().toLowerCase();
      if (!`${item.part_number || ''} ${item.description || ''}`.toLowerCase().includes(q)) return false;
    }
    if (statusFilter !== 'All') {
      const fs = item.filter_status || item.request_status || '';
      if (statusFilter === 'Request Sent' && !['Request Sent', 'Awaiting Response', 'Response Time Expired'].includes(item.request_status) && fs !== 'Request Sent') return false;
      else if (statusFilter === 'Accepted' && !['Accepted', 'Partially Accepted'].includes(item.request_status) && fs !== 'Accepted' && !(item.display_status || '').startsWith('Accepted')) return false;
      else if (statusFilter === 'Rejected' && !['Rejected', 'Rejected Today'].includes(item.request_status) && fs !== 'Rejected') return false;
      else if (statusFilter === 'Completed' && !['Completed', 'Cancelled', 'Cancelled – No Response'].includes(item.request_status) && fs !== 'Completed') return false;
      else if (statusFilter === 'To Process' && fs !== 'To Process' && !['To Process', 'Ready to Send', 'No Further Stock Available'].includes(item.request_status)) return false;
      else if (!['Request Sent', 'Accepted', 'Rejected', 'Completed', 'To Process'].includes(statusFilter)) { /* noop */ }
      else if (statusFilter !== 'All' && statusFilter === 'Request Sent' && fs !== 'Request Sent' && item.request_status !== 'Awaiting Response' && item.request_status !== 'Response Time Expired') {
        // already handled
      }
    }
    if (stageFilter === 'Branch' && item.active_stage !== 'branch' && item.branch_stage_status !== 'open') return false;
    if (stageFilter === 'Dealer' && item.active_stage !== 'dealer' && item.dealer_stage_status !== 'open') return false;
    if (stageFilter === 'Factory' && item.active_stage !== 'factory' && item.factory_stage_status !== 'open') return false;
    return true;
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }), [items, partSearch, statusFilter, stageFilter]);

  const totals = useMemo(() => ({
    required: items.reduce((s, i) => s + Number(i.required_qty || 0), 0),
    accepted: items.reduce((s, i) => s + Number(i.accepted_qty || 0), 0),
    remaining: items.reduce((s, i) => s + Number(i.remaining_qty != null ? i.remaining_qty : Math.max(0, Number(i.required_qty || 0) - Number(i.accepted_qty || 0))), 0),
  }), [items]);

  const dealerUnlocked = orderStage.dealer_stage_status === 'open' || orderStage.active_stage === 'dealer' || orderStage.active_stage === 'factory' || orderStage.active_stage === 'complete';
  const factoryUnlocked = orderStage.factory_stage_status === 'open' || orderStage.active_stage === 'factory';

  const trySetStage = (stage) => {
    if (stage === 'dealer' && !dealerUnlocked) return toast.error('Dealer stage opens only after Branch sources are exhausted');
    if (stage === 'factory' && !factoryUnlocked) return toast.error('Factory stage opens only after Branch + Dealer are exhausted');
    setActiveStage(stage);
  };

  const exportTemplate = async () => {
    try {
      const res = await axios.get(`${API}/order-desk/template`, { responseType: 'blob' });
      const url = window.URL.createObjectURL(new Blob([res.data]));
      const link = document.createElement('a');
      link.href = url; link.setAttribute('download', 'Order_Desk_Template.xlsx');
      document.body.appendChild(link); link.click(); link.remove(); window.URL.revokeObjectURL(url);
    } catch {
      const ws = XLSX.utils.aoa_to_sheet([['Part Number', 'Quantity', 'Description', 'Value'], ['86511B4000', 2, 'FRONT BUMPER', 1500]]);
      const wb = XLSX.utils.book_new(); XLSX.utils.book_append_sheet(wb, ws, 'Order');
      XLSX.writeFile(wb, 'Order_Desk_Sample_Template.xlsx');
    }
  };

  const clearCurrentWorkspace = () => {
    setCurrentOrder(null); setItems([]); setAllocations({}); setExpandedItem('');
    setSendRequestResult(null); setPartSearch(''); setStatusFilter('All'); setStageFilter('All');
    setActiveStage('branch');
    if (fileRef.current) fileRef.current.value = '';
    toast.success('Workspace cleared');
  };

  const stageSources = (item) => {
    const list = activeStage === 'dealer' ? (item.other_dealer_sources || []) : (item.same_dealer_sources || []);
    const minDays = Number(activeStage === 'dealer' ? dealerAgingMin : branchAgingMin) || 0;
    const agingType = activeStage === 'dealer' ? dealerAgingType : branchAgingType;
    return list.filter(s => {
      if (minDays <= 0) return true;
      const days = agingType === 'sales'
        ? Number(s.sales_aging_days ?? 0)
        : Number(s.purchase_aging_days ?? s.aging_days ?? 0);
      return days >= minDays;
    });
  };

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
            <span className="font-bold text-emerald-700">{currentOrder?.order_number || 'Created after upload'}</span>
          </div>
        </div>
      </div>

      {currentOrder && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          {[['Items', currentOrder.item_count], ['Required', totals.required], ['Accepted', totals.accepted], ['Remaining', totals.remaining]].map(([label, value]) => (
            <div key={label} className="rounded-xl border bg-white p-3"><div className="text-xs text-slate-500">{label}</div><div className="text-lg font-bold">{typeof value === 'number' ? formatNumber(value) : value}</div></div>
          ))}
        </div>
      )}

      {/* Compact status filter card */}
      <div className="rounded-xl border bg-white p-3 shadow-sm">
        <div className="flex flex-wrap items-end gap-3">
          <label className="min-w-[160px] text-xs font-medium text-slate-600">Part Search
            <input value={partSearch} onChange={e => setPartSearch(e.target.value)} className="mt-1 h-9 w-full rounded-md border px-2 text-sm" placeholder="Part no / name" />
          </label>
          <label className="min-w-[150px] text-xs font-medium text-slate-600">Status
            <select value={statusFilter} onChange={e => setStatusFilter(e.target.value)} className="mt-1 h-9 w-full rounded-md border px-2 text-sm">
              {PRIMARY_STATUS.map(s => <option key={s} value={s}>{s}</option>)}
            </select>
          </label>
          <label className="min-w-[120px] text-xs font-medium text-slate-600">Stage
            <select value={stageFilter} onChange={e => setStageFilter(e.target.value)} className="mt-1 h-9 w-full rounded-md border px-2 text-sm">
              <option value="All">All</option>
              <option value="Branch">Branch</option>
              <option value="Dealer">Dealer</option>
              <option value="Factory">Factory</option>
            </select>
          </label>
          <Button variant="outline" className="h-9" onClick={() => { setPartSearch(''); setStatusFilter('All'); setStageFilter('All'); }}>Clear Filters</Button>
          <Button variant="outline" className="h-9" onClick={clearCurrentWorkspace}><Eraser className="mr-2 h-4 w-4" />Clear</Button>
          <Button variant="outline" className="h-9" disabled={!currentOrder} onClick={() => currentOrder && openOrderDeskPrint({ order: currentOrder, items: items.map(i => ({ ...i, selected_sources: allocations[i.id] || [] })) })}><Printer className="mr-2 h-4 w-4" />Print</Button>
        </div>
      </div>

      {sendRequestResult && (
        <div className={`rounded-xl border p-4 ${sendRequestResult.emailSent ? 'bg-emerald-50 border-emerald-200' : 'bg-amber-50 border-amber-200'}`}>
          <div className="text-sm font-semibold">{sendRequestResult.requestNumbers.length ? `Request: ${sendRequestResult.requestNumbers.join(', ')}` : sendRequestResult.message}</div>
          <div className="mt-1 text-xs">{sendRequestResult.emailSent ? 'Email Sent' : `Email Failed${sendRequestResult.emailError ? `: ${sendRequestResult.emailError}` : ''}. Request remains valid.`}</div>
          {!sendRequestResult.emailSent && sendRequestResult.requestNumbers.map(rn => (
            <Button key={rn} size="sm" variant="outline" className="mt-2 mr-2" disabled={resendingNumber === rn} onClick={() => resendRequestEmail(rn)}>
              {resendingNumber === rn ? 'Retrying…' : `Retry Email (${rn})`}
            </Button>
          ))}
        </div>
      )}

      {/* Stage tabs */}
      <div className="rounded-xl border bg-white overflow-hidden">
        <div className="flex flex-wrap gap-2 p-3 border-b bg-slate-50">
          {[
            ['branch', 'STEP 1: BRANCH ALLOCATION', true],
            ['dealer', 'STEP 2: DEALER ALLOCATION', dealerUnlocked],
            ['factory', 'STEP 3: FACTORY / OTHER SOURCE', factoryUnlocked],
          ].map(([key, label, unlocked]) => (
            <button
              key={key}
              type="button"
              onClick={() => trySetStage(key)}
              className={`rounded-lg px-3 py-2 text-sm font-semibold border ${activeStage === key ? 'bg-emerald-700 text-white border-emerald-700' : unlocked ? 'bg-white text-slate-700' : 'bg-slate-100 text-slate-400 cursor-not-allowed'}`}
              title={!unlocked ? 'Locked until prior stage is exhausted' : ''}
            >
              {!unlocked && <Lock className="inline h-3 w-3 mr-1" />}{label}
            </button>
          ))}
        </div>

        {/* Stage-specific controls */}
        {activeStage === 'branch' && (
          <div className="flex flex-wrap items-end gap-2 p-3 border-b">
            <label className="min-w-[130px] text-xs font-medium text-slate-600">Branch Aging Type
              <select value={branchAgingType} onChange={e => setBranchAgingType(e.target.value)} className="mt-1 h-9 w-full rounded-md border px-2 text-sm">
                <option value="purchase">Purchase Aging</option>
                <option value="sales">Sales Aging</option>
              </select>
            </label>
            <label className="min-w-[130px] text-xs font-medium text-slate-600">Branch Aging Cutoff
              <select value={branchAgingMin} onChange={e => setBranchAgingMin(e.target.value)} className="mt-1 h-9 w-full rounded-md border px-2 text-sm">
                <option value="0">All Aging</option>
                <option value="30">30+ Days</option>
                <option value="60">60+ Days</option>
                <option value="90">90+ Days</option>
                <option value="120">120+ Days</option>
                <option value="180">180+ Days</option>
                <option value="365">365+ Days</option>
              </select>
            </label>
            <Button onClick={checkAvailability} disabled={!currentOrder || loading || !scopeReady}><Search className="mr-2 h-4 w-4" />Check Branch Availability</Button>
            <Button variant="outline" onClick={() => runAutoSuggest('branch')} disabled={!currentOrder || !currentOrder?.availability_checked || loading || !!autoSuggestLoading}>
              {autoSuggestLoading === 'branch' ? 'Suggesting…' : 'Auto Suggest Branch'}
            </Button>
            <Button onClick={() => sendRequests('branch')} disabled={!currentOrder || loading || !!sendingRequest}>
              <Send className="mr-2 h-4 w-4" />{sendingRequest === 'branch' ? 'Sending…' : 'Send Branch Request'}
            </Button>
          </div>
        )}

        {activeStage === 'dealer' && (
          <div className="flex flex-wrap items-end gap-2 p-3 border-b">
            <label className="min-w-[130px] text-xs font-medium text-slate-600">Dealer Aging Type
              <select value={dealerAgingType} onChange={e => setDealerAgingType(e.target.value)} className="mt-1 h-9 w-full rounded-md border px-2 text-sm">
                <option value="purchase">Purchase Aging</option>
                <option value="sales">Sales Aging</option>
              </select>
            </label>
            <label className="min-w-[130px] text-xs font-medium text-slate-600">Dealer Aging Cutoff
              <select value={dealerAgingMin} onChange={e => setDealerAgingMin(e.target.value)} className="mt-1 h-9 w-full rounded-md border px-2 text-sm">
                <option value="0">All Aging</option>
                <option value="30">30+ Days</option>
                <option value="60">60+ Days</option>
                <option value="90">90+ Days</option>
                <option value="120">120+ Days</option>
                <option value="180">180+ Days</option>
                <option value="365">365+ Days</option>
              </select>
            </label>
            <Button onClick={checkAvailability} disabled={!currentOrder || loading || !scopeReady}><Search className="mr-2 h-4 w-4" />Check Dealer Availability</Button>
            <Button variant="outline" onClick={() => runAutoSuggest('dealer')} disabled={!currentOrder || !currentOrder?.availability_checked || loading || !!autoSuggestLoading}>
              {autoSuggestLoading === 'dealer' ? 'Suggesting…' : 'Auto Suggest Dealer'}
            </Button>
            <Button onClick={() => sendRequests('dealer')} disabled={!currentOrder || loading || !!sendingRequest}>
              <Send className="mr-2 h-4 w-4" />{sendingRequest === 'dealer' ? 'Sending…' : 'Send Dealer Request'}
            </Button>
          </div>
        )}

        {activeStage === 'factory' && (
          <div className="p-3 border-b text-sm text-slate-600">
            Factory / Other Source is available only for remaining quantity after Branch and Dealer stages are exhausted.
            Items below show Factory Order Qty. Prior request history is retained.
          </div>
        )}

        <div className="overflow-x-auto">
          <table className="w-full text-sm min-w-[920px]">
            <thead className="bg-emerald-50">
              <tr>{['Part No', 'Part Name', 'Required', 'Accepted', 'Remaining', 'Requested From', 'Status', 'Action'].map(h => <th key={h} className="p-3 text-left">{h}</th>)}</tr>
            </thead>
            <tbody>
              {filteredItems.map(item => {
                const selected = allocations[item.id] || [];
                const remaining = item.remaining_qty != null ? Number(item.remaining_qty) : Math.max(0, Number(item.required_qty || 0) - Number(item.accepted_qty || 0));
                const expanded = expandedItem === item.id;
                const status = item.display_status || item.request_status || 'To Process';
                const sources = stageSources(item);
                const showFactory = activeStage === 'factory' || item.factory_stage_status === 'open';
                return (
                  <React.Fragment key={item.id}>
                    <tr className={`border-t align-top ${item.qty_locked ? 'bg-sky-50/50' : ''}`}>
                      <td className="p-3 font-medium">
                        <button type="button" className="text-left text-emerald-800 hover:underline" onClick={() => setExpandedItem(expanded ? '' : item.id)}>
                          {item.part_number} {expanded ? <ChevronUp className="inline h-3 w-3" /> : <ChevronDown className="inline h-3 w-3" />}
                        </button>
                        {item.qty_locked && <div className="text-[10px] text-sky-700 flex items-center gap-1 mt-1"><Lock className="h-3 w-3" />{item.lock_reason || 'Locked'}</div>}
                        {item.factory_order_qty > 0 && <div className="text-[10px] font-semibold text-slate-700">Factory Qty: {formatNumber(item.factory_order_qty)}</div>}
                      </td>
                      <td className="p-3">{item.description}</td>
                      <td className="p-3">{formatNumber(item.required_qty)}</td>
                      <td className="p-3">{formatNumber(item.accepted_qty)}</td>
                      <td className="p-3">{formatNumber(remaining)}</td>
                      <td className="p-3 text-xs leading-snug max-w-[220px]">{compactRequestedFrom(item, selected)}</td>
                      <td className="p-3"><StatusBadge status={status} /></td>
                      <td className="p-3 space-y-1">
                        {item.cancel_allowed && item.pending_request_number && (
                          <Button size="sm" variant="outline" onClick={() => cancelTimeout(item.pending_request_number)}>Cancel – No Response</Button>
                        )}
                        {!item.qty_locked && !['Cancelled', 'Cancellation Requested', 'Accepted', 'Completed'].includes(item.request_status) && (
                          <Button size="sm" variant="outline" className="text-amber-800" onClick={() => { setCancelItem(item); setCancelOpen(true); setCancelReason(''); setCancelRemarks(''); }}>Request Cancellation</Button>
                        )}
                      </td>
                    </tr>
                    {expanded && (
                      <tr className="bg-slate-50 border-t">
                        <td colSpan={8} className="p-4 space-y-4">
                          {showFactory && activeStage === 'factory' ? (
                            <div className="rounded-lg border bg-white p-4">
                              <div className="font-semibold mb-1">Factory / Other Source</div>
                              <div className="text-sm text-slate-600">Remaining Qty for factory / external procurement: <b>{formatNumber(item.factory_order_qty || remaining)}</b></div>
                            </div>
                          ) : (
                            <div>
                              <div className="font-semibold mb-2">{activeStage === 'dealer' ? 'Dealer Availability' : 'Branch Availability'}</div>
                              <div className="grid gap-2">
                                {sources.map(source => {
                                  const key = `${source.dealer_name}__${source.branch}`;
                                  const value = (selected.find(x => `${x.dealer_name}__${x.branch}` === key && !(x.request_no || x.request_number))?.request_qty) || '';
                                  const frozen = source.source_frozen_today || source.rejected_today;
                                  return (
                                    <div key={key} className={`grid grid-cols-2 md:grid-cols-7 gap-2 items-center rounded-lg border p-3 ${frozen ? 'bg-rose-50 opacity-80' : 'bg-white'}`}>
                                      <div><div className="text-xs text-slate-500">{activeStage === 'dealer' ? 'Dealer' : 'Branch'}</div>{activeStage === 'dealer' ? `${source.dealer_name} / ${source.branch}` : source.branch}</div>
                                      <div><div className="text-xs text-slate-500">Available Qty</div>{formatNumber(source.available_qty)}{Number(source.reserved_qty || 0) > 0 && <div className="text-[11px] text-amber-600">{formatNumber(source.reserved_qty)} reserved</div>}</div>
                                      <div><div className="text-xs text-slate-500">Purchase Aging</div>{source.purchase_aging_days ?? source.aging_days ?? '-'}</div>
                                      <div><div className="text-xs text-slate-500">Sales Aging</div>{source.sales_aging_days ?? '-'}</div>
                                      <div><div className="text-xs text-slate-500">LOC</div>{source.loc || '-'}</div>
                                      <div>
                                        <div className="text-xs text-slate-500">Suggested Qty</div>
                                        {frozen ? <StatusBadge status="Rejected Today" /> : (
                                          <input type="number" min="0" max={source.net_available_qty ?? source.available_qty} value={value}
                                            disabled={!!item.qty_locked && remaining <= 0 || source.selection_disabled}
                                            onChange={e => updateAllocation(item, source, e.target.value, activeStage)}
                                            className="h-9 w-full rounded border px-2" />
                                        )}
                                      </div>
                                      <div className="text-xs">{frozen ? 'Frozen Today' : (Number(value) > 0 ? `Selected ${value}` : 'Enter qty')}</div>
                                    </div>
                                  );
                                })}
                                {!sources.length && <div className="text-sm text-slate-500">No eligible sources in this stage for the selected aging cutoff.</div>}
                              </div>
                            </div>
                          )}

                          <div>
                            <div className="font-semibold mb-2">Request History</div>
                            <div className="overflow-x-auto rounded-lg border bg-white">
                              <table className="w-full text-xs min-w-[800px]">
                                <thead className="bg-slate-100"><tr>{['Type', 'Source', 'Req', 'Accepted', 'Request No', 'Status', 'Timer', 'Remarks'].map(h => <th key={h} className="p-2 text-left">{h}</th>)}</tr></thead>
                                <tbody>
                                  {(item.request_history || []).map((row, idx) => (
                                    <tr key={`${row.request_id || idx}`} className="border-t">
                                      <td className="p-2">{row.source_type}</td>
                                      <td className="p-2">{row.source_name}</td>
                                      <td className="p-2">{formatNumber(row.requested_qty)}</td>
                                      <td className="p-2">{formatNumber(row.accepted_qty)}</td>
                                      <td className="p-2 text-emerald-700 font-medium">{row.request_no || '-'}</td>
                                      <td className="p-2"><StatusBadge status={row.request_status} /></td>
                                      <td className="p-2">
                                        {row.remaining_seconds != null && row.response_status === 'awaiting'
                                          ? `${Math.max(0, Math.ceil(row.remaining_seconds / 60))} min left`
                                          : (row.response_status || '—')}
                                        {row.cancel_allowed && (
                                          <Button size="sm" variant="outline" className="ml-2 h-7" onClick={() => cancelTimeout(row.request_no)}>Cancel – No Response</Button>
                                        )}
                                      </td>
                                      <td className="p-2">{row.remarks || '—'}</td>
                                    </tr>
                                  ))}
                                  {!(item.request_history || []).length && <tr><td colSpan={8} className="p-3 text-center text-slate-500">No requests yet.</td></tr>}
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
              {!items.length && <tr><td colSpan={8} className="p-10 text-center text-slate-500">Upload Excel or Copy From Excel to create an order.</td></tr>}
              {items.length > 0 && !filteredItems.length && <tr><td colSpan={8} className="p-10 text-center text-slate-500">No items match filters.</td></tr>}
            </tbody>
          </table>
        </div>
      </div>

      <Dialog open={pasteOpen} onOpenChange={setPasteOpen}>
        <DialogContent className="max-w-3xl">
          <DialogHeader><DialogTitle>Copy From Excel</DialogTitle></DialogHeader>
          <textarea value={pasteText} onChange={e => setPasteText(e.target.value)} rows={12} className="w-full rounded-lg border p-3 font-mono text-sm" placeholder={'Part Number\tQuantity\tDescription\tValue'} />
          <div className="flex justify-end gap-2"><Button variant="outline" onClick={() => setPasteOpen(false)}>Cancel</Button><Button onClick={submitPaste} disabled={loading}>Create Order</Button></div>
        </DialogContent>
      </Dialog>

      <Dialog open={addItemsOpen} onOpenChange={setAddItemsOpen}>
        <DialogContent className="max-w-3xl">
          <DialogHeader><DialogTitle>Add Items to {currentOrder?.order_number || 'Order'}</DialogTitle></DialogHeader>
          <textarea value={addItemsText} onChange={e => setAddItemsText(e.target.value)} rows={10} className="w-full rounded-lg border p-3 font-mono text-sm" />
          <div className="flex justify-end gap-2"><Button variant="outline" onClick={() => setAddItemsOpen(false)}>Cancel</Button><Button onClick={submitAddItems} disabled={loading}>Add Items</Button></div>
        </DialogContent>
      </Dialog>

      <Dialog open={cancelOpen} onOpenChange={setCancelOpen}>
        <DialogContent className="max-w-md">
          <DialogHeader><DialogTitle>Request Cancellation</DialogTitle></DialogHeader>
          <p className="text-sm text-slate-500">Part {cancelItem?.part_number} — records are never deleted. Disabled while awaiting response before timeout.</p>
          <label className="block text-xs font-medium text-slate-600 mt-2">Reason
            <select value={cancelReason} onChange={e => setCancelReason(e.target.value)} className="mt-1 h-9 w-full rounded-md border px-2 text-sm">
              <option value="">Select reason</option>
              {CANCELLATION_REASONS.map(r => <option key={r} value={r}>{r}</option>)}
            </select>
          </label>
          <label className="block text-xs font-medium text-slate-600 mt-3">Remarks
            <textarea value={cancelRemarks} onChange={e => setCancelRemarks(e.target.value)} rows={3} className="mt-1 w-full rounded-md border p-2 text-sm" />
          </label>
          <div className="flex justify-end gap-2 mt-3">
            <Button variant="outline" onClick={() => setCancelOpen(false)}>Close</Button>
            <Button onClick={submitCancellation} disabled={loading}>Submit</Button>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
