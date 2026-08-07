import React, { useEffect, useMemo, useRef, useState } from 'react';
import { useOutletContext } from 'react-router-dom';
import axios from 'axios';
import { API, useAuth } from '../App';
import { Smartphone, Download, Ban, Trash2, ScanLine, Copy, ClipboardCheck, Camera, History, Eye, Plus, Upload, X, RefreshCw, Settings2, QrCode, CheckCircle2, ArrowRightLeft } from 'lucide-react';
import { Button } from '../components/ui/button';
import { toast } from 'sonner';
import { NmtsConfirmDialog } from '../components/NmtsConfirmDialog';

const clean = (v) => String(v || '').trim();
const exact = (v) => clean(v) && clean(v) !== 'N/A' && !clean(v).startsWith('All ');
const money = (v) => Number(v || 0).toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const fmt = (v) => {
  if (!v) return '-';

  const value = String(v);
  const hasTimezone = /Z$|[+-]\d{2}:\d{2}$/.test(value);
  const utcValue = hasTimezone ? value : `${value}Z`;

  return new Intl.DateTimeFormat('en-IN', {
    timeZone: 'Asia/Kolkata',
    dateStyle: 'medium',
    timeStyle: 'short',
  }).format(new Date(utcValue));
};
const todayKey = (v=new Date()) => new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Kolkata',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date(v));

