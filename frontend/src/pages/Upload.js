import React, { useEffect, useMemo, useRef, useState } from 'react';
import { useOutletContext } from 'react-router-dom';
import axios from 'axios';
import { API, useAuth } from '@/App';
import { Button } from '@/components/ui/button';
import { Upload, Package, ClipboardCheck, Download, Send, FileSpreadsheet, Search, Eye, XCircle, CheckCircle2 } from 'lucide-react';
import { toast } from 'sonner';
import * as XLSX from 'xlsx';

const COLORS = { primary: '#059669', dark: '#047857', soft: '#ECFDF5', border: '#D1D5DB', text: '#1F2937', muted: '#6B7280', danger: '#DC2626', warning: '#D97706', blue: '#2563EB' };
const CANCEL_REASONS = ['Wrong File', 'Duplicate Upload', 'Wrong Brand', 'Wrong Dealer', 'Wrong Branch', 'Incorrect Data', 'Other'];
const isAll = (v) => !v || String(v).startsWith('All ') || v === 'N/A';

// Authenticated file download helper. Uses axios (which already attaches the
// Bearer token via the global interceptor in App.js) instead of window.open,
// which sends no Authorization header and results in "Not Authenticated".
async function authenticatedDownload(url, fallbackFileName, onProgress) {
  const res = await axios.get(url, {
    responseType: 'blob',
    onDownloadProgress: (event) => {
      if (!onProgress || !event.total) return;
      onProgress(Math.round((event.loaded * 100) / event.total));
    },
  });
  const contentDisposition = res.headers?.['content-disposition'] || '';
  const match = /filename="?([^"]+)"?/i.exec(contentDisposition);
  const fileName = match?.[1] || fallbackFileName || 'download.xlsx';
  const blobUrl = window.URL.createObjectURL(new Blob([res.data]));
  const link = document.createElement('a');
  link.href = blobUrl;
  link.setAttribute('download', fileName);
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.URL.revokeObjectURL(blobUrl);
}

