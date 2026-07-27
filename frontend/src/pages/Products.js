import React, { useEffect, useMemo, useRef, useState } from 'react';
import { useOutletContext } from 'react-router-dom';
import axios from 'axios';
import { API, useAuth } from '@/App.js';
import { Button } from '@/components/ui/button';
import { Package, Search, Download, Archive, Boxes, Printer } from 'lucide-react';
import { toast } from 'sonner';
import { PrintStyles, PrintHeader } from '@/utils/printLayout';

const COLORS = { primary: '#059669', dark: '#047857', soft: '#ECFDF5', border: '#D1D5DB', text: '#1F2937', muted: '#6B7280', danger: '#DC2626', warning: '#D97706', blue: '#2563EB' };
const isAll = (v) => !v || String(v).startsWith('All ') || v === 'N/A';
const PAGE_SIZE_OPTIONS = [100, 300, 500, 1000];
// Required Product Hub column order (Part Number → Active Status).
const PRODUCT_HUB_COLUMNS = ['Part Number', 'Part Name', 'LOC', 'On-Hand', 'Last Receipt Date', 'Last Sales Date', 'MAV', 'Part Category', 'Branch', 'Brand', 'Dealer', 'Purchase Aging', 'Sales Aging', 'Uploaded Date', 'Uploaded User', 'Active Status'];
const CATEGORY_OPTIONS = ['All Categories', 'Genuine Parts', 'Accessories', 'Non OEM parts'];

// Uploaded Date = the item's original created_at timestamp (set once at upload
// time and carried through publish), formatted for display.
function formatUploadedDate(createdAt) {
  if (!createdAt) return '-';
  const d = new Date(createdAt);
  if (Number.isNaN(d.getTime())) return '-';
  const dd = String(d.getDate()).padStart(2, '0');
  const mm = String(d.getMonth() + 1).padStart(2, '0');
  return `${dd}-${mm}-${d.getFullYear()}`;
}

// Authenticated backend export download. Never sends raw rows to the frontend;
// the backend generates the file and streams it, so lakhs of records never
// touch the browser's memory or the JS thread.
async function authenticatedDownload(url, fallbackFileName, onProgress) {
  const res = await axios.get(url, {
    responseType: 'blob',
    onDownloadProgress: (event) => {
      if (!onProgress || !event.total) return;
      onProgress(Math.round((event.loaded * 100) / event.total));
    },
  });
  const disposition = res.headers?.['content-disposition'] || '';
  const match = /filename="?([^"]+)"?/i.exec(disposition);
  const fileName = match?.[1] || fallbackFileName;
  const blobUrl = window.URL.createObjectURL(new Blob([res.data]));
  const link = document.createElement('a');
  link.href = blobUrl;
  link.setAttribute('download', fileName);
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.URL.revokeObjectURL(blobUrl);
}