export default function NMTSMobile({ variant = 'mobile' }){
  const showMobileSections = variant !== 'audit';
  const showAuditSections = variant !== 'mobile';
  const { user } = useAuth();
  const { scopeBrand, scopeDealer, scopeBranch } = useOutletContext();
  const brand=clean(scopeBrand), dealer=clean(scopeDealer), branch=clean(scopeBranch);
  const scopeReady=exact(brand)&&exact(dealer)&&exact(branch);
  const canManage=['master','admin'].includes(user?.role);
  const isMaster = user?.role==='master';
  const canManageMobileAccount = ['master','admin','user'].includes(user?.role);
  const [section,setSection]=useState(showAuditSections ? 'physical' : 'mobile');

  // ==================== Mobile User Management (new model) ====================
  const [mobileUsers,setMobileUsers]=useState([]);
  const [todayAttendance,setTodayAttendance]=useState({});
  const [deleteTarget,setDeleteTarget]=useState(null);
  const [deleteBusy,setDeleteBusy]=useState(false);
  const [apkUploading,setApkUploading]=useState(false);
  const apkFileRef=useRef(null);
  const [autoSummary,setAutoSummary]=useState(null);
  const [autoAssignments,setAutoAssignments]=useState([]);
  const [userPerformance,setUserPerformance]=useState([]);
  const [historyRecords,setHistoryRecords]=useState([]);
  const [autoGenerating,setAutoGenerating]=useState(false);
  const [autoRecalcBusy,setAutoRecalcBusy]=useState(false);
  const [creatingUser,setCreatingUser]=useState(false);
  const [pairingFor,setPairingFor]=useState(null);
  const [pairingResult,setPairingResult]=useState(null);

  const [branchMoveTarget, setBranchMoveTarget] = useState(null);
  const [branchMoveBusy, setBranchMoveBusy] = useState(false);
  const [notificationInterval,setNotificationInterval]=useState(30);
  const [savingInterval,setSavingInterval]=useState(false);
  const [appVersions,setAppVersions]=useState([]);
  const [apkDownloadUrl,setApkDownloadUrl]=useState('');

  // ==================== Perpetual Stock + History (unchanged existing feature) ====================
  const [partNumber,setPartNumber]=useState('');
  const [snapshot,setSnapshot]=useState(null);
  const [physicalQty,setPhysicalQty]=useState('');
  const [physicalLocation,setPhysicalLocation]=useState('');
  const [remarks,setRemarks]=useState('');
  const [pending,setPending]=useState([]);
  const [sessions,setSessions]=useState([]);
  const [viewSession,setViewSession]=useState(null);
  const [filters,setFilters]=useState({date_from:'',date_to:'',month:'',user_filter:'',verification_type:'all',result_filter:'all',part_number:'',loc:''});
  const cameraRef=useRef(null);

  const loadMobileUsers=async()=>{
    try{
      const params = isMaster ? {brand_name:brand,dealer_name:dealer,branch} : (user?.role==='admin' ? {branch} : {});
      const r=await axios.get(`${API}/mobile/users`,{params});
      setMobileUsers(r.data||[]);
    }catch(e){toast.error(e.response?.data?.detail||'Mobile user list load failed')}
  };  const loadTodayAttendance=async()=>{
    if(!scopeReady) { setTodayAttendance({}); return; }
    try{
      const r=await axios.get(`${API}/mobile/users/attendance/today`,{params:{brand_name:brand,dealer_name:dealer,branch}});
      setTodayAttendance(r.data?.records||{});
    }catch{ setTodayAttendance({}); }
  };
  const loadAutoPerpetual=async()=>{
    if(!scopeReady){ setAutoSummary(null); setAutoAssignments([]); return; }
    try{
      const [s,a,p]=await Promise.all([
        axios.get(`${API}/mobile/auto-perpetual/summary`,{params:{brand_name:brand,dealer_name:dealer,branch}}),
        axios.get(`${API}/mobile/auto-perpetual/assignments/today`,{params:{brand_name:brand,dealer_name:dealer,branch}}),
        axios.get(`${API}/mobile/auto-perpetual/user-performance`,{params:{brand_name:brand,dealer_name:dealer,branch}}),
      ]);
      setAutoSummary(s.data||null);
      setAutoAssignments(a.data||[]);
      setUserPerformance(p.data||[]);
    }catch(e){ setAutoSummary(null); setAutoAssignments([]); setUserPerformance([]); }
  };
  const loadHistoryRecords=async()=>{
    if(!scopeReady){ setHistoryRecords([]); return; }
    try{
      const params={brand,dealer,branch,...filters, limit:500};
      Object.keys(params).forEach(k=>{ if(params[k]===''||params[k]==='all') delete params[k]; });
      const r=await axios.get(`${API}/mobile/perpetual-stock/verification-history`,{params});
      setHistoryRecords(r.data||[]);
    }catch(e){ toast.error(e.response?.data?.detail||'History load failed'); setHistoryRecords([]); }
  };
  const loadApkLink=async()=>{
    try{
      const r=await axios.get(`${API}/mobile/app-versions/download-link/latest`);
      setApkDownloadUrl(r.data?.download_url||'');
    }catch{ setApkDownloadUrl(''); }
  };
  const loadNotificationInterval=async()=>{
    try{const r=await axios.get(`${API}/mobile/settings/notification-interval`);setNotificationInterval(r.data?.interval_minutes||30);}catch(e){/* non-blocking */}
  };
  const loadAppVersions=async()=>{
    try{const r=await axios.get(`${API}/mobile/app-versions`);setAppVersions(r.data||[]);}catch{/* non-blocking */}
    loadApkLink();
  };

  const loadSessions=async()=>{if(!scopeReady){setSessions([]);return;}try{const r=await axios.get(`${API}/mobile/perpetual-stock/sessions`,{params:{brand,dealer,branch,date_from:filters.date_from||undefined,date_to:filters.date_to||undefined}});setSessions(r.data||[]);}catch(e){toast.error(e.response?.data?.detail||'Verification history load failed')}};

  useEffect(()=>{
    setPairingResult(null);setPairingFor(null);setPending([]);setSnapshot(null);
    loadMobileUsers();loadNotificationInterval();loadAppVersions();loadTodayAttendance();
    if (showAuditSections) { loadSessions(); loadAutoPerpetual(); loadHistoryRecords(); }
    /* eslint-disable-next-line */
  },[scopeBrand,scopeDealer,scopeBranch,user?.id, showAuditSections]);

  const generateNewPairing=async()=>{
    if(!scopeReady) return toast.error('Select an exact Brand, Dealer and Branch in the Dashboard filter first');
    setCreatingUser(true);
    try{
      const payload = {pairing_type:'NEW',brand_name:brand,dealer_name:dealer,branch};
      const r=await axios.post(`${API}/mobile/pairing/generate`,payload);
      setPairingResult(r.data);
      toast.success('New-user pairing QR generated — valid for 10 minutes');
    }catch(e){const d=e.response?.data?.detail;toast.error(typeof d==='string'?d:(d?.message||'Pairing code generation failed'))}
    finally{setCreatingUser(false)}
  };

  const toggleUserStatus=async(mu)=>{
    try{
      await axios.put(`${API}/mobile/users/${mu.mobile_user_id}/status`,{status: mu.status==='active'?'inactive':'active'});
      toast.success(`${mu.mobile_user_id} set to ${mu.status==='active'?'inactive':'active'}`);
      loadMobileUsers();loadTodayAttendance();
    }catch(e){toast.error(e.response?.data?.detail||'Status update failed')}
  };

  const setDailyAttendance=async(mu,status)=>{
    try{
      await axios.put(`${API}/mobile/users/${mu.mobile_user_id}/attendance`,{status});
      toast.success(`${mu.mobile_user_id} marked ${status} for today (Auto Perpetual)`);
      loadTodayAttendance();
    }catch(e){toast.error(e.response?.data?.detail||'Attendance update failed')}
  };

  const deleteMobileUser=async()=>{
    const mu=deleteTarget;
    if(!mu||deleteBusy) return;
    setDeleteBusy(true);
    try{
      await axios.delete(`${API}/mobile/users/${mu.mobile_user_id}`);
      toast.success('Mobile user archived');
      setDeleteTarget(null);
      loadMobileUsers();loadTodayAttendance();
    }catch(e){toast.error(e.response?.data?.detail||'Delete failed')}
    finally{setDeleteBusy(false)}
  };

  const uploadApk=async(e)=>{
    const file=e.target.files?.[0];
    e.target.value='';
    if(!file) return;
    if(!file.name.toLowerCase().endsWith('.apk')) return toast.error('Only .apk files are allowed');
    setApkUploading(true);
    try{
      const form=new FormData();
      form.append('file',file);
      await axios.post(`${API}/mobile/app-versions/upload`,form,{headers:{'Content-Type':'multipart/form-data'}});
      toast.success('APK uploaded');
      loadAppVersions();
    }catch(err){toast.error(err.response?.data?.detail||'APK upload failed')}
    finally{setApkUploading(false)}
  };

  const downloadApk=async()=>{
    try{
      const r=await axios.get(`${API}/mobile/app-versions/download/latest`,{responseType:'blob'});
      const url=URL.createObjectURL(r.data);
      const a=document.createElement('a');
      a.href=url;
      a.download=(appVersions[0]?.apk_filename)||'sleeping-stock.apk';
      a.click();
      URL.revokeObjectURL(url);
    }catch(e){toast.error(e.response?.data?.detail||'Download failed')}
  };

  const copyApkLink=async()=>{
    try{
      let link=apkDownloadUrl;
      if(!link){
        const r=await axios.get(`${API}/mobile/app-versions/download-link/latest`);
        link=r.data?.download_url||'';
        setApkDownloadUrl(link);
      }
      if(!link) return toast.error('No download link available');
      await navigator.clipboard.writeText(link);
      toast.success('Download link copied');
    }catch(e){toast.error('Could not copy link')}
  };

  const generateAutoPerpetual=async(recalc=false)=>{
    if(!scopeReady) return toast.error('Select exact Brand, Dealer and Branch');
    if(recalc) setAutoRecalcBusy(true); else setAutoGenerating(true);
    try{
      const r=await axios.post(`${API}/mobile/auto-perpetual/generate`,null,{params:{brand_name:brand,dealer_name:dealer,branch,recalc_pending:recalc}});
      if(r.data?.duplicate) toast.info('Auto Perpetual already generated for today');
      else toast.success(`Assigned ${r.data?.assignments_created||0} line items to ${r.data?.active_users||0} active users`);
      loadAutoPerpetual();
    }catch(e){toast.error(e.response?.data?.detail||'Generate failed')}
    finally{ setAutoGenerating(false); setAutoRecalcBusy(false); }
  };

  const generatePairing=async(mu)=>{
    setPairingFor(mu.mobile_user_id);
    try{
      const r=await axios.post(`${API}/mobile/pairing/generate`,{pairing_type:'REPAIR',mobile_user_id:mu.mobile_user_id,brand_name:mu.brand_name,dealer_name:mu.dealer_name,branch:mu.branch});
      setPairingResult({mobile_user_id:mu.mobile_user_id,...r.data});
      toast.success('Re-pair QR generated — same Mobile User ID will be reused');
    }catch(e){const d=e.response?.data?.detail;toast.error(typeof d==='string'?d:(d?.message||'Re-pair code generation failed'))}
    finally{setPairingFor(null)}
  };

  const performChangeBranch = async () => {
    const mu = branchMoveTarget;
    if (!mu || branchMoveBusy) return;
    if (!scopeReady) return toast.error('Select the new Brand / Dealer / Branch in the Dashboard filter first');
    setBranchMoveBusy(true);
    try {
      const r = await axios.put(`${API}/mobile/users/${mu.mobile_user_id}/branch`, { brand_name: brand, dealer_name: dealer, branch });
      toast.success(r.data?.message || 'Branch updated');
      setBranchMoveTarget(null);
      loadMobileUsers();
      loadTodayAttendance();
    }catch(e){const d=e.response?.data?.detail;toast.error(typeof d==='string'?d:(d?.message||'Branch change failed'))}
    finally { setBranchMoveBusy(false); }
  };

  const changeBranch = (mu) => {
    if (!scopeReady) return toast.error('Select the new Brand / Dealer / Branch in the Dashboard filter first');
    setBranchMoveTarget(mu);
  };

  const saveNotificationInterval=async()=>{
    setSavingInterval(true);
    try{await axios.put(`${API}/mobile/settings/notification-interval`,{interval_minutes:Number(notificationInterval)});toast.success('Notification interval updated')}
    catch(e){toast.error(e.response?.data?.detail||'Update failed')}
    finally{setSavingInterval(false)}
  };

  const lookup=async()=>{if(!scopeReady)return toast.error('Select exact Dashboard Brand, Dealer and Branch');if(!clean(partNumber))return toast.error('Enter Part Number');try{const r=await axios.get(`${API}/mobile/perpetual-stock/lookup`,{params:{part_number:clean(partNumber),brand,dealer,branch}});setSnapshot(r.data);setPhysicalLocation(r.data.pin_location||'');setPhysicalQty('');setRemarks('')}catch(e){setSnapshot(null);toast.error(e.response?.data?.detail||'Part not found')}};
  const scan=async(e)=>{const file=e.target.files?.[0];e.target.value='';if(!file)return;if(!('TextDetector'in window))return toast.info('OCR is not supported in this browser. Enter Part Number manually.');try{const bitmap=await createImageBitmap(file);const result=await new window.TextDetector().detect(bitmap);const candidates=result.map(x=>x.rawValue).join(' ').match(/[A-Z0-9][A-Z0-9\-_/]{4,}/gi)||[];if(!candidates.length)return toast.error('Part Number not detected');setPartNumber(candidates.sort((a,b)=>b.length-a.length)[0])}catch{toast.error('OCR scan failed')}};
  const calc=useMemo(()=>{if(!snapshot||physicalQty==='')return null;const sys=Number(snapshot.system_quantity||0),phy=Number(physicalQty),mav=Number(snapshot.mav||0),diff=phy-sys;return{difference:diff,shortage_qty:diff<0?-diff:0,excess_qty:diff>0?diff:0,shortage_value:(diff<0?-diff:0)*mav,excess_value:(diff>0?diff:0)*mav}},[snapshot,physicalQty]);
  const addItem=()=>{if(!snapshot||!calc||Number.isNaN(Number(physicalQty)))return toast.error('Enter valid Physical Quantity');if(pending.some(x=>clean(x.part_number).toLowerCase()===clean(snapshot.part_number).toLowerCase()))return toast.error('Part already added');setPending(p=>[...p,{...snapshot,physical_quantity:Number(physicalQty),scanned_location:physicalLocation,remarks,...calc}]);setPartNumber('');setSnapshot(null);setPhysicalQty('');setPhysicalLocation('');setRemarks('');toast.success('Added to temporary verification list')};
  const uploadSession=async()=>{if(!pending.length)return toast.error('Add at least one item');try{const r=await axios.post(`${API}/mobile/perpetual-stock/sessions`,{brand,dealer,branch,items:pending.map(x=>({part_number:x.part_number,physical_quantity:x.physical_quantity,scanned_location:x.scanned_location,remarks:x.remarks}))});toast.success(`Uploaded ${r.data.session_id}`);setPending([]);loadSessions()}catch(e){toast.error(e.response?.data?.detail||'Upload failed')}};
  const openSession=async(s)=>{try{const r=await axios.get(`${API}/mobile/perpetual-stock/sessions/${s.session_id}`);setViewSession(r.data)}catch(e){toast.error(e.response?.data?.detail||'Session load failed')}};
  const updateCorrection=async(row,method)=>{try{const status=method==='no_action'?'pending':'corrected';const r=await axios.put(`${API}/mobile/perpetual-stock/${row.id}/correction`,{correction_status:status,correction_method:method,correction_remarks:''});setViewSession(v=>({...v,items:(v.items||[]).map(x=>x.id===row.id?r.data:x)}));toast.success('Correction updated')}catch(e){toast.error(e.response?.data?.detail||'Correction update failed')}};
  const excel=async(s)=>{try{const r=await axios.get(`${API}/mobile/perpetual-stock/sessions/${s.session_id}/excel`,{responseType:'blob'});const url=URL.createObjectURL(r.data);const a=document.createElement('a');a.href=url;a.download=`${s.session_id}.xlsx`;a.click();URL.revokeObjectURL(url)}catch(e){toast.error('Excel download failed')}};
  const exportAllExcel=async()=>{try{const params={date_from:filters.date_from||undefined,date_to:filters.date_to||undefined};if(exact(brand))params.brand=brand;if(exact(dealer))params.dealer=dealer;if(exact(branch))params.branch=branch;const r=await axios.get(`${API}/mobile/perpetual-stock/export-all/excel`,{params,responseType:'blob'});const url=URL.createObjectURL(r.data);const a=document.createElement('a');a.href=url;a.download=user?.role==='master'?'perpetual_stock_master.xlsx':user?.role==='admin'?'perpetual_stock_admin.xlsx':'perpetual_stock_branch.xlsx';a.click();URL.revokeObjectURL(url);toast.success('Complete Perpetual Stock Excel downloaded')}catch(e){toast.error(e.response?.data?.detail||'Complete Excel download failed')}};
  const todayItems=useMemo(()=>sessions.filter(s=>todayKey(s.created_at)===todayKey()).reduce((n,s)=>n+Number(s.total_items||0),0),[sessions]);

  const latestVersion = appVersions[0];

  return <div className="space-y-6" data-testid={variant === 'audit' ? 'stock-audit-page' : 'nmts-mobile-page'}>
    {showMobileSections && (
      <>
    <div className="grid md:grid-cols-2 gap-3 max-w-xl"><Tab active={section==='mobile'} onClick={()=>setSection('mobile')} icon={Smartphone} title="Mobile Users"/><Tab active={section==='settings'} onClick={()=>setSection('settings')} icon={Settings2} title="App &amp; Settings"/></div>
    {!scopeReady && user?.role!=='user' && <div className="border border-amber-300 bg-amber-50 p-3 rounded-lg text-amber-800">Select an exact Brand, Dealer and Branch in the Dashboard filter. Mobile User creation and pairing use only that selected scope.</div>}
      </>
    )}

    {showAuditSections && (
      <>
    <div className="grid md:grid-cols-3 gap-3"><Tab active={section==='physical'} onClick={()=>setSection('physical')} icon={ClipboardCheck} title="Physical Perpetual"/><Tab active={section==='auto'} onClick={()=>setSection('auto')} icon={Camera} title="Auto Perpetual"/><Tab active={section==='history'} onClick={()=>setSection('history')} icon={History} title="Verification History"/></div>
    {!scopeReady && user?.role!=='user' && <div className="border border-gray-200 bg-gray-50 p-3 rounded-lg text-gray-700 text-sm">Select an exact Brand, Dealer and Branch in the top header filters for perpetual stock verification.</div>}
      </>
    )}

    {showMobileSections && section==='mobile'&&<div className="space-y-5">
      {canManage && <Card title="Pair a New Mobile User"><div className="flex flex-col md:flex-row md:items-center md:justify-between gap-3">
        <div><p className="font-medium">Generate one-time QR for the selected Brand / Dealer / Branch.</p><p className="text-xs text-gray-500 mt-1">The Mobile User ID is created only after the user enters Name + Mobile Number and scans this QR.</p></div>
        <Button onClick={generateNewPairing} disabled={creatingUser||!scopeReady}><QrCode className="h-4 w-4 mr-2"/>{creatingUser?'Generating...':'Generate New Pairing QR'}</Button>
      </div></Card>}

      {pairingResult && <div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/75 backdrop-blur-sm p-4" onMouseDown={()=>setPairingResult(null)}>
        <div className="relative w-full max-w-[720px] overflow-hidden rounded-[28px] border border-white/20 bg-gradient-to-b from-[#1b1d1c] to-[#080a09] p-6 md:p-8 text-white shadow-2xl" onMouseDown={e=>e.stopPropagation()}>
          <button aria-label="Close pairing QR" onClick={()=>setPairingResult(null)} className="absolute right-5 top-5 rounded-full border border-white/30 bg-black/30 p-2 hover:bg-white/10"><X className="h-6 w-6"/></button>
          <div className="flex items-center justify-center gap-4 pr-10">
            <img src="/sleeping-stock-logo-transparent.png" alt="Sleeping Stock" className="h-20 w-20 object-contain"/>
            <div><div className="text-3xl font-black tracking-tight"><span className="text-white">Sleeping</span><span className="text-lime-400">Stock</span></div><div className="text-sm text-gray-300">Non moving Tracking System</div></div>
          </div>
          <div className="mt-5 text-center"><h2 className="text-3xl md:text-4xl font-black">{pairingResult.pairing_type==='REPAIR'?'Re-pair':'Pair'} Your <span className="text-lime-400">Device</span></h2><p className="mt-2 text-sm md:text-base text-gray-300">{pairingResult.pairing_type==='REPAIR'?`Existing ID ${pairingResult.mobile_user_id} will be reused.`:'Name and mobile number are entered in the app.'}</p></div>
          <div className="mx-auto mt-6 w-fit rounded-[24px] border border-lime-400/70 bg-white p-4 shadow-[0_0_35px_rgba(163,230,53,0.35)]">
            {pairingResult.qr_code_data_url ? <img src={pairingResult.qr_code_data_url} alt="Device pairing QR code" className="h-[300px] w-[300px] md:h-[380px] md:w-[380px] image-render-pixel"/> : <div className="flex h-[300px] w-[300px] items-center justify-center text-black">QR image unavailable</div>}
          </div>
          <div className="mt-6 rounded-2xl border border-white/15 bg-white/5 p-4 md:p-5">
            <div className="text-sm text-gray-300">Manual Pairing Code</div>
            <div className="mt-1 flex items-center justify-between gap-3"><span className="break-all font-mono text-2xl md:text-4xl font-black tracking-widest text-white">{pairingResult.pairing_code}</span><button className="shrink-0 rounded-xl border border-lime-400/40 p-3 text-lime-400 hover:bg-lime-400/10" onClick={()=>{navigator.clipboard.writeText(pairingResult.pairing_code);toast.success('Pairing code copied')}}><Copy className="h-6 w-6"/></button></div>
          </div>
          <div className="mt-4 text-center text-sm text-gray-400">One-time code · Expires {fmt(pairingResult.expires_at)}</div>
        </div>
      </div>}

      <Card title="Mobile Users">
        <p className="text-xs text-gray-500 mb-3">Mark <b>Today (Auto)</b> Active/Inactive before Generate Auto Perpetual. Account Active/Inactive controls login access.</p>
        <div className="overflow-x-auto"><table className="w-full text-sm min-w-[1200px]"><thead><tr>{['Mobile User ID','Name','Mobile','Brand','Dealer','Branch','Account','Today (Auto)','Last Active','Actions'].map(h=><th key={h} className="text-left p-3">{h}</th>)}</tr></thead><tbody>
        {mobileUsers.map(mu=>{
          const att=todayAttendance[mu.mobile_user_id];
          const attLabel=att==='active'?'Active':att==='inactive'?'Inactive':'Not set';
          return <tr className="border-t" key={mu.mobile_user_id}>
          <td className="p-3 font-mono">{mu.mobile_user_id}</td><td>{mu.name}</td><td>{mu.mobile_number}</td><td>{mu.brand_name}</td><td>{mu.dealer_name}</td><td>{mu.branch}</td>
          <td><span className={`px-2 py-1 rounded text-xs ${mu.status==='active'?'bg-green-100 text-green-700':'bg-gray-200 text-gray-600'}`}>{mu.status}</span></td>
          <td><span className={`px-2 py-1 rounded text-xs ${att==='active'?'bg-emerald-100 text-emerald-800':att==='inactive'?'bg-amber-100 text-amber-800':'bg-gray-100 text-gray-600'}`}>{attLabel}</span></td>
          <td>{mu.last_active_at?fmt(mu.last_active_at):'-'}</td>
          <td><div className="flex gap-2 flex-wrap">
            {canManage && <Button size="sm" variant="outline" title="Re-pair QR" onClick={()=>generatePairing(mu)} disabled={pairingFor===mu.mobile_user_id || mu.status!=='active'}><RefreshCw className="h-4 w-4"/></Button>}
            {canManage && <Button size="sm" variant="outline" title="Change branch" onClick={()=>changeBranch(mu)} disabled={!scopeReady}><ArrowRightLeft className="h-4 w-4"/></Button>}
            {canManageMobileAccount && mu.status!=='deleted' && <>
              <Button size="sm" variant="outline" title="Account active/inactive" onClick={()=>toggleUserStatus(mu)}>{mu.status==='active'?<Ban className="h-4 w-4"/>:<CheckCircle2 className="h-4 w-4"/>}</Button>
              <Button size="sm" variant={att==='active'?'default':'outline'} title="Present for Auto today" onClick={()=>setDailyAttendance(mu,'active')}>Present</Button>
              <Button size="sm" variant={att==='inactive'?'secondary':'outline'} title="Absent for Auto today" onClick={()=>setDailyAttendance(mu,'inactive')}>Absent</Button>
            </>}
            {isMaster && mu.status!=='deleted' && <Button size="sm" variant="outline" title="Delete (archive)" onClick={()=>setDeleteTarget(mu)}><Trash2 className="h-4 w-4 text-red-600"/></Button>}
          </div></td>
        </tr>})}
        {!mobileUsers.length && <tr><td colSpan={10} className="text-center py-8 text-gray-500">No mobile users yet for this scope.</td></tr>}
      </tbody></table></div></Card>
    </div>}

    {showMobileSections && section==='settings'&&<div className="space-y-5">
      <Card title="Notification Repeat Interval"><div className="flex items-center gap-3 flex-wrap">
        <input type="number" min="1" className="border rounded h-10 px-3 w-32" value={notificationInterval} disabled={!isMaster} onChange={e=>setNotificationInterval(e.target.value)}/>
        <span className="text-sm text-gray-500">minutes between repeat alerts for an unaccepted branch request</span>
        {isMaster && <Button onClick={saveNotificationInterval} disabled={savingInterval}><RefreshCw className="h-4 w-4 mr-2"/>{savingInterval?'Saving...':'Save'}</Button>}
      </div></Card>

      <Card title="Mobile APK">
        {isMaster && <div className="mb-4 flex flex-wrap items-center gap-3">
          <input ref={apkFileRef} type="file" accept=".apk,application/vnd.android.package-archive" className="hidden" onChange={uploadApk}/>
          <Button onClick={()=>apkFileRef.current?.click()} disabled={apkUploading}><Upload className="h-4 w-4 mr-2"/>{apkUploading?'Uploading...':'Upload APK'}</Button>
          <span className="text-xs text-gray-500">.apk only · Master Admin</span>
        </div>}
        <div className="flex flex-col md:flex-row md:items-center md:justify-between gap-4">
          <div>{latestVersion ? <>Latest: <b>{latestVersion.version_name||latestVersion.apk_filename}</b><div className="text-xs text-gray-500 mt-1">Released {fmt(latestVersion.release_date)}</div></> : 'No APK published yet.'}</div>
          <div className="flex flex-wrap gap-2">
            {latestVersion && <Button onClick={downloadApk}><Download className="h-4 w-4 mr-2"/>Download APK</Button>}
            {latestVersion && <Button variant="outline" onClick={copyApkLink}><Copy className="h-4 w-4 mr-2"/>Copy Download Link</Button>}
          </div>
        </div>
      </Card>
    </div>}

    {showAuditSections && section==='physical'&&<div className="space-y-5">
      <Card title="Physical Perpetual"><div className="flex flex-wrap gap-2"><input className="border rounded h-10 px-3 flex-1 min-w-64" value={partNumber} onChange={e=>setPartNumber(e.target.value.toUpperCase())} placeholder="Manual Part Number"/><Button onClick={lookup}><ScanLine className="h-4 w-4 mr-2"/>Lookup</Button></div>
      {snapshot&&<div className="mt-4 space-y-4"><div className="grid md:grid-cols-4 gap-3"><Info label="Part Number" value={snapshot.part_number}/><Info label="Part Name" value={snapshot.part_name}/><Info label="System Quantity" value={snapshot.system_quantity}/><Info label="MAV" value={money(snapshot.mav)}/><Info label="PIN Location" value={snapshot.pin_location||'-'}/><Info label="Physical Quantity" value={<input type="number" className="border rounded h-9 px-2 w-full" value={physicalQty} onChange={e=>setPhysicalQty(e.target.value)}/>}/><Info label="Physical Location" value={<input className="border rounded h-9 px-2 w-full" value={physicalLocation} onChange={e=>setPhysicalLocation(e.target.value)}/>}/><Info label="Remarks" value={<input className="border rounded h-9 px-2 w-full" value={remarks} onChange={e=>setRemarks(e.target.value)}/>} /></div>{calc&&<div className="grid md:grid-cols-5 gap-3"><Info label="Difference" value={calc.difference}/><Info label="Shortage Qty" value={calc.shortage_qty}/><Info label="Excess Qty" value={calc.excess_qty}/><Info label="Shortage Value" value={money(calc.shortage_value)}/><Info label="Excess Value" value={money(calc.excess_value)}/></div>}<Button onClick={addItem}><Plus className="h-4 w-4 mr-2"/>Add to Verification List</Button></div>}</Card>
      <Card title={`Temporary Verification List (${pending.length})`}><DetailTable rows={pending} removable onRemove={i=>setPending(p=>p.filter((_,x)=>x!==i))}/><div className="flex justify-end mt-4"><Button onClick={uploadSession} disabled={!pending.length}><Upload className="h-4 w-4 mr-2"/>Finish Upload</Button></div></Card>
      <Card title="Today's Verification"><p className="text-sm text-gray-500 mb-3">All verified line items for today. Total items: {todayItems}</p><DetailTable rows={sessions.filter(s=>todayKey(s.created_at)===todayKey()).flatMap(s=>s.items||[])} empty="Open History and View a session to inspect its items."/></Card>
    </div>}

    {showAuditSections && section==='auto'&&<div className="space-y-5">
      <Card title="Auto Perpetual — monthly coverage">
        <p className="text-sm text-gray-600 mb-4">Mark mobile users Present/Absent under Mobile Users, then generate today&apos;s allocations. One AOPS session per user per day.</p>
        {autoSummary && <div className="grid sm:grid-cols-2 lg:grid-cols-4 gap-3 mb-4">
          <Info label="Month" value={autoSummary.month_key}/>
          <Info label="Total lines" value={autoSummary.total_stock_lines}/>
          <Info label="Verified (unique)" value={autoSummary.verified_unique_lines}/>
          <Info label="Coverage %" value={`${autoSummary.monthly_coverage_pct}%`}/>
          <Info label="Pending" value={autoSummary.pending_lines}/>
          <Info label="Days left" value={autoSummary.days_remaining}/>
          <Info label="Match lines" value={autoSummary.match_lines}/>
          <Info label="Shortage lines" value={autoSummary.shortage_lines}/>
          <Info label="Damage lines" value={autoSummary.damage_lines}/>
          <Info label="Physical count" value={autoSummary.physical_verification_count}/>
          <Info label="Auto count" value={autoSummary.auto_verification_count}/>
        </div>}
        <div className="flex flex-wrap gap-2">
          <Button onClick={()=>generateAutoPerpetual(false)} disabled={!scopeReady||autoGenerating}><ClipboardCheck className="h-4 w-4 mr-2"/>{autoGenerating?'Generating...':'Generate Auto Perpetual'}</Button>
          <Button variant="outline" onClick={()=>generateAutoPerpetual(true)} disabled={!scopeReady||autoRecalcBusy}><RefreshCw className="h-4 w-4 mr-2"/>{autoRecalcBusy?'Working...':'Recalculate pending'}</Button>
          <Button variant="outline" onClick={loadAutoPerpetual} disabled={!scopeReady}><RefreshCw className="h-4 w-4 mr-2"/>Refresh</Button>
        </div>
      </Card>
      <Card title={`Today&apos;s assignments (${autoAssignments.length})`}>
        <div className="overflow-x-auto"><table className="w-full text-sm"><thead><tr>{['User','Part','LOC','Status'].map(h=><th key={h} className="text-left p-2">{h}</th>)}</tr></thead><tbody>
          {autoAssignments.map(a=><tr key={a.id||`${a.mobile_user_id}-${a.part_number}`} className="border-t"><td className="p-2 font-mono">{a.mobile_user_id}</td><td className="p-2">{a.part_number}</td><td className="p-2">{a.loc||'-'}</td><td className="p-2">{a.status}</td></tr>)}
          {!autoAssignments.length && <tr><td colSpan={4} className="text-center py-6 text-gray-500">No assignments yet. Generate Auto Perpetual after marking attendance.</td></tr>}
        </tbody></table></div>
      </Card>
      <Card title="User performance (this month)">
        <div className="overflow-x-auto"><table className="w-full text-sm min-w-[1100px]"><thead><tr>{['Rank','User','Monthly Target','Normal','Catch-up','Assigned','Completed','Pending','Completion %'].map(h=><th key={h} className="text-left p-2">{h}</th>)}</tr></thead><tbody>
          {userPerformance.map(u=><tr key={u.mobile_user_id} className="border-t"><td className="p-2">{u.rank}</td><td className="p-2">{u.name}<div className="text-xs text-gray-500 font-mono">{u.mobile_user_id}</div></td><td className="p-2">{u.monthly_target}</td><td className="p-2">{u.normal_target}</td><td className="p-2">{u.catch_up_target}</td><td className="p-2">{u.assigned}</td><td className="p-2">{u.completed}</td><td className="p-2">{u.pending}</td><td className="p-2 font-semibold">{u.completion_pct}%</td></tr>)}
          {!userPerformance.length && <tr><td colSpan={9} className="text-center py-6 text-gray-500">No performance data yet.</td></tr>}
        </tbody></table></div>
      </Card>
    </div>}

    {showAuditSections && section==='history'&&<div className="space-y-5"><Card title="Verification History Filters"><div className="grid md:grid-cols-4 gap-3"><input type="date" className="border rounded h-10 px-3" value={filters.date_from} onChange={e=>setFilters({...filters,date_from:e.target.value})}/><input type="date" className="border rounded h-10 px-3" value={filters.date_to} onChange={e=>setFilters({...filters,date_to:e.target.value})}/><input type="month" className="border rounded h-10 px-3" value={filters.month} onChange={e=>setFilters({...filters,month:e.target.value})}/><select className="border rounded h-10 px-3" value={filters.user_filter} onChange={e=>setFilters({...filters,user_filter:e.target.value})}><option value="">All Users</option>{mobileUsers.map(u=><option key={u.mobile_user_id} value={u.mobile_user_id}>{u.name}</option>)}</select><select className="border rounded h-10 px-3" value={filters.verification_type} onChange={e=>setFilters({...filters,verification_type:e.target.value})}><option value="all">All Types</option><option value="physical">Physical</option><option value="auto">Auto</option><option value="recheck">Recheck</option></select><select className="border rounded h-10 px-3" value={filters.result_filter} onChange={e=>setFilters({...filters,result_filter:e.target.value})}><option value="all">All Results</option><option value="match">Match</option><option value="shortage">Shortage</option><option value="excess">Excess</option><option value="damage">Damage</option></select><input className="border rounded h-10 px-3" placeholder="Part Number" value={filters.part_number} onChange={e=>setFilters({...filters,part_number:e.target.value})}/><input className="border rounded h-10 px-3" placeholder="LOC" value={filters.loc} onChange={e=>setFilters({...filters,loc:e.target.value})}/></div><Button className="mt-3" onClick={()=>{loadHistoryRecords(); loadSessions();}}>Apply Filters</Button></Card>
      <Card title={`Audit records (${historyRecords.length})`}><div className="overflow-x-auto"><table className="w-full text-sm min-w-[1600px]"><thead><tr>{['Session','Type','Date','User','Part','LOC','System','Physical','Short','Excess','Damage','Result','Remark'].map(h=><th key={h} className="text-left p-2">{h}</th>)}</tr></thead><tbody>{historyRecords.map(r=><tr key={r.id||`${r.session_id}-${r.part_number}`} className="border-t"><td className="p-2 font-mono text-xs">{r.session_id}</td><td className="p-2">{r.coverage_kind==='recheck'?'Recheck':r.verification_type||'-'}</td><td className="p-2">{fmt(r.verified_at||r.created_at)}</td><td className="p-2">{r.verified_by_name||r.verified_user}</td><td className="p-2">{r.part_number}</td><td className="p-2">{r.pin_location||r.system_location||'-'}</td><td className="p-2">{r.system_quantity}</td><td className="p-2">{r.physical_quantity}</td><td className="p-2">{r.shortage_qty}</td><td className="p-2">{r.excess_qty}</td><td className="p-2">{r.damage_qty||0}</td><td className="p-2">{r.quantity_status||r.overall_status}</td><td className="p-2">{r.remark||r.remarks||'-'}</td></tr>)}</tbody></table></div></Card>
      <Card title="Session summary"><div className="flex flex-wrap items-center justify-between gap-3 mb-4"><Button onClick={exportAllExcel}><Download className="h-4 w-4 mr-2"/>Download Complete Excel</Button></div><div className="overflow-x-auto"><table className="w-full text-sm min-w-[1350px]"><thead><tr>{['Session ID','Date','Brand','Dealer','Branch','User','Total Items','Shortage Qty','Excess Qty','Status','View'].map(h=><th key={h} className="text-left p-3">{h}</th>)}</tr></thead><tbody>{sessions.map(s=><tr key={s.session_id} className="border-t"><td className="p-3 font-semibold">{s.session_id}</td><td>{fmt(s.created_at)}</td><td>{s.brand_name}</td><td>{s.dealer_name}</td><td>{s.branch}</td><td>{s.verified_by_name}</td><td>{s.total_items}</td><td>{s.total_shortage_qty}</td><td>{s.total_excess_qty}</td><td>{s.status}</td><td><Button size="sm" variant="outline" onClick={()=>openSession(s)}><Eye className="h-4 w-4"/></Button></td></tr>)}</tbody></table></div></Card></div>}

    {showAuditSections && viewSession&&<div className="fixed inset-0 bg-black/50 z-50 p-4 overflow-auto"><div className="bg-white rounded-xl max-w-7xl mx-auto p-5 border shadow-lg"><div className="flex justify-between mb-4"><div><h2 className="text-xl font-bold">{viewSession.session_id}</h2><p className="text-gray-500">{fmt(viewSession.created_at)}</p></div><Button variant="outline" onClick={()=>setViewSession(null)}><X className="h-4 w-4"/></Button></div><DetailTable rows={viewSession.items||[]} canManage={canManage} onCorrection={updateCorrection}/></div></div>}

    <NmtsConfirmDialog
      open={!!branchMoveTarget}
      title="Move Mobile User Branch"
      message={
        branchMoveTarget
          ? `Move ${branchMoveTarget.mobile_user_id} from ${branchMoveTarget.branch} to ${branch}? Existing mobile session will be logged out.`
          : ''
      }
      confirmLabel="Move Branch"
      variant="danger"
      loading={branchMoveBusy}
      onCancel={() => {
        if (!branchMoveBusy) setBranchMoveTarget(null);
      }}
      onConfirm={performChangeBranch}
    />

    <NmtsConfirmDialog
      open={!!deleteTarget}
      title="Delete Mobile User"
      message={deleteTarget ? `Archive ${deleteTarget.mobile_user_id}? Verification history is preserved; they will not receive new allocations.` : ''}
      confirmLabel="Delete"
      variant="danger"
      loading={deleteBusy}
      onCancel={() => { if (!deleteBusy) setDeleteTarget(null); }}
      onConfirm={deleteMobileUser}
    />
  </div>
}

