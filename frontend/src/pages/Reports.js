import React, { useEffect, useMemo, useState } from 'react';
import axios from 'axios';
import { API, useAuth } from '../App.js';
import { toast } from 'sonner';
import { BarChart3, Boxes, Clock3, IndianRupee, ClipboardList, FileSpreadsheet, GitPullRequest, Building2, Warehouse, Network, ArrowLeftRight, CircleAlert, Download, X, Upload, Search, CheckCircle2 } from 'lucide-react';
import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, LineChart, Line, PieChart, Pie, Cell, Legend } from 'recharts';

const reports = [
  ['inventory','01','Inventory Report','Current active inventory with value, scope and dual aging.',Boxes],
  ['aging','02','Aging Report','Purchase and Sales aging with independent buckets.',Clock3],
  ['stock-value','03','Stock Value Report','Item-level stock value and management totals.',IndianRupee],
  ['order-summary','04','Order Summary','One row per saved Order Desk order.',ClipboardList],
  ['order-details','05','Order Details','Part-level order availability and reservation detail.',FileSpreadsheet],
  ['request-summary','06','Request Summary','One row per Parts Transfer Request.',GitPullRequest],
  ['request-details','07','Request Details','Part-level request lifecycle and values.',ClipboardList],
  ['branch-summary','08','Branch Summary','Inventory, order, request and missed opportunity KPIs.',Warehouse],
  ['dealer-summary','09','Dealer Summary','Consolidated dealer performance within role scope.',Building2],
  ['brand-summary','10','Brand Summary','Brand-level management consolidation.',Network],
  ['stock-movement','11','Stock Movement','Opening, additions, reductions and closing by aging.',ArrowLeftRight],
  ['missed-opportunity','12','Missed Opportunity','Unfulfilled requests where stock remains available now.',CircleAlert],
];
const today = new Date();
const isoLocal = d => {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${y}-${m}-${day}`;
};
const defaultFrom = isoLocal(new Date(today.getFullYear(), today.getMonth(), 1));
const defaultTo = isoLocal(today);
const historicalMax = isoLocal(new Date(today.getFullYear(), today.getMonth(), 0));
const fmt = n => new Intl.NumberFormat('en-IN',{maximumFractionDigits:2}).format(Number(n||0));
const money = n => `₹${fmt(n)}`;

function ScopeFilters({user, value, onChange, minDate, historical=false}) {
  const [masterOptions,setMasterOptions]=useState({brands:[],dealers:[],branches:[]});
  const [loadingOptions,setLoadingOptions]=useState(true);

  useEffect(() => {
    let active = true;
    const loadOptions = async () => {
      setLoadingOptions(true);
      try {
        // Reuse the existing NMTS scope master endpoint so Reports follows the
        // same Brand → Dealer → Branch relationships as DashboardLayout.
        const response = await axios.get(`${API}/scope/options`);
        if (active) {
          setMasterOptions({
            brands: Array.isArray(response.data?.brands) ? response.data.brands : [],
            dealers: Array.isArray(response.data?.dealers) ? response.data.dealers : [],
            branches: Array.isArray(response.data?.branches) ? response.data.branches : [],
          });
        }
      } catch (error) {
        if (active) {
          setMasterOptions({brands:[],dealers:[],branches:[]});
          toast.error(error.response?.data?.detail || 'Unable to load report scope options');
        }
      } finally {
        if (active) setLoadingOptions(false);
      }
    };
    loadOptions();
    return () => { active = false; };
  }, []);

  const nameOf = item => typeof item === 'string' ? item : (item?.name || '');
  const same = (a,b) => String(a||'').trim().toLocaleLowerCase() === String(b||'').trim().toLocaleLowerCase();
  const uniqueNames = items => [...new Set(items.map(nameOf).filter(Boolean))].sort((a,b)=>a.localeCompare(b));
  const selectedBrand = value.brand && value.brand !== 'All Brands' ? value.brand : '';
  const selectedDealer = value.dealer && value.dealer !== 'All Dealers' ? value.dealer : '';

  const dealers = uniqueNames(masterOptions.dealers.filter(item => {
    if (!selectedBrand) return true;
    if (typeof item === 'string') return true;
    const itemBrand = item.brand_name || item.brand || item.brandName;
    return !itemBrand || same(itemBrand, selectedBrand);
  }));
  const branches = uniqueNames(masterOptions.branches.filter(item => {
    if (typeof item === 'string') return true;
    const itemBrand = item.brand_name || item.brand || item.brandName;
    const itemDealer = item.dealer_name || item.dealer || item.dealerName;
    if (selectedBrand && itemBrand && !same(itemBrand, selectedBrand)) return false;
    if (selectedDealer && itemDealer && !same(itemDealer, selectedDealer)) return false;
    return true;
  }));
  const brands = uniqueNames(masterOptions.brands);

  const set=(k,v)=>{
    const next={...value,[k]:v};
    if(k==='brand') {
      next.dealer='All Dealers';
      next.branch='All Branches';
    } else if(k==='dealer') {
      next.branch='All Branches';
    }
    onChange(next);
  };
  const maxDate = historical ? historicalMax : defaultTo;
  return <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
    <label className="text-sm font-medium text-gray-700">From Date<input type="date" min={historical?undefined:minDate} max={maxDate} value={value.from_date} onChange={e=>set('from_date',e.target.value)} className="mt-1 w-full rounded-xl border px-3 py-2"/></label>
    <label className="text-sm font-medium text-gray-700">To Date<input type="date" min={historical?undefined:minDate} max={maxDate} value={value.to_date} onChange={e=>set('to_date',e.target.value)} className="mt-1 w-full rounded-xl border px-3 py-2"/></label>
    {user.role==='master' ? <>
      <label className="text-sm font-medium text-gray-700">Brand<select disabled={loadingOptions} value={value.brand} onChange={e=>set('brand',e.target.value)} className="mt-1 w-full rounded-xl border px-3 py-2 disabled:bg-gray-100"><option>All Brands</option>{brands.map(x=><option key={x} value={x}>{x}</option>)}</select></label>
      <label className="text-sm font-medium text-gray-700">Dealer<select disabled={loadingOptions} value={value.dealer} onChange={e=>set('dealer',e.target.value)} className="mt-1 w-full rounded-xl border px-3 py-2 disabled:bg-gray-100"><option>All Dealers</option>{dealers.map(x=><option key={x} value={x}>{x}</option>)}</select></label>
      <label className="text-sm font-medium text-gray-700">Branch<select disabled={loadingOptions} value={value.branch} onChange={e=>set('branch',e.target.value)} className="mt-1 w-full rounded-xl border px-3 py-2 disabled:bg-gray-100"><option>All Branches</option>{branches.map(x=><option key={x} value={x}>{x}</option>)}</select></label>
    </> : user.role==='admin' ? <>
      <label className="text-sm font-medium text-gray-700">Brand<input disabled value={user.brand||''} className="mt-1 w-full rounded-xl border bg-gray-100 px-3 py-2"/></label>
      <label className="text-sm font-medium text-gray-700">Dealer<input disabled value={user.group||''} className="mt-1 w-full rounded-xl border bg-gray-100 px-3 py-2"/></label>
      <label className="text-sm font-medium text-gray-700">Branch<select disabled={loadingOptions} value={value.branch} onChange={e=>set('branch',e.target.value)} className="mt-1 w-full rounded-xl border px-3 py-2 disabled:bg-gray-100"><option>All Branches</option>{branches.map(x=><option key={x} value={x}>{x}</option>)}</select></label>
    </> : <div className="md:col-span-2 rounded-xl bg-gray-50 p-3 text-sm"><b>Scope:</b> {user.brand} / {user.group} / {user.location}</div>}
  </div>
}
function ReportDrawer({report,user,onClose}){
  const [f,setF]=useState({from_date:defaultFrom,to_date:defaultTo,brand:user.role==='master'?'All Brands':user.brand,dealer:user.role==='master'?'All Dealers':user.group,branch:user.role==='user'?user.location:'All Branches'});
  const [busy,setBusy]=useState(false);
  const download=async()=>{setBusy(true);try{const r=await axios.get(`${API}/reports-center/export/${report[0]}`,{params:f,responseType:'blob'});const url=URL.createObjectURL(r.data);const a=document.createElement('a');a.href=url;a.download=`NMTS_${report[2].replaceAll(' ','_')}_${f.from_date}_${f.to_date}.xlsx`;a.click();URL.revokeObjectURL(url);toast.success(`${report[2]} downloaded`);}catch(e){toast.error(e.response?.data?.detail||'Report generation failed');}finally{setBusy(false)}};
  return <div className="fixed inset-0 z-50 flex justify-end bg-black/30"><div className="h-full w-full max-w-xl overflow-y-auto bg-white p-6 shadow-2xl">
    <div className="flex items-start justify-between"><div><span className="text-emerald-600 font-bold">{report[1]}</span><h2 className="text-2xl font-bold text-gray-900">{report[2]}</h2><p className="mt-1 text-sm text-gray-500">{report[3]}</p></div><button onClick={onClose} className="rounded-full p-2 hover:bg-gray-100"><X/></button></div>
    <div className="my-6 border-t"/><ScopeFilters user={user} value={f} onChange={setF} minDate={defaultFrom}/>
    <button disabled={busy} onClick={download} className="mt-8 flex w-full items-center justify-center gap-2 rounded-xl bg-emerald-600 px-4 py-3 font-semibold text-white disabled:opacity-60"><Download/>{busy?'Generating Excel...':'Excel Download'}</button>
  </div></div>
}

function ReportsTab({user}) { const [selected,setSelected]=useState(null); return <><div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-5">{reports.map(r=>{const Icon=r[4];return <button key={r[0]} onClick={()=>setSelected(r)} className="group rounded-2xl border bg-white p-5 text-left shadow-sm transition hover:-translate-y-1 hover:border-emerald-300 hover:shadow-lg"><div className="flex justify-between"><span className="text-3xl font-black text-gray-200 group-hover:text-emerald-200">{r[1]}</span><div className="rounded-xl bg-emerald-50 p-3 text-emerald-600"><Icon/></div></div><h3 className="mt-4 text-lg font-bold text-gray-900">{r[2]}</h3><p className="mt-2 min-h-10 text-sm text-gray-500">{r[3]}</p><span className="mt-4 inline-flex items-center gap-2 text-sm font-semibold text-emerald-600">Open / Download <Download size={16}/></span></button>})}</div>{selected&&<ReportDrawer report={selected} user={user} onClose={()=>setSelected(null)}/>}</> }

function Kpi({label,value,sub}){return <div className="rounded-2xl border bg-white p-5 shadow-sm"><p className="text-sm text-gray-500">{label}</p><p className="mt-2 text-2xl font-bold text-gray-900">{value}</p>{sub&&<p className="mt-1 text-xs text-gray-400">{sub}</p>}</div>}
function AnalyticsTab({user}){
 const [f,setF]=useState({from_date:defaultFrom,to_date:defaultTo,brand:user.role==='master'?'All Brands':user.brand,dealer:user.role==='master'?'All Dealers':user.group,branch:user.role==='user'?user.location:'All Branches'}); const [data,setData]=useState(null); const [loading,setLoading]=useState(false);
 const load=async()=>{setLoading(true);try{setData((await axios.get(`${API}/reports-center/analytics`,{params:f})).data)}catch(e){toast.error(e.response?.data?.detail||'Analytics failed')}finally{setLoading(false)}}; useEffect(()=>{let active=true;const initialLoad=async()=>{setLoading(true);try{const r=await axios.get(`${API}/reports-center/analytics`,{params:f});if(active)setData(r.data);}catch(e){if(active)toast.error(e.response?.data?.detail||'Analytics failed')}finally{if(active)setLoading(false)}};initialLoad();return()=>{active=false};},[]);
 return <div className="space-y-6"><div className="rounded-2xl border bg-white p-5"><ScopeFilters user={user} value={f} onChange={setF} minDate={defaultFrom}/><button onClick={load} className="mt-4 rounded-xl bg-emerald-600 px-5 py-2 font-semibold text-white">Apply Analytics Filters</button></div>{!loading&&data?<>
 <div className="grid grid-cols-2 lg:grid-cols-5 gap-4"><Kpi label="Current Line Items" value={fmt(data.stock.line_items)}/><Kpi label="Available Quantity" value={fmt(data.stock.quantity)}/><Kpi label="Current Stock Value" value={money(data.stock.value)}/><Kpi label="Avg Purchase Aging" value={`${fmt(data.stock.avg_purchase_aging)} days`}/><Kpi label="Avg Sales Aging" value={`${fmt(data.stock.avg_sales_aging)} days`}/></div>
 <div className="grid grid-cols-1 xl:grid-cols-2 gap-6"><ChartBox title="Aging Distribution – Quantity"><ResponsiveContainer width="100%" height={300}><BarChart data={data.aging}><CartesianGrid strokeDasharray="3 3"/><XAxis dataKey="bucket" fontSize={11}/><YAxis/><Tooltip/><Bar dataKey="quantity" fill="#059669"/></BarChart></ResponsiveContainer></ChartBox><ChartBox title="Aging Distribution – Value"><ResponsiveContainer width="100%" height={300}><PieChart><Pie data={data.aging} dataKey="value" nameKey="bucket" outerRadius={100} label>{data.aging.map((_,i)=><Cell key={i} fill={['#10b981','#34d399','#6ee7b7','#f59e0b','#ef4444'][i]}/>)}</Pie><Tooltip formatter={money}/><Legend/></PieChart></ResponsiveContainer></ChartBox></div>
 <div className="grid grid-cols-2 lg:grid-cols-4 gap-4"><Kpi label="Total Orders" value={fmt(data.orders.total)}/><Kpi label="Order Line Items" value={fmt(data.orders.line_items)}/><Kpi label="Requested Quantity" value={fmt(data.orders.requested_qty)}/><Kpi label="Order Value" value={money(data.orders.value)}/><Kpi label="Total Requests" value={fmt(data.requests.total)}/><Kpi label="Approved Quantity" value={fmt(data.requests.approved_qty)}/><Kpi label="Completed Transfer Value" value={money(data.requests.completed_value)}/><Kpi label="Missed Opportunity Value" value={money(data.missed.value)}/></div>
 <ChartBox title="Branch Comparison"><ResponsiveContainer width="100%" height={340}><BarChart data={data.branches}><CartesianGrid strokeDasharray="3 3"/><XAxis dataKey="name"/><YAxis/><Tooltip/><Legend/><Bar dataKey="stock_value" name="Stock Value" fill="#059669"/><Bar dataKey="missed_value" name="Missed Opportunity" fill="#ef4444"/></BarChart></ResponsiveContainer></ChartBox>
 <ChartBox title="Current-Month Daily Trend"><ResponsiveContainer width="100%" height={320}><LineChart data={data.daily}><CartesianGrid strokeDasharray="3 3"/><XAxis dataKey="date"/><YAxis/><Tooltip/><Legend/><Line type="monotone" dataKey="orders" stroke="#059669"/><Line type="monotone" dataKey="requests" stroke="#2563eb"/><Line type="monotone" dataKey="completed" stroke="#f59e0b"/></LineChart></ResponsiveContainer></ChartBox>
 </>:<div className="rounded-2xl border bg-white p-12 text-center text-gray-500">Apply filters to load analytics.</div>}</div>
}
function ChartBox({title,children}){return <div className="rounded-2xl border bg-white p-5 shadow-sm"><h3 className="mb-4 font-bold text-gray-900">{title}</h3>{children}</div>}

function OldReportsTab({user}){
 const [form,setForm]=useState({report_type:'Inventory Report',from_date:'',to_date:'',brand:user.role==='master'?'All Brands':user.brand,dealer:user.role==='master'?'All Dealers':user.group,branch:user.role==='user'?user.location:'All Branches',aging_type:'Both',required_format:'Excel',reason:''}); const [rows,setRows]=useState([]); const [busy,setBusy]=useState(false); const [file,setFile]=useState(null); const [manage,setManage]=useState(null);
 const load=async()=>{try{const r=await axios.get(`${API}/reports-center/old-requests`);setRows(r.data.records||[]);}catch(e){toast.error(e.response?.data?.detail||'Unable to load old report requests')}}; useEffect(()=>{let active=true;const initialLoad=async()=>{try{const r=await axios.get(`${API}/reports-center/old-requests`);if(active)setRows(r.data.records||[]);}catch(e){if(active)toast.error(e.response?.data?.detail||'Unable to load old report requests')}};initialLoad();return()=>{active=false};},[]);
 const submit=async()=>{setBusy(true);try{await axios.post(`${API}/reports-center/old-requests`,form);toast.success('Old Report Request submitted');load()}catch(e){toast.error(e.response?.data?.detail||'Request failed')}finally{setBusy(false)}};
 const patch=async(status)=>{const remarks=manage?.admin_remarks||'';try{await axios.patch(`${API}/reports-center/old-requests/${manage.request_number}`,{status,admin_remarks:remarks});toast.success('Status updated');setManage(null);load()}catch(e){toast.error(e.response?.data?.detail||'Update failed')}};
 const upload=async()=>{if(!file)return toast.error('Select an Excel or ZIP file');const fd=new FormData();fd.append('file',file);fd.append('admin_remarks',manage?.admin_remarks||'');try{await axios.post(`${API}/reports-center/old-requests/${manage.request_number}/upload`,fd);toast.success('Historical report uploaded');setManage(null);setFile(null);load()}catch(e){toast.error(e.response?.data?.detail||'Upload failed')}};
 const download=async(r)=>{try{const x=await axios.get(`${API}/reports-center/old-requests/${r.request_number}/download`,{responseType:'blob'});const u=URL.createObjectURL(x.data),a=document.createElement('a');a.href=u;a.download=r.file_name||`${r.request_number}.xlsx`;a.click();URL.revokeObjectURL(u);load()}catch(e){toast.error(e.response?.data?.detail||'Download failed')}};
 return <div className="space-y-6"><div className="rounded-2xl border bg-white p-6"><h3 className="text-xl font-bold">Request Old Report</h3><div className="mt-5 grid grid-cols-1 md:grid-cols-2 gap-4"><label className="text-sm font-medium">Report Type<select value={form.report_type} onChange={e=>setForm({...form,report_type:e.target.value})} className="mt-1 w-full rounded-xl border px-3 py-2">{[...reports.map(r=>r[2]),'Complete Historical Report'].map(x=><option key={x}>{x}</option>)}</select></label><ScopeFilters user={user} value={form} onChange={setForm} historical/><label className="text-sm font-medium">Aging Type<select value={form.aging_type} onChange={e=>setForm({...form,aging_type:e.target.value})} className="mt-1 w-full rounded-xl border px-3 py-2"><option>Purchase Aging</option><option>Sales Aging</option><option>Both</option></select></label><label className="text-sm font-medium">Required Format<select value={form.required_format} onChange={e=>setForm({...form,required_format:e.target.value})} className="mt-1 w-full rounded-xl border px-3 py-2"><option>Excel</option><option>ZIP</option><option>Excel + Supporting Files if needed</option></select></label><label className="md:col-span-2 text-sm font-medium">Reason / Remarks<textarea value={form.reason} onChange={e=>setForm({...form,reason:e.target.value})} className="mt-1 w-full rounded-xl border px-3 py-2" rows="3"/></label></div><button disabled={busy} onClick={submit} className="mt-4 rounded-xl bg-emerald-600 px-5 py-3 font-semibold text-white">{busy?'Submitting...':'Submit Old Report Request'}</button></div>
 <div className="rounded-2xl border bg-white overflow-hidden"><div className="p-5"><h3 className="text-xl font-bold">{user.role==='master'?'Historical Report Request Management':'My Report Requests'}</h3></div><div className="overflow-x-auto"><table className="min-w-full text-sm"><thead className="bg-gray-50"><tr>{['Request Number','Report','Period','Scope','Status','File','Expiry','Action'].map(x=><th className="px-4 py-3 text-left" key={x}>{x}</th>)}</tr></thead><tbody>{rows.map(r=><tr className="border-t" key={r.request_number}><td className="px-4 py-3 font-semibold">{r.request_number}</td><td className="px-4 py-3">{r.report_type}</td><td className="px-4 py-3">{r.from_date} → {r.to_date}</td><td className="px-4 py-3">{[r.scope?.brand,r.scope?.dealer,r.scope?.branch].filter(Boolean).join(' / ')}</td><td className="px-4 py-3"><span className="rounded-full bg-emerald-50 px-2 py-1 text-emerald-700">{r.status}</span></td><td className="px-4 py-3">{r.file_name||'—'}</td><td className="px-4 py-3">{r.expiry_date?String(r.expiry_date).slice(0,10):'—'}</td><td className="px-4 py-3 flex gap-2">{r.status==='Ready for Download'&&<button onClick={()=>download(r)} className="rounded-lg bg-emerald-600 px-3 py-1.5 text-white">Download</button>}{user.role==='master'&&<button onClick={()=>setManage(r)} className="rounded-lg border px-3 py-1.5">Manage</button>}</td></tr>)}{!rows.length&&<tr><td colSpan="8" className="p-10 text-center text-gray-500">No report requests found.</td></tr>}</tbody></table></div></div>
 {manage&&<div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"><div className="w-full max-w-lg rounded-2xl bg-white p-6"><div className="flex justify-between"><h3 className="text-xl font-bold">Manage {manage.request_number}</h3><button onClick={()=>setManage(null)}><X/></button></div><textarea placeholder="Admin remarks / rejection reason" value={manage.admin_remarks||''} onChange={e=>setManage({...manage,admin_remarks:e.target.value})} className="mt-4 w-full rounded-xl border p-3"/><input type="file" accept=".xlsx,.xls,.zip" onChange={e=>setFile(e.target.files[0])} className="mt-4 w-full rounded-xl border p-3"/><div className="mt-4 flex flex-wrap gap-2"><button onClick={()=>patch('Under Review')} className="rounded-lg border px-3 py-2">Under Review</button><button onClick={()=>patch('Processing')} className="rounded-lg border px-3 py-2">Processing</button><button onClick={()=>patch('Rejected')} className="rounded-lg bg-red-600 px-3 py-2 text-white">Reject</button><button onClick={upload} className="rounded-lg bg-emerald-600 px-3 py-2 text-white flex gap-2"><Upload size={18}/>Upload & Ready</button></div></div></div>}</div>
}

export function Reports(){const {user}=useAuth();const [tab,setTab]=useState('reports');return <div className="space-y-6" data-testid="reports-center"><div className="rounded-2xl bg-gradient-to-r from-emerald-600 to-emerald-400 p-6 text-white"><div className="flex items-center gap-3"><BarChart3 size={34}/><div><h1 className="text-2xl font-bold">Reports Center</h1><p className="text-emerald-50">Current-month reports, management analytics and secure historical report requests</p></div></div></div><div className="flex gap-2 rounded-2xl border bg-white p-2">{[['reports','Reports'],['analytics','Analytics'],['old','Old Reports']].map(([k,n])=><button key={k} onClick={()=>setTab(k)} className={`flex-1 rounded-xl px-4 py-3 font-semibold ${tab===k?'bg-emerald-600 text-white':'text-gray-600 hover:bg-gray-50'}`}>{n}</button>)}</div>{tab==='reports'?<ReportsTab user={user}/>:tab==='analytics'?<AnalyticsTab user={user}/>:<OldReportsTab user={user}/>}</div>}