export function UploadCenter() {
  const { user } = useAuth();
  const outletScope = useOutletContext() || {};
  const scopeBrand = outletScope.scopeBrand || 'All Brands';
  const scopeDealer = outletScope.scopeDealer || 'All Dealers';
  const scopeBranch = outletScope.scopeBranch || 'All Branches';
  const activeType = 'product';
  const [search, setSearch] = useState('');
  const [productFile, setProductFile] = useState(null);
  const [orderFile, setOrderFile] = useState(null);
  const [productUploads, setProductUploads] = useState([]);
  const [orderUploads, setOrderUploads] = useState([]);
  const [uploading, setUploading] = useState(false);
  const [downloadingKey, setDownloadingKey] = useState(null);
  const [cancelTarget, setCancelTarget] = useState(null);
  const [cancelReason, setCancelReason] = useState(CANCEL_REASONS[0]);
  const [masterSummary, setMasterSummary] = useState(null);
  const [todaySummary, setTodaySummary] = useState(null);
  const productFileRef = useRef(null);
  const orderFileRef = useRef(null);

  const isMaster = user?.role === 'master';
  const isAdmin = user?.role === 'admin';
  const isUser = user?.role === 'user';
  const activeFile = activeType === 'product' ? productFile : orderFile;
  const activeUploads = activeType === 'product' ? productUploads : orderUploads;
  const activeTitle = activeType === 'product' ? 'Product Hub' : 'Order Desk';
  const TypeIcon = activeType === 'product' ? Package : ClipboardCheck;

  useEffect(() => {
    fetchUploads();
    // Master/Admin see the Brand/Dealer/Branch roll-up cards; a plain User sees
    // only their own Today Summary. Both automatically refresh whenever the
    // DashboardLayout Brand → Dealer → Branch filter (the single source of truth)
    // changes, since that filter is passed in via scopeBrand/scopeDealer/scopeBranch.
    if (isMaster || isAdmin) fetchMasterSummary();
    if (isUser) fetchTodaySummary();
    /* eslint-disable-next-line */
  }, [scopeBrand, scopeDealer, scopeBranch]);

  const fetchMasterSummary = async () => {
    try {
      const params = new URLSearchParams();
      if (scopeBrand) params.append('brand', scopeBrand);
      if (scopeDealer) params.append('dealer', scopeDealer);
      if (scopeBranch) params.append('branch', scopeBranch);
      const res = await axios.get(`${API}/uploads/master-summary?${params.toString()}`);
      setMasterSummary(res.data || null);
    } catch (e) { setMasterSummary(null); }
  };

  // User's own Today Summary — the backend always scopes this to the logged-in
  // user's own uploads for a 'user' role, regardless of any filter, so this can
  // never show another user's uploaded data.
  const fetchTodaySummary = async () => {
    try {
      const res = await axios.get(`${API}/uploads/today-summary`);
      setTodaySummary(res.data || null);
    } catch (e) { setTodaySummary(null); }
  };

  const uploadQuery = (type) => {
    const params = new URLSearchParams({ type });
    if (scopeBrand) params.append('brand', scopeBrand);
    if (scopeDealer) params.append('dealer', scopeDealer);
    if (scopeBranch) params.append('branch', scopeBranch);
    return params.toString();
  };

  const fetchUploads = async () => {
    try {
      const [p, o] = await Promise.all([axios.get(`${API}/uploads/v2?${uploadQuery('product')}`), axios.get(`${API}/uploads/v2?${uploadQuery('order')}`)]);
      setProductUploads(Array.isArray(p.data) ? p.data : []);
      setOrderUploads(Array.isArray(o.data) ? o.data : []);
    } catch (e) { toast.error('Upload history load failed'); }
  };

  const setFile = (type, file) => type === 'product' ? setProductFile(file) : setOrderFile(file);
  const handleFilePick = (type, e) => {
    e.preventDefault();
    const file = e.dataTransfer?.files?.[0] || e.target?.files?.[0];
    if (!file) return;
    if (!file.name.endsWith('.xlsx') && !file.name.endsWith('.xls')) return toast.error('Please select Excel file only');
    setFile(type, file);
  };

  const handleUpload = async () => {
    if (!activeFile) return toast.error('Please select an Excel file first');
    const form = new FormData();
    form.append('file', activeFile);
    form.append('upload_type', activeType);
    setUploading(true);
    try {
      const endpoint = activeType === 'product' ? `${API}/upload/v2` : `${API}/orders/upload`;
      const res = await axios.post(endpoint, form, { headers: { 'Content-Type': 'multipart/form-data' } });
      toast.success(`Uploaded. No: ${res.data?.upload_no || ''}`);
      setFile(activeType, null);
      fetchUploads();
      if (isMaster || isAdmin) fetchMasterSummary();
      if (isUser) fetchTodaySummary();
    } catch (e) { toast.error(e.response?.data?.detail || 'Upload failed'); }
    finally { setUploading(false); }
  };

  const downloadSampleTemplate = async () => {
    const typeName = activeType === 'product' ? 'Product Hub' : 'Order Desk';
    const brandName = !isAll(scopeBrand) ? scopeBrand : user?.brand;
    if (!brandName) return toast.error('Please select Brand');
    try {
      const res = await axios.get(`${API}/templates`);
      const list = Array.isArray(res.data) ? res.data : [];
      const t = list.find(x => (x.brand || '') === brandName && ((x.templateType || x.template_type || '') === typeName));
      if (!t) return toast.error(`${brandName} ${typeName} template not found`);
      const id = t.id || t.templateId || t.template_id;
      if (downloadingKey) return toast.info('Download already in progress');
      setDownloadingKey('template');
      try {
        await authenticatedDownload(`${API}/templates/download/${id}`, t.fileName || `${brandName}_${typeName}_template.xlsx`);
        toast.success('Template downloaded');
      } finally {
        setDownloadingKey(null);
      }
    } catch (e) { toast.error(e.response?.data?.detail || 'Template download failed'); setDownloadingKey(null); }
  };

  const publishUpload = async (u) => {
    if (!window.confirm(`Publish ${u.upload_no} to ${activeTitle}?`)) return;
    try {
      await axios.put(`${API}/uploads/${u.id}/publish-v2`);
      toast.success('Published');
      fetchUploads();
      if (isMaster || isAdmin) fetchMasterSummary();
      if (isUser) fetchTodaySummary();
    } catch (e) { toast.error(e.response?.data?.detail || 'Publish failed'); }
  };

  const openCancelModal = (u) => { setCancelTarget(u); setCancelReason(CANCEL_REASONS[0]); };
  const closeCancelModal = () => setCancelTarget(null);
  const confirmCancelUpload = async () => {
    if (!cancelTarget) return;
    if (!cancelReason) return toast.error('Reason is required');
    try {
      await axios.put(`${API}/uploads/${cancelTarget.id}/cancel-v2`, { reason: cancelReason });
      toast.success('Cancelled');
      closeCancelModal();
      fetchUploads();
      if (isMaster || isAdmin) fetchMasterSummary();
      if (isUser) fetchTodaySummary();
    } catch (e) { toast.error(e.response?.data?.detail || 'Cancel failed'); }
  };

  const downloadRaw = async (u) => {
    if (downloadingKey) return toast.info('Download already in progress');
    const key = `raw-${u.id}`;
    setDownloadingKey(key);
    try {
      await authenticatedDownload(`${API}/uploads/${u.id}/raw-file`, u.file_name || 'raw_upload.xlsx');
      toast.success('Raw Excel downloaded');
    } catch (e) { toast.error(e.response?.data?.detail || 'Raw file download failed'); }
    finally { setDownloadingKey(null); }
  };
  const clearFile = () => setFile(activeType, null);
  const exportData = () => {
    if (downloadingKey) return toast.info('Download already in progress');
    if (!filteredUploads.length) return toast.error('No data to export');
    setDownloadingKey('history-export');
    const ws = XLSX.utils.json_to_sheet(filteredUploads);
    const wb = XLSX.utils.book_new();
    XLSX.utils.book_append_sheet(wb, ws, `${activeTitle} History`);
    XLSX.writeFile(wb, `${activeType}_upload_history.xlsx`);
    toast.success('Upload history exported');
    setDownloadingKey(null);
  };

  const filteredUploads = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return activeUploads;
    return activeUploads.filter(x => [x.upload_no, x.file_name, x.uploaded_user_name, x.user_code, x.brand_name, x.dealer_name, x.branch, x.status, x.publish_status].filter(Boolean).some(v => String(v).toLowerCase().includes(q)));
  }, [activeUploads, search]);

  const latest = activeUploads[0] || null;
  const summary = latest ? { totalItems: latest.item_count || latest.rows_imported || 0, totalQty: latest.total_available_qty || 0, totalValue: latest.total_value || 0 } : { totalItems: 0, totalQty: 0, totalValue: 0 };

  return <div className="space-y-4" data-testid="upload-center-page">
    <div className="flex items-center justify-between gap-3">
      <div className="flex items-center gap-3"><Upload className="h-8 w-8" style={{color: COLORS.dark}}/><div><h1 className="text-2xl font-bold" style={{color: COLORS.dark}}>Upload Center</h1><p className="text-sm" style={{color: COLORS.muted}}>Upload, validate, publish and store raw Excel files.</p></div></div>
      <Button onClick={fetchUploads} variant="outline">Refresh</Button>
    </div>

    {(isMaster || isAdmin) && masterSummary && (
      <div className="rounded-2xl bg-white p-4 shadow-sm" style={{border: `1px solid ${COLORS.border}`}}>
        <h2 className="text-sm font-bold mb-3" style={{color: COLORS.muted}}>{isMaster ? 'Master Admin Summary' : 'Admin Summary'} — Today</h2>
        <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-9 gap-2">
          <SummaryCard title="Total Uploaded Brand" value={masterSummary.brandsUploaded} />
          <SummaryCard title="Total Uploaded Dealer" value={masterSummary.dealersUploaded} />
          <SummaryCard title="Total Uploaded Branch" value={masterSummary.branchesUploaded} />
          <SummaryCard title="Expected Uploaders" value={masterSummary.expectedUploads} />
          <SummaryCard title="Completed Uploaders" value={masterSummary.completedUploads} color={COLORS.dark} />
          <SummaryCard title="Balance Uploaders" value={masterSummary.balanceUploads} color={COLORS.warning} />
          <SummaryCard title="Published Uploads" value={masterSummary.published} color={COLORS.dark} />
          <SummaryCard title="Pending Uploads" value={masterSummary.pending} color={COLORS.warning} />
          <SummaryCard title="Cancelled Uploads" value={masterSummary.cancelled} color={COLORS.danger} />
        </div>
      </div>
    )}

    {isUser && todaySummary && (
      <div className="rounded-2xl bg-white p-4 shadow-sm" style={{border: `1px solid ${COLORS.border}`}}>
        <h2 className="text-sm font-bold mb-3" style={{color: COLORS.muted}}>My Today Upload Summary</h2>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
          <SummaryCard title="Today Uploaded Items" value={todaySummary.todayUploadedItems} />
          <SummaryCard title="Today Uploaded Available Items" value={todaySummary.todayUploadedAvailableItems} color={COLORS.blue} />
          <SummaryCard title="Today Uploaded Available Quantity" value={todaySummary.todayUploadedAvailableQty} color={COLORS.dark} />
          <SummaryCard title="Today Uploaded Value" value={todaySummary.todayUploadedValue} prefix="₹" color={COLORS.dark} />
        </div>
      </div>
    )}

    {((isMaster || isAdmin) ? masterSummary : todaySummary) && (() => {
      const ps = (isMaster || isAdmin) ? masterSummary : todaySummary;
      const uploadedItems = (isMaster || isAdmin) ? ps.uploadedItems : ps.todayUploadedItems;
      const uploadedQty = (isMaster || isAdmin) ? ps.uploadedQty : ps.todayUploadedAvailableQty;
      const uploadedValue = (isMaster || isAdmin) ? ps.uploadedValue : ps.todayUploadedValue;
      return (
        <div className="rounded-2xl bg-white p-4 shadow-sm" style={{border: `1px solid ${COLORS.border}`}}>
          <h2 className="text-sm font-bold mb-3" style={{color: COLORS.muted}}>Publish Summary — Today</h2>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead><tr style={{backgroundColor: COLORS.primary, color:'#fff'}}><th className="p-3 text-left">Metric</th><th className="p-3 text-right">Uploaded</th><th className="p-3 text-right">Published</th><th className="p-3 text-right">Pending</th></tr></thead>
              <tbody>
                <PublishSummaryRow label="Items" uploaded={uploadedItems} published={ps.publishedItems} pending={ps.pendingItems} />
                <PublishSummaryRow label="Quantity" uploaded={uploadedQty} published={ps.publishedQty} pending={ps.pendingQty} />
                <PublishSummaryRow label="Value" uploaded={uploadedValue} published={ps.publishedValue} pending={ps.pendingValue} currency />
              </tbody>
            </table>
          </div>
        </div>
      );
    })()}

    <div className="rounded-2xl bg-white p-4 shadow-sm" style={{border: `1px solid ${COLORS.border}`}}>
      <div className="flex flex-wrap gap-2 border-b pb-3 mb-3">
        <button
          type="button"
          className="inline-flex items-center gap-2 rounded-xl px-4 py-2 text-sm font-semibold text-white"
          style={{ backgroundColor: COLORS.dark }}
        >
          <Package className="h-4 w-4" />
        Product Hub
        </button>
      </div>
      <div className="grid grid-cols-1 lg:grid-cols-[1fr_260px] gap-3">
        <div className="border-2 border-dashed rounded-2xl min-h-[130px] flex flex-col items-center justify-center text-center cursor-pointer" style={{backgroundColor: COLORS.soft, borderColor: COLORS.primary}} onClick={() => (activeType==='product'?productFileRef.current?.click():orderFileRef.current?.click())} onDrop={(e)=>handleFilePick(activeType,e)} onDragOver={(e)=>e.preventDefault()}>
          <TypeIcon className="h-8 w-8 mb-2" style={{color: COLORS.dark}} />
          <h2 className="text-base font-bold" style={{color: COLORS.text}}>{activeFile ? activeFile.name : `Drag & Drop ${activeTitle} Excel`}</h2>
          <p className="text-xs" style={{color: COLORS.muted}}>or click to browse (.xlsx / .xls)</p>
          <input type="file" ref={activeType==='product'?productFileRef:orderFileRef} className="hidden" accept=".xlsx,.xls" onChange={(e)=>handleFilePick(activeType,e)} />
        </div>
        <div className="rounded-2xl p-3 space-y-2" style={{backgroundColor:'#F9FAFB', border:`1px solid ${COLORS.border}`}}>
          <Button onClick={downloadSampleTemplate} variant="outline" className="w-full gap-2"><Download className="h-4 w-4"/> Sample Template</Button>
          <Button onClick={handleUpload} disabled={uploading} className="w-full" style={{backgroundColor: COLORS.primary, color:'#fff'}}>{uploading?'Uploading...':'Upload'}</Button>
          <Button onClick={clearFile} variant="outline" className="w-full">Clear</Button>
        </div>
      </div>
      <div className="grid grid-cols-1 md:grid-cols-3 gap-3 mt-3">
        <Stat title="Last Uploaded Items" value={Number(summary.totalItems).toLocaleString('en-IN')} />
        <Stat title="Last Uploaded Quantity" value={Number(summary.totalQty).toLocaleString('en-IN')} color={COLORS.blue} />
        <Stat title="Last Uploaded Value" value={`₹${Number(summary.totalValue).toLocaleString('en-IN')}`} color={COLORS.dark} />
      </div>
    </div>

    <div className="rounded-2xl bg-white p-4 shadow-sm" style={{border: `1px solid ${COLORS.border}`}}>
      <div className="flex flex-col md:flex-row md:items-center md:justify-between gap-3 mb-3">
        <h2 className="text-lg font-bold" style={{color: COLORS.text}}>{activeTitle} Upload History</h2>
        <div className="flex gap-2 w-full md:w-auto">
          <div className="relative flex-1 md:w-96"><Search className="h-4 w-4 absolute left-3 top-3" style={{color: COLORS.muted}}/><input value={search} onChange={(e)=>setSearch(e.target.value)} placeholder="Search Upload No / File / User" className="w-full pl-9 pr-4 py-2 rounded-xl border" /></div>
          <Button onClick={exportData} disabled={!!downloadingKey} variant="outline" className="gap-2"><FileSpreadsheet className="h-4 w-4"/> {downloadingKey === 'history-export' ? 'Exporting…' : 'Export'}</Button>
        </div>
      </div>
      <div className="overflow-x-auto rounded-xl max-h-[58vh]" style={{border:`1px solid ${COLORS.border}`}}>
        <table className="w-full text-xs"><thead><tr style={{backgroundColor: COLORS.primary, color:'#fff'}}>{['Upload No','Date','Time','Uploaded Name','User ID','File Name','Total Items','Total Qty','Total Value','Status','Action'].map(h=><th key={h} className="p-3 text-left whitespace-nowrap">{h}</th>)}</tr></thead>
        <tbody>{filteredUploads.length===0 ? <tr><td colSpan={11} className="p-5 text-center" style={{color: COLORS.muted}}>No uploads yet</td></tr> : filteredUploads.map((u,i)=><tr key={u.id||i} className="border-b" style={{backgroundColor:i%2?'#fff':COLORS.soft}}>
          <td className="p-3 font-bold whitespace-nowrap" style={{color:u.status==='Cancelled'?COLORS.danger:COLORS.dark}}>{u.upload_no||'-'}</td><td className="p-3 whitespace-nowrap">{u.upload_date||'-'}</td><td className="p-3 whitespace-nowrap">{u.upload_time||'-'}</td><td className="p-3 whitespace-nowrap">{u.uploaded_user_name||'-'}</td><td className="p-3 whitespace-nowrap">{u.user_code||u.uploaded_user_id||'-'}</td><td className="p-3 whitespace-nowrap">{u.file_name||'-'}</td><td className="p-3 whitespace-nowrap">{u.item_count||u.rows_imported||0}</td><td className="p-3 whitespace-nowrap">{Number(u.total_available_qty||0).toLocaleString('en-IN')}</td><td className="p-3 whitespace-nowrap">₹{Number(u.total_value||0).toLocaleString('en-IN')}</td><td className="p-3"><StatusBadge u={u}/></td><td className="p-3 whitespace-nowrap"><div className="flex gap-3"><Download className="h-4 w-4 cursor-pointer" onClick={()=>downloadRaw(u)} style={{color:COLORS.blue}} title="Download Raw Excel" />{(isMaster||isAdmin||isUser)&&u.publish_status!=='Published'&&u.status!=='Cancelled'&&<Send className="h-4 w-4 cursor-pointer" onClick={()=>publishUpload(u)} style={{color:COLORS.primary, backgroundColor:COLORS.soft, borderRadius:6, padding:2, width:22, height:22}} title="Publish"/>}{(isMaster||isAdmin)&&u.status!=='Cancelled'&&<XCircle className="h-4 w-4 cursor-pointer" onClick={()=>openCancelModal(u)} style={{color:COLORS.danger}} title="Cancel"/>}</div></td>
        </tr>)}</tbody></table>
      </div>
    </div>


    {cancelTarget && (
      <div className="fixed inset-0 z-50 flex items-center justify-center" style={{backgroundColor: 'rgba(0,0,0,0.4)'}} onClick={closeCancelModal}>
        <div className="bg-white rounded-2xl p-5 w-full max-w-sm shadow-xl" onClick={(e) => e.stopPropagation()}>
          <h3 className="text-lg font-bold mb-1" style={{color: COLORS.dark}}>Cancel Upload {cancelTarget.upload_no}</h3>
          <p className="text-xs mb-3" style={{color: COLORS.muted}}>Reason is required to cancel this upload.</p>
          <div className="space-y-2 mb-4">
            {CANCEL_REASONS.map((r) => (
              <label key={r} className="flex items-center gap-2 text-sm cursor-pointer" style={{color: COLORS.text}}>
                <input type="radio" name="cancelReason" value={r} checked={cancelReason === r} onChange={() => setCancelReason(r)} />
                {r}
              </label>
            ))}
          </div>
          <div className="flex gap-2 justify-end">
            <Button onClick={closeCancelModal} variant="outline">Back</Button>
            <Button onClick={confirmCancelUpload} style={{backgroundColor: COLORS.danger, color: '#fff'}}>Confirm Cancel</Button>
          </div>
        </div>
      </div>
    )}
  </div>;
}