const Card=({title,children})=><div className="bg-white rounded-xl border p-5"><h2 className="font-bold text-lg mb-4">{title}</h2>{children}</div>;
const Tab=({active,onClick,icon:Icon,title})=><button onClick={onClick} className={`border rounded-xl p-4 flex gap-3 items-center ${active?'bg-green-50 ring-2 ring-green-500':'bg-white'}`}><Icon className="h-5 w-5 text-green-600"/><b>{title}</b></button>;
const Info=({label,value})=><div className="border rounded-lg p-3 bg-gray-50"><div className="text-xs text-gray-500 mb-1">{label}</div><div className="font-semibold">{value}</div></div>;
const SimpleTable=({headers,rows})=><div className="overflow-x-auto"><table className="w-full text-sm"><thead><tr>{headers.map(h=><th key={h} className="text-left p-3">{h}</th>)}</tr></thead><tbody>{rows.map((r,i)=><tr className="border-t" key={i}>{r.map((v,j)=><td className="p-3" key={j}>{v}</td>)}</tr>)}{!rows.length&&<tr><td colSpan={headers.length} className="text-center py-8 text-gray-500">No records yet.</td></tr>}</tbody></table></div>;
function DetailTable({rows=[],removable=false,onRemove,empty='No verified items',canManage=false,onCorrection}){return <div className="overflow-x-auto"><table className="w-full text-sm min-w-[1500px]"><thead><tr>{['Part Number','Part Name','MAV','System Qty','Physical Qty','Difference','Shortage Qty','Excess Qty','Shortage Value','Excess Value','PIN Location','Status','Correction Status','Correction Method','Remarks',...(removable?['Action']:[]),...(canManage?['Correction']:[])].map(h=><th key={h} className="text-left p-3">{h}</th>)}</tr></thead><tbody>{rows.map((r,i)=><tr className="border-t" key={r.id||r.part_number||i}><td className="p-3 font-medium">{r.part_number}</td><td>{r.part_name||r.item_name||r.description||r.part_description||'-'}</td><td>{money(r.mav)}</td><td>{r.system_quantity}</td><td>{r.physical_quantity}</td><td>{r.difference}</td><td>{r.shortage_qty}</td><td>{r.excess_qty}</td><td>{money(r.shortage_value)}</td><td>{money(r.excess_value)}</td><td>{r.pin_location||r.system_location||'-'}</td><td>{r.overall_status||r.quantity_status||'-'}</td><td>{r.correction_status||'-'}</td><td>{r.correction_method||'-'}</td><td>{r.remarks||r.remark||'-'}</td>{removable&&<td><Button size="sm" variant="outline" onClick={()=>onRemove(i)}><Trash2 className="h-4 w-4"/></Button></td>}{canManage&&<td><select className="border rounded h-9 px-2" defaultValue="" onChange={e=>{if(e.target.value)onCorrection(r,e.target.value);e.target.value=''}}><option value="">Update...</option><option value="system_corrected">System corrected</option><option value="physical_relocated">Physical relocated</option><option value="both">Both corrected</option><option value="no_action">No action</option></select></td>}</tr>)}{!rows.length&&<tr><td colSpan={17} className="text-center py-8 text-gray-500">{empty}</td></tr>}</tbody></table></div>}
