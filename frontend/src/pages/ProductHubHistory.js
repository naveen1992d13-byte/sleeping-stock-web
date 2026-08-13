import React, { useEffect, useState } from 'react';
import { useOutletContext } from 'react-router-dom';
import axios from 'axios';
import { API, useAuth } from '@/App';
import { Button } from '@/components/ui/button';
import { History, Download, Search, RotateCcw, Eye, ChevronLeft, ChevronRight } from 'lucide-react';
import { toast } from 'sonner';
import { canExportExcel } from '@/lib/excelPermissions';

const COLORS={primary:'#059669',dark:'#047857',soft:'#ECFDF5',border:'#D1D5DB',muted:'#6B7280'};
const isAll=(v)=>!v||String(v).startsWith('All ')||v==='N/A';

function todayIso(){return new Date().toISOString().slice(0,10);}
function firstOfMonthIso(){const d=new Date(); return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-01`;}

async function downloadBlob(url,fallback){
  const res=await axios.get(url,{responseType:'blob'});
  const cd=res.headers?.['content-disposition']||'';
  const match=/filename="?([^";]+)"?/i.exec(cd);
  const blobUrl=window.URL.createObjectURL(new Blob([res.data]));
  const link=document.createElement('a'); link.href=blobUrl; link.download=match?.[1]||fallback;
  document.body.appendChild(link); link.click(); link.remove(); window.URL.revokeObjectURL(blobUrl);
}

export function ProductHubHistory(){
  const { user } = useAuth();
  const canExport = canExportExcel(user);
  const outlet=useOutletContext()||{};
  const [fromDate,setFromDate]=useState(firstOfMonthIso());
  const [toDate,setToDate]=useState(todayIso());
  const [appliedRange,setAppliedRange]=useState({from:firstOfMonthIso(),to:todayIso()});
  const [rows,setRows]=useState([]);
  const [loading,setLoading]=useState(false);
  const [downloading,setDownloading]=useState(false);
  const [viewRow,setViewRow]=useState(null);
  const [detailRows,setDetailRows]=useState([]);
  const [detailPage,setDetailPage]=useState(1);
  const [detailMeta,setDetailMeta]=useState(null);
  const [detailSources,setDetailSources]=useState({});
  const [detailLoading,setDetailLoading]=useState(false);
  const [detailSearch,setDetailSearch]=useState('');
  const [archiveUnavailableMsg,setArchiveUnavailableMsg]=useState('');
  const scopeBrand=outlet.scopeBrand||'All Brands';
  const scopeDealer=outlet.scopeDealer||'All Dealers';
  const scopeBranch=outlet.scopeBranch||'All Branches';

  const buildParams=(extra={})=>{const p=new URLSearchParams();
    if(!isAll(scopeBrand))p.append('brand',scopeBrand);
    if(!isAll(scopeDealer))p.append('dealer',scopeDealer);
    if(!isAll(scopeBranch))p.append('branch',scopeBranch);
    Object.entries(extra).forEach(([k,v])=>{if(v)p.append(k,v)}); return p;
  };

  const load=async(range=appliedRange)=>{setLoading(true); try{
    const p=buildParams({from_date:range.from,to_date:range.to});
    const res=await axios.get(`${API}/product-hub-history?${p.toString()}`);
    setRows(Array.isArray(res.data)?res.data:[]);
  }catch{toast.error('History load failed')}finally{setLoading(false)}};

  useEffect(()=>{load(appliedRange);/*eslint-disable-next-line*/},[scopeBrand,scopeDealer,scopeBranch,appliedRange.from,appliedRange.to]);

  const search=()=>{if(!fromDate||!toDate)return toast.error('Select From Date and To Date'); if(fromDate>toDate)return toast.error('From Date cannot be after To Date'); setAppliedRange({from:fromDate,to:toDate});};
  const clear=()=>{const range={from:firstOfMonthIso(),to:todayIso()}; setFromDate(range.from);setToDate(range.to);setAppliedRange(range);};
  const downloadRow=async(r)=>{const p=buildParams({date_key:r.date_key,brand:r.brand,dealer:r.dealer,branch:r.branch}); try{await downloadBlob(`${API}/product-hub-history/download?${p.toString()}`,`Product_Hub_History_${r.date_key||''}.xlsx`)}catch{toast.error('Download failed')}};
  const downloadFiltered=async()=>{if(!rows.length)return toast.error('No filtered data to download'); setDownloading(true); try{const p=buildParams({from_date:appliedRange.from,to_date:appliedRange.to}); await downloadBlob(`${API}/product-hub-history/download?${p.toString()}`,`Product_Hub_History_${appliedRange.from}_to_${appliedRange.to}.xlsx`); toast.success('Filtered history downloaded')}catch{toast.error('Download failed')}finally{setDownloading(false)}};

  const openView=async(r,page=1,searchText=detailSearch)=>{
    setViewRow(r); setDetailPage(page); setDetailLoading(true); setArchiveUnavailableMsg('');
    try{
      const p=buildParams({
        date_key:r.date_key,
        brand:r.brand,
        dealer:r.dealer,
        branch:r.branch,
        page:String(page),
        page_size:'50',
        search:searchText||undefined,
      });
      const res=await axios.get(`${API}/product-hub-history/rows?${p.toString()}`);
      if(res.data?.archive_unavailable){
        setDetailRows([]);
        setDetailMeta(null);
        setDetailSources(res.data?.sources||{});
        setArchiveUnavailableMsg(res.data?.message||'Archive temporarily unavailable. Please retry.');
        toast.error(res.data?.message||'Archive temporarily unavailable. Please retry.');
      }else{
        setDetailRows(Array.isArray(res.data?.rows)?res.data.rows:[]);
        setDetailMeta(res.data?.page||null);
        setDetailSources(res.data?.sources||{});
      }
    }catch{toast.error('Failed to load historical rows')}finally{setDetailLoading(false)};
  };

  return <div className="space-y-3">
    <div className="flex flex-wrap items-end gap-2 rounded-xl border bg-white p-3 shadow-sm">
      <div className="flex items-center gap-2 mr-auto min-w-[200px]">
        <History className="h-5 w-5 text-emerald-700 shrink-0"/>
        <span className="text-sm font-semibold text-gray-800">Product Hub History</span>
      </div>
      <div className="flex flex-wrap items-end gap-2 bg-white p-3 rounded-xl" style={{border:`1px solid ${COLORS.border}`}}>
        <label className="text-xs font-bold" style={{color:COLORS.muted}}>From Date<input type="date" value={fromDate} onChange={e=>setFromDate(e.target.value)} className="block mt-1 px-3 py-2 rounded-xl border bg-white font-normal text-sm"/></label>
        <label className="text-xs font-bold" style={{color:COLORS.muted}}>To Date<input type="date" value={toDate} onChange={e=>setToDate(e.target.value)} className="block mt-1 px-3 py-2 rounded-xl border bg-white font-normal text-sm"/></label>
        <Button onClick={search} className="gap-2" style={{backgroundColor:COLORS.primary,color:'#fff'}}><Search className="h-4 w-4"/>Search</Button>
        <Button onClick={clear} variant="outline" className="gap-2"><RotateCcw className="h-4 w-4"/>Clear</Button>
        {canExport && <Button onClick={downloadFiltered} disabled={downloading} variant="outline" className="gap-2"><Download className="h-4 w-4"/>{downloading?'Downloading…':'Download Filtered'}</Button>}
      </div>
    </div>
    <div className="overflow-x-auto rounded-2xl bg-white" style={{border:`1px solid ${COLORS.border}`}}><table className="w-full text-sm"><thead><tr style={{backgroundColor:COLORS.primary,color:'#fff'}}>{['Date','Brand','Dealer','Branch','Records','Total Qty','Total Value','Action'].map(x=><th key={x} className="p-3 text-left">{x}</th>)}</tr></thead><tbody>{!loading&&rows.length===0?<tr><td colSpan={8} className="p-5 text-center" style={{color:COLORS.muted}}>No history found for selected date range</td></tr>:rows.map((r,i)=><tr key={`${r.date_key}-${r.brand}-${r.dealer}-${r.branch}-${i}`} className="border-b" style={{backgroundColor:i%2?'#fff':COLORS.soft}}><td className="p-3">{r.date_key||'-'}</td><td className="p-3">{r.brand||'-'}</td><td className="p-3">{r.dealer||'-'}</td><td className="p-3">{r.branch||'-'}</td><td className="p-3">{r.records||0}</td><td className="p-3">{Number(r.total_available_qty||0).toLocaleString('en-IN')}</td><td className="p-3">₹{Number(r.total_value||0).toLocaleString('en-IN')}</td><td className="p-3"><div className="flex flex-wrap gap-2"><Button onClick={()=>openView(r,1,'')} variant="outline" className="gap-2"><Eye className="h-4 w-4"/>View</Button>{canExport ? <Button onClick={()=>downloadRow(r)} variant="outline" className="gap-2"><Download className="h-4 w-4"/>Download</Button> : <span className="text-xs text-gray-400 self-center">Excel restricted</span>}</div></td></tr>)}</tbody></table></div>

    {viewRow && (
      <div className="rounded-2xl border bg-white p-4 space-y-3" style={{borderColor:COLORS.border}}>
        <div className="flex flex-wrap items-center gap-2">
          <div className="mr-auto text-sm font-semibold text-gray-800">
            Viewing {viewRow.date_key} · {viewRow.brand} · {viewRow.dealer} · {viewRow.branch}
            <span className="ml-2 text-xs font-normal text-gray-500">source: {Object.values(detailSources||{}).join(', ')||'—'}</span>
          </div>
          <input value={detailSearch} onChange={e=>setDetailSearch(e.target.value)} placeholder="Search part" className="rounded border px-2 py-1 text-sm"/>
          <Button variant="outline" onClick={()=>openView(viewRow,1,detailSearch)} className="gap-2"><Search className="h-4 w-4"/>Filter</Button>
          <Button variant="outline" onClick={()=>{setViewRow(null);setDetailRows([]);}}>Close</Button>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead><tr style={{backgroundColor:COLORS.dark,color:'#fff'}}>{['Part No','Part Name','Qty','Value','Category'].map(h=><th key={h} className="p-2 text-left">{h}</th>)}</tr></thead>
            <tbody>
              {detailLoading?<tr><td colSpan={5} className="p-4 text-center text-gray-500">Loading…</td></tr>:
               archiveUnavailableMsg?<tr><td colSpan={5} className="p-4 text-center text-amber-700 font-medium">{archiveUnavailableMsg}</td></tr>:
               detailRows.length===0?<tr><td colSpan={5} className="p-4 text-center text-gray-500">No rows</td></tr>:
               detailRows.map((p,i)=><tr key={`${p.part_number}-${i}`} className="border-b"><td className="p-2">{p.part_number||'-'}</td><td className="p-2">{p.item_name||p.part_name||'-'}</td><td className="p-2">{p.available_qty_number??p.quantity??0}</td><td className="p-2">{p.total_value_number??p.total_value??0}</td><td className="p-2">{p.part_category||p.category||'-'}</td></tr>)}
            </tbody>
          </table>
        </div>
        <div className="flex items-center gap-2 text-sm text-gray-600">
          <Button size="sm" variant="outline" disabled={!detailMeta||detailPage<=1||detailLoading} onClick={()=>openView(viewRow,detailPage-1,detailSearch)}><ChevronLeft className="h-4 w-4"/></Button>
          <span>Page {detailMeta?.page||detailPage} / {detailMeta?.total_pages||1} · {detailMeta?.total??detailRows.length} rows</span>
          <Button size="sm" variant="outline" disabled={!detailMeta||detailPage>=(detailMeta.total_pages||1)||detailLoading} onClick={()=>openView(viewRow,detailPage+1,detailSearch)}><ChevronRight className="h-4 w-4"/></Button>
        </div>
      </div>
    )}
  </div>;
}