function PublishSummaryRow({label, uploaded, published, pending, currency=false}){
  const fmt=(v)=>`${currency?'₹':''}${Number(v||0).toLocaleString('en-IN')}`;
  return <tr className="border-b"><td className="p-3 font-bold" style={{color:COLORS.text}}>{label}</td><td className="p-3 text-right">{fmt(uploaded)}</td><td className="p-3 text-right font-bold" style={{color:COLORS.dark}}>{fmt(published)}</td><td className="p-3 text-right font-bold" style={{color:COLORS.warning}}>{fmt(pending)}</td></tr>;
}
function Stat({ title, value, color = COLORS.primary }) {
  return (
    <div
      className="rounded-2xl bg-white p-4 shadow-sm"
      style={{
        border: `1px solid ${COLORS.border}`,
        borderLeft: `4px solid ${color}`,
      }}
    >
      <p
        className="text-xs font-semibold uppercase tracking-wide"
        style={{ color: COLORS.muted }}
      >
        {title}
      </p>
      <p className="mt-2 text-2xl font-bold" style={{ color }}>
        {value ?? 0}
      </p>
    </div>
  );
}

function SummaryCard({title,value,color=COLORS.primary,prefix=''}){return <div className="rounded-xl p-2 text-center" style={{backgroundColor:'#F9FAFB', border:`1px solid ${COLORS.border}`}}><p className="text-[10px] font-bold leading-tight" style={{color:COLORS.muted}}>{title}</p><h3 className="text-lg font-bold" style={{color}}>{prefix}{Number(value||0).toLocaleString('en-IN')}</h3></div>}
function StatusBadge({u}){const s=u.status||'Uploaded'; const cancelled=s==='Cancelled'; const published=u.publish_status==='Published'; const color=cancelled?COLORS.danger:published?COLORS.dark:COLORS.warning; return <span className="inline-flex items-center gap-1 px-2 py-1 rounded-full font-bold" style={{color, backgroundColor:cancelled?'#FEE2E2':published?'#DCFCE7':'#FEF3C7'}}>{published?<CheckCircle2 className="h-3 w-3"/>:<Eye className="h-3 w-3"/>}{cancelled?'Cancelled':published?'Published':s}</span>}
export default UploadCenter;
