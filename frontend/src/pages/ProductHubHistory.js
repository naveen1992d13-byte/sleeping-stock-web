import React, { useEffect, useState } from 'react';
import { useOutletContext } from 'react-router-dom';
import axios from 'axios';
import { API } from '@/App.js';
import { Button } from '@/components/ui/button';
import { History, Download, Search, RotateCcw } from 'lucide-react';
import { toast } from 'sonner';

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
  const outlet=useOutletContext()||{};
  const [fromDate,setFromDate]=useState(firstOfMonthIso());
  const [toDate,setToDate]=useState(todayIso());
  const [appliedRange,setAppliedRange]=useState({from:firstOfMonthIso(),to:todayIso()});
  const [rows,setRows]=useState([]);
  const [loading,setLoading]=useState(false);
  const [downloading,setDownloading]=useState(false);
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

  return <div className="space-y-4">
    <div className="flex flex-col xl:flex-row xl:items-center xl:justify-between gap-3">
      <div className="flex items-center gap-3"><History className="h-8 w-8" style={{color:COLORS.dark}}/><div><h1 className="text-2xl font-bold" style={{color:COLORS.dark}}>Product Hub History</h1><p className="text-sm" style={{color:COLORS.muted}}>Check and download uploaded history by date range.</p></div></div>
      <div className="flex flex-wrap items-end gap-2 bg-white p-3 rounded-xl" style={{border:`1px solid ${COLORS.border}`}}>
        <label className="text-xs font-bold" style={{color:COLORS.muted}}>From Date<input type="date" value={fromDate} onChange={e=>setFromDate(e.target.value)} className="block mt-1 px-3 py-2 rounded-xl border bg-white font-normal text-sm"/></label>
        <label className="text-xs font-bold" style={{color:COLORS.muted}}>To Date<input type="date" value={toDate} onChange={e=>setToDate(e.target.value)} className="block mt-1 px-3 py-2 rounded-xl border bg-white font-normal text-sm"/></label>
        <Button onClick={search} className="gap-2" style={{backgroundColor:COLORS.primary,color:'#fff'}}><Search className="h-4 w-4"/>Search</Button>
        <Button onClick={clear} variant="outline" className="gap-2"><RotateCcw className="h-4 w-4"/>Clear</Button>
        <Button onClick={downloadFiltered} disabled={downloading} variant="outline" className="gap-2"><Download className="h-4 w-4"/>{downloading?'Downloading…':'Download Filtered'}</Button>
      </div>
    </div>
    <div className="overflow-x-auto rounded-2xl bg-white" style={{border:`1px solid ${COLORS.border}`}}><table className="w-full text-sm"><thead><tr style={{backgroundColor:COLORS.primary,color:'#fff'}}>{['Date','Brand','Dealer','Branch','Records','Total Qty','Total Value','Action'].map(x=><th key={x} className="p-3 text-left">{x}</th>)}</tr></thead><tbody>{!loading&&rows.length===0?<tr><td colSpan={8} className="p-5 text-center" style={{color:COLORS.muted}}>No history found for selected date range</td></tr>:rows.map((r,i)=><tr key={`${r.date_key}-${r.brand}-${r.dealer}-${r.branch}-${i}`} className="border-b" style={{backgroundColor:i%2?'#fff':COLORS.soft}}><td className="p-3">{r.date_key||'-'}</td><td className="p-3">{r.brand||'-'}</td><td className="p-3">{r.dealer||'-'}</td><td className="p-3">{r.branch||'-'}</td><td className="p-3">{r.records||0}</td><td className="p-3">{Number(r.total_available_qty||0).toLocaleString('en-IN')}</td><td className="p-3">₹{Number(r.total_value||0).toLocaleString('en-IN')}</td><td className="p-3"><Button onClick={()=>downloadRow(r)} variant="outline" className="gap-2"><Download className="h-4 w-4"/>Download</Button></td></tr>)}</tbody></table></div>
  </div>;
}