export function Products() {
  const { user } = useAuth();
  const outletScope = useOutletContext() || {};
  const scopeBrand = outletScope.scopeBrand || 'All Brands';
  const scopeDealer = outletScope.scopeDealer || 'All Dealers';
  const scopeBranch = outletScope.scopeBranch || 'All Branches';
  const isMaster = user?.role === 'master';

  const [summary, setSummary] = useState({ totalItem: 0, totalAvailableItem: 0, totalAvailableQty: 0, totalValue: 0 });

  const [records, setRecords] = useState([]);
  const [totalRecords, setTotalRecords] = useState(0);
  const [totalPages, setTotalPages] = useState(1);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(300);
  const [search, setSearch] = useState('');
  const [searchInput, setSearchInput] = useState('');
  const [category, setCategory] = useState('All Categories');
  const [stockStatus, setStockStatus] = useState('all');
  const [loadingRecords, setLoadingRecords] = useState(false);
  const [exporting, setExporting] = useState(false);

  // Master Admin sees the same part-level Product Details table as Admin/User —
  // the global Brand/Dealer/Branch filters (including "All Branches") still
  // scope which records are returned, with backend pagination throughout.
  const effectiveBranch = scopeBranch;

  useEffect(() => {
    setPage(1);
  }, [scopeBrand, scopeDealer, scopeBranch]);

  useEffect(() => {
    const t = setTimeout(() => { setSearch(searchInput.trim()); setPage(1); }, 400);
    return () => clearTimeout(t);
  }, [searchInput]);

  const scopeParams = (extra = {}) => {
    const params = new URLSearchParams();
    if (!isAll(scopeBrand)) params.append('brand', scopeBrand);
    if (!isAll(scopeDealer)) params.append('dealer', scopeDealer);
    if (!isAll(effectiveBranch)) params.append('branch', effectiveBranch);
    if (category && category !== 'All Categories') params.append('category', category);
    if (stockStatus && stockStatus !== 'all') params.append('stock_status', stockStatus);
    Object.entries(extra).forEach(([k, v]) => { if (v !== undefined && v !== null && v !== '') params.append(k, v); });
    return params.toString();
  };

  const fetchSummary = async () => {
    try {
      const res = await axios.get(`${API}/product-hub/summary?${scopeParams({ search })}`);
      setSummary(res.data || { totalItem: 0, totalAvailableItem: 0, totalAvailableQty: 0, totalValue: 0 });
    } catch { toast.error('Summary load failed'); }
  };

  const fetchRecords = async () => {
    setLoadingRecords(true);
    try {
      const res = await axios.get(`${API}/product-hub/records?${scopeParams({ search, page, page_size: pageSize })}`);
      setRecords(Array.isArray(res.data?.records) ? res.data.records : []);
      setTotalRecords(res.data?.total || 0);
      setTotalPages(res.data?.totalPages || 1);
    } catch { toast.error('Product Hub records load failed'); }
    finally { setLoadingRecords(false); }
  };

  useEffect(() => {
    fetchSummary();
    /* eslint-disable-next-line */
  }, [scopeBrand, scopeDealer, effectiveBranch, search, category, stockStatus]);

  useEffect(() => {
    setPage(1);
  }, [category, stockStatus]);

  useEffect(() => {
    fetchRecords();
    /* eslint-disable-next-line */
  }, [scopeBrand, scopeDealer, effectiveBranch, search, category, stockStatus, page, pageSize]);

  const exportBranch = async (row) => {
    if (exporting) return toast.info('Export already in progress');
    const brandName = row?.brand_name || scopeBrand;
    const dealerName = row?.dealer_name || scopeDealer;
    const branchName = row?.branch || effectiveBranch;
    if (isAll(brandName) || isAll(dealerName) || isAll(branchName)) {
      return toast.error('Select a specific Brand / Dealer / Branch to export');
    }
    setExporting(true);
    try {
      const params = new URLSearchParams({ brand: brandName, dealer: dealerName, branch: branchName });
      if (category && category !== 'All Categories') params.append('category', category);
      if (stockStatus && stockStatus !== 'all') params.append('stock_status', stockStatus);
      await authenticatedDownload(`${API}/product-hub/export/branch?${params.toString()}`, `ProductHub_${branchName}.xlsx`);
      toast.success('Branch export downloaded');
    } catch (e) { toast.error(e.response?.data?.detail || 'Export failed', { id: toastId }); }
    finally { setExporting(false); }
  };

  const exportMasterZip = async () => {
    if (exporting) return toast.info('Export already in progress');
    setExporting(true);
    try {
      const params = new URLSearchParams();
      if (!isAll(scopeBrand)) params.append('brand', scopeBrand);
      if (!isAll(scopeDealer)) params.append('dealer', scopeDealer);
      await authenticatedDownload(`${API}/product-hub/export/master?${params.toString()}`, 'ProductHub_Full_Export.zip');
      toast.success('Full export downloaded');
    } catch (e) { toast.error(e.response?.data?.detail || 'Full export failed'); }
    finally { setExporting(false); }
  };

  return <div className="space-y-4" data-testid="product-hub-page">
    <PrintStyles />
    <div className="flex items-center justify-between gap-3 flex-wrap no-print">
      <div className="flex items-center gap-3">
        <Package className="h-8 w-8" style={{ color: COLORS.dark }} />
        <div>
          <h1 className="text-2xl font-bold" style={{ color: COLORS.dark }}>Product Hub</h1>
          <p className="text-sm" style={{ color: COLORS.muted }}>Today's Published Inventory — {isAll(effectiveBranch) ? 'All Branches' : effectiveBranch}</p>
        </div>
      </div>
      {isMaster && (
        <Button onClick={exportMasterZip} disabled={exporting} variant="outline" className="gap-2">
          <Archive className="h-4 w-4" /> {exporting ? 'Preparing…' : 'Export All (ZIP)'}
        </Button>
      )}
    </div>

    <div className="grid grid-cols-2 md:grid-cols-4 gap-3 no-print">
      <SummaryCard title="Total Item" value={summary.totalItem} icon={Boxes} />
      <SummaryCard title="Total Available Item" value={summary.totalAvailableItem} color={COLORS.blue} icon={Boxes} />
      <SummaryCard title="Total Available Quantity" value={summary.totalAvailableQty} color={COLORS.dark} icon={Boxes} />
      <SummaryCard title="Total Value" value={summary.totalValue} prefix="₹" color={COLORS.dark} icon={Boxes} />
    </div>

    <div className="rounded-2xl bg-white p-4 shadow-sm" style={{ border: `1px solid ${COLORS.border}` }}>
      <div className="flex flex-col md:flex-row md:items-center md:justify-between gap-3 mb-3 no-print">
        <h2 className="text-lg font-bold" style={{ color: COLORS.text }}>{isAll(effectiveBranch) ? 'Product Records' : `${effectiveBranch} — Product Records`}</h2>
        <div className="flex gap-2 w-full md:w-auto flex-wrap">
          <div className="relative flex-1 md:w-64">
            <Search className="h-4 w-4 absolute left-3 top-3" style={{ color: COLORS.muted }} />
            <input value={searchInput} onChange={(e) => setSearchInput(e.target.value)} placeholder="Search Part No / Part Name" className="w-full pl-9 pr-4 py-2 rounded-xl border" />
          </div>
          <select value={category} onChange={(e) => setCategory(e.target.value)} className="px-3 py-2 rounded-xl border text-sm" title="Part Category">
            {CATEGORY_OPTIONS.map(c => <option key={c} value={c}>{c}</option>)}
          </select>
          <select value={stockStatus} onChange={(e) => setStockStatus(e.target.value)} className="px-3 py-2 rounded-xl border text-sm" title="Stock Status">
            <option value="all">All Items</option>
            <option value="available">Available Items</option>
            <option value="zero">Zero Quantity Items</option>
          </select>
          <select value={pageSize} onChange={(e) => { setPageSize(Number(e.target.value)); setPage(1); }} className="px-3 py-2 rounded-xl border text-sm">
            {PAGE_SIZE_OPTIONS.map(sz => <option key={sz} value={sz}>{sz} / page</option>)}
          </select>
          <Button onClick={() => exportBranch({ brand_name: scopeBrand, dealer_name: scopeDealer, branch: effectiveBranch })} disabled={exporting} variant="outline" className="gap-2">
            <Download className="h-4 w-4" /> Export
          </Button>
          <Button onClick={() => window.print()} variant="outline" className="gap-2">
            <Printer className="h-4 w-4" /> Print
          </Button>
        </div>
      </div>

      <PrintHeader
        title="Product Hub — Product Records"
        subtitle={`Scope: ${scopeBrand} / ${scopeDealer} / ${effectiveBranch}`}
        meta={[['Category', category], ['Stock Status', stockStatus === 'all' ? 'All Items' : stockStatus], ['Records', totalRecords]]}
      />

      <div className="overflow-x-auto rounded-xl max-h-[58vh]" style={{ border: `1px solid ${COLORS.border}` }}>
        <table className="w-full text-xs">
          <thead><tr style={{ backgroundColor: COLORS.primary, color: '#fff' }}>
            {PRODUCT_HUB_COLUMNS.map(h => <th key={h} className="p-3 text-left whitespace-nowrap">{h}</th>)}
          </tr></thead>
          <tbody>
            {!loadingRecords && records.length === 0 ? (
              <tr><td colSpan={PRODUCT_HUB_COLUMNS.length} className="p-5 text-center" style={{ color: COLORS.muted }}>No records found</td></tr>
            ) : records.map((p, i) => (
              <tr key={p.id || i} className="border-b" style={{ backgroundColor: i % 2 ? '#fff' : COLORS.soft }}>
                <td className="p-3 font-bold whitespace-nowrap" style={{ color: COLORS.dark }}>{p.part_number || p.part_no || '-'}</td>
                <td className="p-3 whitespace-nowrap">{p.item_name || p.part_name || p.description || '-'}</td>
                <td className="p-3 whitespace-nowrap">{p.location || p.loc || p.bin_location || '-'}</td>
                <td className="p-3 whitespace-nowrap">{Number(p.available_qty_number ?? p.quantity ?? p.available_qty ?? p.on_hand ?? 0).toLocaleString('en-IN')}</td>
                <td className="p-3 whitespace-nowrap">{p.last_receipt_date || p.receipt_date || p.last_purchase_date || '-'}</td>
                <td className="p-3 whitespace-nowrap">{p.last_sales_date || p.sales_date || p.last_sale_date || '-'}</td>
                <td className="p-3 whitespace-nowrap">₹{Number(p.mav_value ?? p.price ?? p.unit_value_number ?? p.value ?? 0).toLocaleString('en-IN')}</td>
                <td className="p-3 whitespace-nowrap">{p.part_category || p.parts_type || p.category || '-'}</td>
                <td className="p-3 whitespace-nowrap">{p.branch || p.branch_name || p.location_name || '-'}</td>
                <td className="p-3 whitespace-nowrap">{p.brand_name || p.brand || '-'}</td>
                <td className="p-3 whitespace-nowrap">{p.dealer_name || p.dealer || p.group || '-'}</td>
                <td className="p-3 whitespace-nowrap">{p.purchase_aging_days ?? p.purchase_aging ?? '-'}</td>
                <td className="p-3 whitespace-nowrap">{p.sales_aging_days ?? p.sales_aging ?? '-'}</td>
                <td className="p-3 whitespace-nowrap">{formatUploadedDate(p.created_at || p.uploaded_at || p.upload_date)}</td>
                <td className="p-3 whitespace-nowrap">{p.uploaded_user_name || p.upload_users || p.uploaded_by_name || '-'}</td>
                <td className="p-3 whitespace-nowrap">
                  <span className="px-2 py-1 rounded-full font-bold" style={{ color: (p.is_active_today ?? (p.active_status === 'Active' || p.status === 'Active')) ? COLORS.dark : COLORS.muted, backgroundColor: (p.is_active_today ?? (p.active_status === 'Active' || p.status === 'Active')) ? '#DCFCE7' : '#F3F4F6' }}>
                    {(p.is_active_today ?? (p.active_status === 'Active' || p.status === 'Active')) ? 'Active' : 'Inactive'}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="flex items-center justify-between mt-3 text-sm no-print" style={{ color: COLORS.muted }}>
        <span>{Number(totalRecords).toLocaleString('en-IN')} records{search ? ` (filtered)` : ''}</span>
        <div className="flex items-center gap-2">
          <Button onClick={() => setPage(p => Math.max(1, p - 1))} disabled={page <= 1} variant="outline" className="h-8 px-3">Prev</Button>
          <span>Page {page} / {totalPages}</span>
          <Button onClick={() => setPage(p => Math.min(totalPages, p + 1))} disabled={page >= totalPages} variant="outline" className="h-8 px-3">Next</Button>
        </div>
      </div>
    </div>

  </div>;
}

function SummaryCard({ title, value, prefix = '', color = COLORS.primary, icon: Icon }) {
  return (
    <div className="rounded-2xl bg-white p-4 shadow-sm" style={{ border: `1px solid ${COLORS.border}` }}>
      <div className="flex items-center justify-between">
        <p className="text-xs font-bold" style={{ color: COLORS.muted }}>{title}</p>
        {Icon && <Icon className="h-4 w-4" style={{ color }} />}
      </div>
      <h3 className="text-2xl font-bold mt-1" style={{ color }}>{prefix}{Number(value || 0).toLocaleString('en-IN')}</h3>
    </div>
  );
}

export default Products;
