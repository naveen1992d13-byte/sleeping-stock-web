import React, { Fragment, useEffect, useMemo, useRef, useState } from 'react';
import { useOutletContext } from 'react-router-dom';
import axios from 'axios';
import { API, useAuth } from '../App';
import { Download, Ban, Trash2, ScanLine, Copy, ClipboardCheck, Camera, History, Eye, Plus, Upload, X, RefreshCw, QrCode, CheckCircle2, ArrowRightLeft } from 'lucide-react';
import { Button } from '../components/ui/button';
import { toast } from 'sonner';
import { NmtsConfirmDialog } from '../components/NmtsConfirmDialog';
import { NmtsModal } from '../components/NmtsModal';

const HUB_COLORS = {
  card: '#DCE8D1',
  primary: '#059669',
  active: '#8BC34A',
  button: '#708A5D',
  border: '#C8D5BF',
  text: '#263326',
  muted: '#5F6B5A',
};
const clean = (v) => String(v || '').trim();
const exact = (v) => clean(v) && clean(v) !== 'N/A' && !clean(v).startsWith('All ');
const isAllLabel = (v) => !clean(v) || clean(v) === 'N/A' || clean(v).toLowerCase().startsWith('all ');
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

export default function NMTSMobile({ variant = 'mobile', hideInternalNav = false, sectionOverride = null }){
  const showMobileSections = variant !== 'audit';
  const showAuditSections = variant !== 'mobile';
  const { user } = useAuth();
  const { scopeBrand, scopeDealer, scopeBranch } = useOutletContext();
  const brand=clean(scopeBrand), dealer=clean(scopeDealer), branch=clean(scopeBranch);
  const scopeReady=exact(brand)&&exact(dealer)&&exact(branch);
  const canManage=['master','admin'].includes(user?.role);
  const isMaster = user?.role==='master';
  const isUser = user?.role==='user';
  const canPair = canManage || isUser;
  const canManageMobileAccount = canManage;
  const userBrand = clean(user?.brand);
  const userDealer = clean(user?.dealer || user?.group);
  const userBranch = clean(user?.branch || user?.location);
  const pairBrand = isUser ? userBrand : brand;
  const pairDealer = isUser ? userDealer : dealer;
  const pairBranch = isUser ? userBranch : branch;
  const pairReady = exact(pairBrand) && exact(pairDealer) && exact(pairBranch);
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
  const [autoSuggestions,setAutoSuggestions]=useState([]);
  const [suggestionDetail,setSuggestionDetail]=useState(null);
  const [auditUserFilter,setAuditUserFilter]=useState('');
  const [todayRecords,setTodayRecords]=useState([]);
  const [sendBusy,setSendBusy]=useState(false);
  const [autoGenerating,setAutoGenerating]=useState(false);
  const [autoRecalcBusy,setAutoRecalcBusy]=useState(false);
  const [creatingUser,setCreatingUser]=useState(false);
  const [pairingFor,setPairingFor]=useState(null);
  const [pairingResult,setPairingResult]=useState(null);

  const [branchMoveTarget, setBranchMoveTarget] = useState(null);
  const [branchMoveBusy, setBranchMoveBusy] = useState(false);
  const [moveDestBranch, setMoveDestBranch] = useState('');
  const [moveBranches, setMoveBranches] = useState([]);
  const [moveConfirm, setMoveConfirm] = useState(false);
  const [movePending, setMovePending] = useState({ pending_requests: [], pending_assignments: [], has_pending: false });
  const hubTheme = hideInternalNav;
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
  };
  const loadTodayAttendance=async()=>{
    if(!scopeReady) { setTodayAttendance({}); return; }
    try{
      const r=await axios.get(`${API}/mobile/users/attendance/today`,{params:{brand_name:brand,dealer_name:dealer,branch}});
      setTodayAttendance(r.data?.records||{});
    }catch{ setTodayAttendance({}); }
  };
  const loadAutoPerpetual=async()=>{
    if(!scopeReady){ setAutoSummary(null); setAutoAssignments([]); return; }
    try{
      const [s,a,p,sg]=await Promise.all([
        axios.get(`${API}/mobile/auto-perpetual/summary`,{params:{brand_name:brand,dealer_name:dealer,branch}}),
        axios.get(`${API}/mobile/auto-perpetual/assignments/today`,{params:{brand_name:brand,dealer_name:dealer,branch}}),
        axios.get(`${API}/mobile/auto-perpetual/user-performance`,{params:{brand_name:brand,dealer_name:dealer,branch}}),
        axios.get(`${API}/mobile/auto-perpetual/suggestions`,{params:{brand_name:brand,dealer_name:dealer,branch}}),
      ]);
      setAutoSummary(s.data||null);
      setAutoAssignments(a.data||[]);
      setUserPerformance(p.data||[]);
      setAutoSuggestions(sg.data||[]);
    }catch(e){ setAutoSummary(null); setAutoAssignments([]); setUserPerformance([]); setAutoSuggestions([]); }
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
  const loadTodayRecords=async()=>{
    if(!scopeReady){ setTodayRecords([]); return; }
    try{
      const day=todayKey();
      const r=await axios.get(`${API}/mobile/perpetual-stock/verification-history`,{params:{brand,dealer,branch,date_from:day,date_to:day,limit:500}});
      setTodayRecords(Array.isArray(r.data)?r.data:[]);
    }catch{ setTodayRecords([]); }
  };
  const loadApkLink=async()=>{
    try{
      const r=await axios.get(`${API}/mobile/app-versions/download-link/latest`);
      setApkDownloadUrl(r.data?.download_url||'');
    }catch{ setApkDownloadUrl(''); }
  };
  const loadAppVersions=async()=>{
    try{const r=await axios.get(`${API}/mobile/app-versions`);setAppVersions(r.data||[]);}catch{/* non-blocking */}
    loadApkLink();
  };

  const loadSessions=async()=>{if(!scopeReady){setSessions([]);return;}try{const r=await axios.get(`${API}/mobile/perpetual-stock/sessions`,{params:{brand,dealer,branch,date_from:filters.date_from||undefined,date_to:filters.date_to||undefined}});setSessions(r.data||[]);}catch(e){toast.error(e.response?.data?.detail||'Verification history load failed')}};

  useEffect(()=>{
    setPairingResult(null);setPairingFor(null);setPending([]);setSnapshot(null);
    loadMobileUsers();loadAppVersions();loadTodayAttendance();
    if (showAuditSections) { loadSessions(); loadAutoPerpetual(); loadHistoryRecords(); loadTodayRecords(); }
    /* eslint-disable-next-line */
  },[scopeBrand,scopeDealer,scopeBranch,user?.id, showAuditSections]);

  useEffect(()=>{
    if (!showMobileSections) return undefined;
    const id = setInterval(()=>{ loadMobileUsers(); }, 30000);
    return ()=>clearInterval(id);
    /* eslint-disable-next-line */
  },[scopeBrand,scopeDealer,scopeBranch,user?.id, showMobileSections]);

  const generateNewPairing=async()=>{
    if(!pairReady) return toast.error(isUser ? 'Your assigned Brand, Dealer and Branch are required for pairing' : 'Select an exact Brand, Dealer and Branch in the Dashboard filter first');
    setCreatingUser(true);
    try{
      const payload = {pairing_type:'NEW',brand_name:pairBrand,dealer_name:pairDealer,branch:pairBranch};
      const r=await axios.post(`${API}/mobile/pairing/generate`,payload);
      setPairingResult({...r.data, brand_name:pairBrand, dealer_name:pairDealer, branch:pairBranch});
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
      const r=await axios.post(`${API}/mobile/auto-perpetual/generate`,null,{params:{brand_name:brand,dealer_name:dealer,branch,recalc_pending:recalc},timeout:90000});
      if(r.data?.duplicate) toast.info('Draft suggestion already exists for today');
      else toast.success(`Draft ${r.data?.suggestion_number||r.data?.suggestion?.suggestion_number||''} created with ${r.data?.total_items||r.data?.suggestion?.total_items||0} items — review and Send to Mobile`);
      loadAutoPerpetual();
    }catch(e){
      const detail=e.response?.data?.detail;
      const msg=typeof detail==='string'?detail:(e.code==='ECONNABORTED'?'Generate timed out. Nothing was created.':'Generate failed');
      toast.error(msg);
    }
    finally{ setAutoGenerating(false); setAutoRecalcBusy(false); }
  };

  const openSuggestion=async(id)=>{
    try{
      const r=await axios.get(`${API}/mobile/auto-perpetual/suggestions/${id}`);
      setAuditUserFilter('');
      setSuggestionDetail(r.data);
    }catch(e){toast.error('Could not load suggestion details')}
  };

  const sendSuggestion=async(id)=>{
    setSendBusy(true);
    try{
      const r=await axios.post(`${API}/mobile/auto-perpetual/suggestions/${id}/send`);
      toast.success(`Sent ${r.data?.assignments_created||0} assignments to mobile users`);
      setSuggestionDetail(null);
      loadAutoPerpetual();
    }catch(e){toast.error(e.response?.data?.detail||'Send failed')}
    finally{setSendBusy(false)}
  };

  const generatePairing=async(mu)=>{
    setPairingFor(mu.mobile_user_id);
    try{
      const r=await axios.post(`${API}/mobile/pairing/generate`,{pairing_type:'REPAIR',mobile_user_id:mu.mobile_user_id,brand_name:mu.brand_name,dealer_name:mu.dealer_name,branch:mu.branch});
      setPairingResult({mobile_user_id:mu.mobile_user_id, brand_name:mu.brand_name, dealer_name:mu.dealer_name, branch:mu.branch, ...r.data});
      toast.success('Re-pair QR generated — same Mobile User ID will be reused');
    }catch(e){const d=e.response?.data?.detail;toast.error(typeof d==='string'?d:(d?.message||'Re-pair code generation failed'))}
    finally{setPairingFor(null)}
  };

  const destOptions = useMemo(
    () => moveBranches.filter((b) => {
      const name = clean(b.name);
      if (!branchMoveTarget || isAllLabel(name)) return false;
      if (name.toLowerCase() === clean(branchMoveTarget.branch).toLowerCase()) return false;
      const currentBrand = clean(branchMoveTarget.brand_name).toLowerCase();
      const currentDealer = clean(branchMoveTarget.dealer_name).toLowerCase();
      const rowBrand = clean(b.brand || b.brand_name).toLowerCase();
      const rowDealer = clean(b.dealer || b.dealer_name).toLowerCase();
      if (b.dealer_id && branchMoveTarget.dealer_id) return b.dealer_id === branchMoveTarget.dealer_id;
      return rowBrand === currentBrand && rowDealer === currentDealer;
    }),
    [moveBranches, branchMoveTarget]
  );
  const moveReady = Boolean(branchMoveTarget && clean(moveDestBranch) && destOptions.some((b) => clean(b.name) === clean(moveDestBranch)));

  const closeTransfer = (force=false) => {
    if (branchMoveBusy && !force) return;
    setBranchMoveTarget(null);
    setMoveDestBranch('');
    setMoveConfirm(false);
    setMovePending({ pending_requests: [], pending_assignments: [], has_pending: false });
  };

  const performChangeBranch = async () => {
    const mu = branchMoveTarget;
    if (!mu || branchMoveBusy || !moveReady) return;
    if (!moveConfirm) {
      setMoveConfirm(true);
      return;
    }
    setBranchMoveBusy(true);
    try {
      const r = await axios.put(`${API}/mobile/users/${mu.mobile_user_id}/branch`, {
        brand_name: mu.brand_name,
        dealer_name: mu.dealer_name,
        branch: moveDestBranch,
        confirm_pending_release: Boolean(movePending.has_pending),
      });
      toast.success(r.data?.message || 'Branch updated');
      closeTransfer(true);
      loadMobileUsers();
      loadTodayAttendance();
      window.dispatchEvent(new Event('nmts-mobile-users-changed'));
    }catch(e){
      const d=e.response?.data?.detail;
      if (d && typeof d==='object' && d.code==='PENDING_WORK') {
        setMovePending({
          pending_requests: d.pending_requests || [],
          pending_assignments: d.pending_assignments || [],
          has_pending: true,
        });
        setMoveConfirm(true);
        toast.error(d.message || 'Pending work must be reviewed before moving');
      } else {
        toast.error(typeof d==='string'?d:(d?.message||'Branch change failed'));
      }
    }
    finally { setBranchMoveBusy(false); }
  };

  const changeBranch = async (mu) => {
    if (!canManageMobileAccount) return;
    setBranchMoveTarget(mu);
    setMoveDestBranch('');
    setMoveConfirm(false);
    setMovePending({ pending_requests: [], pending_assignments: [], has_pending: false });
    try {
      const [bRes, pendingRes] = await Promise.all([
        axios.get(`${API}/mobile/users/${mu.mobile_user_id}/transfer-branches`),
        axios.get(`${API}/mobile/users/${mu.mobile_user_id}/transfer-pending`).catch(() => ({ data: {} })),
      ]);
      const payload = bRes.data || {};
      setBranchMoveTarget((prev) => prev && prev.mobile_user_id === mu.mobile_user_id ? { ...prev, dealer_id: payload.dealer_id || prev.dealer_id } : prev);
      setMoveBranches(Array.isArray(payload.branches) ? payload.branches : []);
      const pending = pendingRes.data || {};
      setMovePending({
        pending_requests: pending.pending_requests || [],
        pending_assignments: pending.pending_assignments || [],
        has_pending: Boolean(pending.has_pending),
      });
    } catch {
      toast.error('Could not load destination branches');
      setBranchMoveTarget(null);
    }
  };

  const lookup=async()=>{if(!scopeReady)return toast.error('Select exact Dashboard Brand, Dealer and Branch');if(!clean(partNumber))return toast.error('Enter Part Number');try{const r=await axios.get(`${API}/mobile/perpetual-stock/lookup`,{params:{part_number:clean(partNumber),brand,dealer,branch}});setSnapshot(r.data);setPhysicalLocation(r.data.pin_location||'');setPhysicalQty('');setRemarks('')}catch(e){setSnapshot(null);toast.error(e.response?.data?.detail||'Part not found')}};
  const scan=async(e)=>{const file=e.target.files?.[0];e.target.value='';if(!file)return;if(!('TextDetector'in window))return toast.info('OCR is not supported in this browser. Enter Part Number manually.');try{const bitmap=await createImageBitmap(file);const result=await new window.TextDetector().detect(bitmap);const candidates=result.map(x=>x.rawValue).join(' ').match(/[A-Z0-9][A-Z0-9\-_/]{4,}/gi)||[];if(!candidates.length)return toast.error('Part Number not detected');setPartNumber(candidates.sort((a,b)=>b.length-a.length)[0])}catch{toast.error('OCR scan failed')}};
  const calc=useMemo(()=>{if(!snapshot||physicalQty==='')return null;const sys=Number(snapshot.system_quantity||0),phy=Number(physicalQty),mav=Number(snapshot.mav||0),diff=phy-sys;return{difference:diff,shortage_qty:diff<0?-diff:0,excess_qty:diff>0?diff:0,shortage_value:(diff<0?-diff:0)*mav,excess_value:(diff>0?diff:0)*mav}},[snapshot,physicalQty]);
  const addItem=()=>{if(!snapshot||!calc||Number.isNaN(Number(physicalQty)))return toast.error('Enter valid Physical Quantity');if(pending.some(x=>clean(x.part_number).toLowerCase()===clean(snapshot.part_number).toLowerCase()))return toast.error('Part already added');setPending(p=>[...p,{...snapshot,physical_quantity:Number(physicalQty),scanned_location:physicalLocation,remarks,...calc}]);setPartNumber('');setSnapshot(null);setPhysicalQty('');setPhysicalLocation('');setRemarks('');toast.success('Added to pending verification')};
  const uploadSession=async()=>{if(!pending.length)return toast.error('Add at least one item');try{const r=await axios.post(`${API}/mobile/perpetual-stock/sessions`,{brand,dealer,branch,items:pending.map(x=>({part_number:x.part_number,physical_quantity:x.physical_quantity,scanned_location:x.scanned_location,remarks:x.remarks}))});toast.success(`Uploaded ${r.data.session_id}`);setPending([]);loadSessions()}catch(e){toast.error(e.response?.data?.detail||'Upload failed')}};
  const openSession=async(s)=>{try{const r=await axios.get(`${API}/mobile/perpetual-stock/sessions/${s.session_id}`);setViewSession(r.data)}catch(e){toast.error(e.response?.data?.detail||'Session load failed')}};
  const updateCorrection=async(row,method)=>{try{const status=method==='no_action'?'pending':'corrected';const r=await axios.put(`${API}/mobile/perpetual-stock/${row.id}/correction`,{correction_status:status,correction_method:method,correction_remarks:''});setViewSession(v=>({...v,items:(v.items||[]).map(x=>x.id===row.id?r.data:x)}));toast.success('Correction updated')}catch(e){toast.error(e.response?.data?.detail||'Correction update failed')}};
  const canExportExcel = user?.role === 'master' || user?.role === 'admin';
  const excel=async(s)=>{if(!canExportExcel)return toast.error('Excel download restricted to Admin / Master Admin');try{const r=await axios.get(`${API}/mobile/perpetual-stock/sessions/${s.session_id}/excel`,{responseType:'blob'});const url=URL.createObjectURL(r.data);const a=document.createElement('a');a.href=url;a.download=`${s.session_id}.xlsx`;a.click();URL.revokeObjectURL(url)}catch(e){toast.error(e.response?.data?.detail||'Excel download failed')}};
  const exportAllExcel=async()=>{if(!canExportExcel)return toast.error('Excel download restricted to Admin / Master Admin');try{const params={date_from:filters.date_from||undefined,date_to:filters.date_to||undefined};if(exact(brand))params.brand=brand;if(exact(dealer))params.dealer=dealer;if(exact(branch))params.branch=branch;const r=await axios.get(`${API}/mobile/perpetual-stock/export-all/excel`,{params,responseType:'blob'});const url=URL.createObjectURL(r.data);const a=document.createElement('a');a.href=url;a.download=user?.role==='master'?'perpetual_stock_master.xlsx':user?.role==='admin'?'perpetual_stock_admin.xlsx':'perpetual_stock_branch.xlsx';a.click();URL.revokeObjectURL(url);toast.success('Complete Perpetual Stock Excel downloaded')}catch(e){toast.error(e.response?.data?.detail||'Complete Excel download failed')}};
  const todayItems=useMemo(()=>todayRecords.length,[todayRecords]);

  const latestVersion = appVersions[0];
  const visibleSection = sectionOverride || section;

  return <div className="space-y-6" data-testid={variant === 'audit' ? 'stock-audit-page' : 'nmts-mobile-page'}>
    {showMobileSections && (
      <>
    {!scopeReady && user?.role!=='user' && <div className="border border-amber-300 bg-amber-50 p-3 rounded-lg text-amber-800">Select an exact Brand, Dealer and Branch in the Dashboard filter. Mobile User creation and pairing use only that selected scope.</div>}
      </>
    )}

    {showAuditSections && (
      <>
    <div className="grid md:grid-cols-3 gap-3"><Tab active={section==='physical'} onClick={()=>setSection('physical')} icon={ClipboardCheck} title="Physical Perpetual"/><Tab active={section==='auto'} onClick={()=>setSection('auto')} icon={Camera} title="Auto Perpetual"/><Tab active={section==='history'} onClick={()=>setSection('history')} icon={History} title="Verification History"/></div>
    {!scopeReady && user?.role!=='user' && <div className="border border-gray-200 bg-gray-50 p-3 rounded-lg text-gray-700 text-sm">Select an exact Brand, Dealer and Branch in the top header filters for perpetual stock verification.</div>}
      </>
    )}

    {showMobileSections && visibleSection==='mobile'&&<div className="space-y-5" style={hubTheme ? { color: HUB_COLORS.text } : undefined}>
      <div className="grid md:grid-cols-2 gap-5 items-stretch">
      {canPair && <Card title="Pair Mobile User" themed={hubTheme}><div className="flex flex-col gap-3 h-full min-h-[160px] overflow-hidden">
        <div><p className="font-medium">{isUser ? 'Generate one-time QR for your assigned Brand / Dealer / Branch.' : 'Generate one-time QR for the selected Brand / Dealer / Branch.'}</p><p className="text-xs mt-1" style={{ color: hubTheme ? HUB_COLORS.muted : undefined }} >The Mobile User ID is created only after the user enters Name + Mobile Number and scans this QR.{isUser ? ` Scope: ${pairBrand} / ${pairDealer} / ${pairBranch}.` : ''}</p></div>
        <div className="mt-auto pt-2 w-full flex md:justify-end"><Button className="w-full md:w-auto" onClick={generateNewPairing} disabled={creatingUser||!pairReady} style={hubTheme ? { backgroundColor: HUB_COLORS.button } : undefined}><QrCode className="h-4 w-4 mr-2"/>{creatingUser?'Generating...':'Generate New Pairing QR'}</Button></div>
      </div></Card>}
      <Card title="Download Mobile App" themed={hubTheme}>
        {isMaster && <div className="mb-4 flex flex-wrap items-center gap-3">
          <input ref={apkFileRef} type="file" accept=".apk,application/vnd.android.package-archive" className="hidden" onChange={uploadApk}/>
          <Button onClick={()=>apkFileRef.current?.click()} disabled={apkUploading} style={hubTheme ? { backgroundColor: HUB_COLORS.button } : undefined} data-testid="user-hub-upload-apk"><Upload className="h-4 w-4 mr-2"/>{apkUploading?'Uploading...':'Upload APK'}</Button>
          <span className="text-xs" style={{ color: hubTheme ? HUB_COLORS.muted : undefined }}>.apk only · Master Admin</span>
        </div>}
        <div className="flex flex-col gap-4 h-full">
          <div>{latestVersion ? <>Latest: <b>{latestVersion.version_name||latestVersion.apk_filename}</b><div className="text-xs mt-1" style={{ color: hubTheme ? HUB_COLORS.muted : undefined }}>Released {fmt(latestVersion.release_date)}</div></> : 'No APK published yet.'}</div>
          <div className="mt-auto flex flex-wrap gap-2">
            {latestVersion && <Button onClick={downloadApk} style={hubTheme ? { backgroundColor: HUB_COLORS.button } : undefined} data-testid="user-hub-download-apk"><Download className="h-4 w-4 mr-2"/>Download APK</Button>}
            {latestVersion && <Button variant="outline" onClick={copyApkLink} data-testid="user-hub-copy-apk-link"><Copy className="h-4 w-4 mr-2"/>Copy Download Link</Button>}
          </div>
        </div>
      </Card>
      </div>

      {pairingResult && <div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/75 backdrop-blur-sm p-4" onMouseDown={()=>setPairingResult(null)}>
        <div className="relative w-full max-w-[500px] max-h-[90vh] overflow-y-auto rounded-[24px] border border-white/20 bg-gradient-to-b from-[#1b1d1c] to-[#080a09] p-5 text-white shadow-2xl" onMouseDown={e=>e.stopPropagation()}>
          <button aria-label="Close pairing QR" onClick={()=>setPairingResult(null)} className="absolute right-4 top-4 rounded-full border border-white/30 bg-black/30 p-2 hover:bg-white/10"><X className="h-5 w-5"/></button>
          <div className="flex items-center justify-center gap-3 pr-10">
            <img src="/sleeping-stock-logo-transparent.png" alt="Sleeping Stock" className="h-14 w-14 object-contain"/>
            <div><div className="text-2xl font-black tracking-tight"><span className="text-white">Sleeping</span><span className="text-lime-400">Stock</span></div><div className="text-xs text-gray-300">Non moving Tracking System</div></div>
          </div>
          <div className="mt-4 text-center"><h2 className="text-2xl font-black">{pairingResult.pairing_type==='REPAIR'?'Re-pair':'Pair'} Your <span className="text-lime-400">Device</span></h2><p className="mt-1 text-sm text-gray-300">{pairingResult.pairing_type==='REPAIR'?`Existing ID ${pairingResult.mobile_user_id} will be reused.`:'Name and mobile number are entered in the app.'}</p></div>
          <div className="mt-3 text-center text-sm text-gray-200">{[pairingResult.brand_name, pairingResult.dealer_name, pairingResult.branch].filter(Boolean).join(' · ') || 'Branch details unavailable'}</div>
          <div className="mx-auto mt-4 w-fit rounded-[20px] border border-lime-400/70 bg-white p-3 shadow-[0_0_28px_rgba(163,230,53,0.28)]">
            {pairingResult.qr_code_data_url ? <img src={pairingResult.qr_code_data_url} alt="Device pairing QR code" className="block h-auto w-[min(280px,70vw)] max-w-[280px] aspect-square object-contain image-render-pixel md:w-[250px] lg:w-[280px]"/> : <div className="flex h-[240px] w-[240px] items-center justify-center text-black">QR image unavailable</div>}
          </div>
          <div className="mt-4 rounded-2xl border border-white/15 bg-white/5 p-4">
            <div className="text-xs text-gray-300">Manual Pairing Code</div>
            <div className="mt-1 flex items-center justify-between gap-3"><span className="break-all font-mono text-xl md:text-2xl font-black tracking-widest text-white">{pairingResult.pairing_code}</span><button className="shrink-0 rounded-xl border border-lime-400/40 p-2 text-lime-400 hover:bg-lime-400/10" onClick={()=>{navigator.clipboard.writeText(pairingResult.pairing_code);toast.success('Pairing code copied')}}><Copy className="h-5 w-5"/></button></div>
          </div>
          <div className="mt-3 text-center text-sm text-gray-400">One-time code · Expires {fmt(pairingResult.expires_at)}</div>
          <div className="mt-4 flex justify-center"><Button variant="outline" onClick={()=>setPairingResult(null)}>Close</Button></div>
        </div>
      </div>}

      <Card title="Mobile Users" themed={hubTheme}>
        <p className="text-xs mb-3" style={{ color: hubTheme ? HUB_COLORS.muted : undefined }}>Admin Status is the website Active/Inactive control. Session is Logged In after pair and Logged Out after Logout. Presence is Online while the app sends heartbeats; Offline covers phone-off and network-off. Mark <b>Today (Auto)</b> before Generate Auto Perpetual.</p>
        <div className="nmts-users-table-wrap overflow-x-auto rounded-xl bg-white border">
          <table className="w-full text-sm nmts-users-table" style={{ minWidth: 2100 }}>
            <thead>
              <tr style={{ backgroundColor: HUB_COLORS.primary, color: '#fff' }}>
                {['Mobile User ID','Name','Mobile','Brand','Dealer','Branch','Admin Status','Session','Presence','Last Seen','Today (Auto)','Actions'].map(h=><th key={h} className="p-3 text-left whitespace-nowrap">{h}</th>)}
              </tr>
            </thead>
            <tbody>
        {mobileUsers.map(mu=>{
          const att=todayAttendance[mu.mobile_user_id];
          const attLabel=att==='active'?'Active':att==='inactive'?'Inactive':'Not set';
          const adminStatus = mu.admin_status || mu.status;
          const sessionLabel = mu.session_state === 'logged_in' ? 'Logged In' : 'Logged Out';
          const presenceLabel = mu.presence === 'online' ? 'Online' : 'Offline';
          return <tr className="border-b whitespace-nowrap" key={mu.mobile_user_id}>
          <td className="p-3 font-mono whitespace-nowrap">{mu.mobile_user_id}</td>
          <td className="p-3 min-w-[120px]">{mu.name}</td>
          <td className="p-3 whitespace-nowrap">{mu.mobile_number}</td>
          <td className="p-3 whitespace-nowrap">{mu.brand_name}</td>
          <td className="p-3 min-w-[100px]">{mu.dealer_name}</td>
          <td className="p-3 min-w-[100px]">{mu.branch}</td>
          <td className="p-3 status-cell"><span className={`inline-flex px-2.5 py-1 rounded-full text-xs font-bold whitespace-nowrap ${adminStatus==='active'?'bg-green-100 text-green-700':'bg-gray-200 text-gray-600'}`}>{adminStatus==='active'?'Active':'Inactive'}</span></td>
          <td className="p-3 whitespace-nowrap"><span className={`inline-flex px-2.5 py-1 rounded-full text-xs font-bold ${mu.session_state==='logged_in'?'bg-sky-100 text-sky-800':'bg-gray-100 text-gray-600'}`}>{sessionLabel}</span></td>
          <td className="p-3 whitespace-nowrap"><span className={`inline-flex px-2.5 py-1 rounded-full text-xs font-bold ${mu.presence==='online'?'bg-emerald-100 text-emerald-800':'bg-gray-100 text-gray-600'}`}>{presenceLabel}</span></td>
          <td className="p-3 whitespace-nowrap">{mu.last_seen_at?fmt(mu.last_seen_at):(mu.last_active_at?fmt(mu.last_active_at):'-')}</td>
          <td className="p-3 whitespace-nowrap"><span className={`inline-flex px-2.5 py-1 rounded-full text-xs font-bold ${att==='active'?'bg-emerald-100 text-emerald-800':att==='inactive'?'bg-amber-100 text-amber-800':'bg-gray-100 text-gray-600'}`}>{attLabel}</span></td>
          <td className="p-3 whitespace-nowrap"><div className="flex gap-2 flex-nowrap items-center" style={{ minWidth: 540 }}>
            {canPair && <Button size="sm" variant="outline" title="Re-pair QR" onClick={()=>generatePairing(mu)} disabled={pairingFor===mu.mobile_user_id || mu.status!=='active'}><RefreshCw className="h-4 w-4"/></Button>}
            {canManage && <Button size="sm" variant="outline" title="Transfer Branch" onClick={()=>changeBranch(mu)}><ArrowRightLeft className="h-4 w-4"/></Button>}
            {canManageMobileAccount && mu.status!=='deleted' && <>
              <Button size="sm" variant="outline" title="Account active/inactive" onClick={()=>toggleUserStatus(mu)}>{mu.status==='active'?<Ban className="h-4 w-4"/>:<CheckCircle2 className="h-4 w-4"/>}</Button>
              <Button size="sm" variant={att==='active'?'default':'outline'} title="Present for Auto today" onClick={()=>setDailyAttendance(mu,'active')}>Present</Button>
              <Button size="sm" variant={att==='inactive'?'secondary':'outline'} title="Absent for Auto today" onClick={()=>setDailyAttendance(mu,'inactive')}>Absent</Button>
            </>}
            {isMaster && mu.status!=='deleted' && <Button size="sm" variant="outline" title="Delete (archive)" onClick={()=>setDeleteTarget(mu)}><Trash2 className="h-4 w-4 text-red-600"/></Button>}
          </div></td>
        </tr>})}
        {!mobileUsers.length && <tr><td colSpan={12} className="text-center py-8 text-gray-500">No mobile users yet for this scope.</td></tr>}
      </tbody></table></div></Card>
    </div>}

    {showAuditSections && section==='physical'&&<div className="space-y-5">
      <Card title="Physical Perpetual"><div className="flex flex-wrap gap-2"><input className="border rounded h-10 px-3 flex-1 min-w-64" value={partNumber} onChange={e=>setPartNumber(e.target.value.toUpperCase())} placeholder="Manual Part Number"/><Button onClick={lookup}><ScanLine className="h-4 w-4 mr-2"/>Lookup</Button></div>
      {snapshot&&<div className="mt-4 space-y-4"><div className="grid md:grid-cols-4 gap-3"><Info label="Part Number" value={snapshot.part_number}/><Info label="Part Name" value={snapshot.part_name}/><Info label="System Quantity" value={snapshot.system_quantity}/><Info label="MAV" value={money(snapshot.mav)}/><Info label="PIN Location" value={snapshot.pin_location||'-'}/><Info label="Physical Quantity" value={<input type="number" className="border rounded h-9 px-2 w-full" value={physicalQty} onChange={e=>setPhysicalQty(e.target.value)}/>}/><Info label="Physical Location" value={<input className="border rounded h-9 px-2 w-full" value={physicalLocation} onChange={e=>setPhysicalLocation(e.target.value)}/>}/><Info label="Remarks" value={<input className="border rounded h-9 px-2 w-full" value={remarks} onChange={e=>setRemarks(e.target.value)}/>} /></div>{calc&&<div className="grid md:grid-cols-5 gap-3"><Info label="Difference" value={calc.difference}/><Info label="Shortage Qty" value={calc.shortage_qty}/><Info label="Excess Qty" value={calc.excess_qty}/><Info label="Shortage Value" value={money(calc.shortage_value)}/><Info label="Excess Value" value={money(calc.excess_value)}/></div>}<Button onClick={addItem}><Plus className="h-4 w-4 mr-2"/>Add to Verification List</Button></div>}</Card>
      <Card title={`Pending Verification (${pending.length})`}><DetailTable rows={pending} removable onRemove={i=>setPending(p=>p.filter((_,x)=>x!==i))}/><div className="flex justify-end mt-4"><Button onClick={uploadSession} disabled={!pending.length}><Upload className="h-4 w-4 mr-2"/>Submit Verification</Button></div></Card>
      <Card title="Today's Verification"><p className="text-sm text-gray-500 mb-3">Completed verification records for today. Total items: {todayItems}</p><DetailTable rows={todayRecords} empty="No completed verification records for today."/></Card>
    </div>}

    {showAuditSections && section==='auto'&&<div className="space-y-5">
      <Card title="Auto Perpetual — monthly coverage">
        <p className="text-sm text-gray-600 mb-4">Mark attendance, then <b>Generate</b> to create a DRAFT suggestion (APS). Review the summary and use <b>Send to Mobile Users</b> to activate assignments.</p>
        {autoSummary?.month_end_recovery_warning && <p className="text-sm text-amber-700 bg-amber-50 border border-amber-200 rounded-lg p-3 mb-3">Month-end recovery adjustment may be required — check carry-forward backlog and days remaining.</p>}
        {autoSummary && <div className="grid sm:grid-cols-2 lg:grid-cols-4 gap-3 mb-4">
          <Info label="Month" value={autoSummary.month_key}/>
          <Info label="Eligible lines" value={autoSummary.eligible_lines ?? autoSummary.total_stock_lines}/>
          <Info label="Verified (unique)" value={autoSummary.verified_unique_lines}/>
          <Info label="Coverage %" value={`${autoSummary.monthly_coverage_pct}%`}/>
          <Info label="Pending" value={autoSummary.pending_lines}/>
          <Info label="Days left" value={autoSummary.days_remaining}/>
          <Info label="Carry-forward backlog" value={autoSummary.carry_forward_backlog ?? 0}/>
          <Info label="Recovery extra / user" value={autoSummary.recovery_extra_per_user ?? 0}/>
          <Info label="Match lines" value={autoSummary.match_lines}/>
          <Info label="Loc mismatch" value={autoSummary.location_mismatch_lines ?? 0}/>
          <Info label="Physical count" value={autoSummary.physical_verification_count}/>
          <Info label="Auto count" value={autoSummary.auto_verification_count}/>
        </div>}
        <div className="flex flex-wrap gap-2">
          <Button onClick={()=>generateAutoPerpetual(false)} disabled={!scopeReady||autoGenerating}><ClipboardCheck className="h-4 w-4 mr-2"/>{autoGenerating?'Generating...':'Generate Auto Perpetual (Draft)'}</Button>
          <Button variant="outline" onClick={()=>generateAutoPerpetual(true)} disabled={!scopeReady||autoRecalcBusy}><RefreshCw className="h-4 w-4 mr-2"/>{autoRecalcBusy?'Working...':'Regenerate draft'}</Button>
          <Button variant="outline" onClick={loadAutoPerpetual} disabled={!scopeReady}><RefreshCw className="h-4 w-4 mr-2"/>Refresh</Button>
        </div>
      </Card>
      <Card title="Audit Sessions">
        <div className="overflow-x-auto"><table className="w-full text-sm min-w-[900px]"><thead><tr>{['Audit ID','Date','Users','Assigned','Completed','Pending','Status','View'].map(h=><th key={h} className="text-left p-2 whitespace-nowrap">{h}</th>)}</tr></thead><tbody>
          {autoSuggestions.map(s=>{
            const assigned=s.assigned_count ?? s.assignments_created ?? s.total_items ?? 0;
            const completed=s.completed_count ?? 0;
            const pendingCount=s.pending_count ?? Math.max(0, assigned-completed);
            return <tr key={s.id} className="border-t">
              <td className="p-2 font-mono"><button className="text-emerald-700 font-semibold underline" onClick={()=>openSuggestion(s.id)}>{s.audit_id||s.suggestion_number}</button></td>
              <td className="p-2 whitespace-nowrap">{s.allocation_date||'-'}</td>
              <td className="p-2">{s.users_label || s.user_names?.join(', ') || (s.user_count ?? '-')}</td>
              <td className="p-2">{assigned}</td>
              <td className="p-2">{completed}</td>
              <td className="p-2">{pendingCount}</td>
              <td className="p-2">{s.status}</td>
              <td className="p-2 whitespace-nowrap"><Button size="sm" variant="outline" onClick={()=>openSuggestion(s.id)}><Eye className="h-4 w-4 mr-1"/>View</Button>{s.status==='DRAFT' && <Button size="sm" className="ml-2" onClick={()=>sendSuggestion(s.id)} disabled={sendBusy}>Send</Button>}</td>
            </tr>;
          })}
          {!autoSuggestions.length && <tr><td colSpan={8} className="text-center py-6 text-gray-500">No Audit Sessions yet. Generate a draft after marking attendance.</td></tr>}
        </tbody></table></div>
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
      <Card title="Session summary"><div className="flex flex-wrap items-center justify-between gap-3 mb-4">{canExportExcel && <Button onClick={exportAllExcel}><Download className="h-4 w-4 mr-2"/>Download Complete Excel</Button>}</div><div className="overflow-x-auto"><table className="w-full text-sm min-w-[1350px]"><thead><tr>{['Session ID','Date','Brand','Dealer','Branch','User','Total Items','Shortage Qty','Excess Qty','Status','View'].map(h=><th key={h} className="text-left p-3">{h}</th>)}</tr></thead><tbody>{sessions.map(s=><tr key={s.session_id} className="border-t"><td className="p-3 font-semibold">{s.session_id}</td><td>{fmt(s.created_at)}</td><td>{s.brand_name}</td><td>{s.dealer_name}</td><td>{s.branch}</td><td>{s.verified_by_name}</td><td>{s.total_items}</td><td>{s.total_shortage_qty}</td><td>{s.total_excess_qty}</td><td>{s.status}</td><td><Button size="sm" variant="outline" onClick={()=>openSession(s)}><Eye className="h-4 w-4"/></Button></td></tr>)}</tbody></table></div></Card></div>}

    {showAuditSections && suggestionDetail&&<AuditAllocationDetail suggestion={suggestionDetail} userFilter={auditUserFilter} onFilterUser={setAuditUserFilter} onClose={()=>{setSuggestionDetail(null); setAuditUserFilter('');}} onSend={()=>sendSuggestion(suggestionDetail.id)} sendBusy={sendBusy}/>}

    {showAuditSections && viewSession&&<div className="fixed inset-0 bg-black/50 z-50 p-4 overflow-auto"><div className="bg-white rounded-xl max-w-7xl mx-auto p-5 border shadow-lg"><div className="flex justify-between mb-4"><div><h2 className="text-xl font-bold">{viewSession.session_id}</h2><p className="text-gray-500">{fmt(viewSession.created_at)}</p></div><Button variant="outline" onClick={()=>setViewSession(null)}><X className="h-4 w-4"/></Button></div><DetailTable rows={viewSession.items||[]} canManage={canManage} onCorrection={updateCorrection}/></div></div>}

    <NmtsModal
      open={!!branchMoveTarget}
      onClose={closeTransfer}
      title="Transfer Branch"
    >
      {branchMoveTarget && (
        <div className="space-y-4" data-testid="transfer-branch-dialog" style={hubTheme ? { color: HUB_COLORS.text } : undefined}>
          <div className="grid md:grid-cols-3 gap-3">
            <div className="border rounded-lg p-3" style={{ backgroundColor: hubTheme ? HUB_COLORS.card : undefined }}><div className="text-xs mb-1" style={{ color: HUB_COLORS.muted }}>Current Brand</div><div className="font-semibold">{branchMoveTarget.brand_name || '-'}</div></div>
            <div className="border rounded-lg p-3" style={{ backgroundColor: hubTheme ? HUB_COLORS.card : undefined }}><div className="text-xs mb-1" style={{ color: HUB_COLORS.muted }}>Current Dealer</div><div className="font-semibold">{branchMoveTarget.dealer_name || '-'}</div></div>
            <div className="border rounded-lg p-3" style={{ backgroundColor: hubTheme ? HUB_COLORS.card : undefined }}><div className="text-xs mb-1" style={{ color: HUB_COLORS.muted }}>Current Branch</div><div className="font-semibold">{branchMoveTarget.branch || '-'}</div></div>
          </div>
          <label className="block">
            <span className="text-sm font-semibold">Select Branch</span>
            <select className="mt-1 w-full border rounded-lg h-10 px-3" value={moveDestBranch} onChange={(e)=>{setMoveDestBranch(e.target.value); setMoveConfirm(false);}} data-testid="transfer-select-branch">
              <option value="">Select branch</option>
              {destOptions.map((b)=> <option key={b.id || b.name} value={b.name}>{b.name}</option>)}
            </select>
          </label>
          {movePending.has_pending && (
            <div className="border border-amber-300 bg-amber-50 text-amber-900 rounded-lg p-3 text-sm space-y-2" data-testid="transfer-pending-work">
              <p className="font-semibold">Pending requests or work must be reviewed before Move.</p>
              {!!movePending.pending_requests.length && (
                <div>
                  <div className="font-medium">Pending requests</div>
                  <ul className="list-disc pl-5">
                    {movePending.pending_requests.map((row)=> (
                      <li key={row.request_group_key}>{row.request_group_key} · {row.lock_status || 'picked'} · {row.branch || branchMoveTarget.branch}</li>
                    ))}
                  </ul>
                </div>
              )}
              {!!movePending.pending_assignments.length && (
                <div>
                  <div className="font-medium">Pending Auto Perpetual work</div>
                  <ul className="list-disc pl-5">
                    {movePending.pending_assignments.map((row, idx)=> (
                      <li key={`${row.part_number || 'part'}-${idx}`}>{row.part_number || 'Part'} · {row.loc || '-'} · {row.allocation_date || ''}</li>
                    ))}
                  </ul>
                </div>
              )}
              <p>Confirming Move releases pending requests with the existing Admin release process. Pending work is not removed silently.</p>
            </div>
          )}
          {moveConfirm && (
            <div className="border border-amber-300 bg-amber-50 text-amber-900 rounded-lg p-3 text-sm">
              Move {branchMoveTarget.mobile_user_id} from {branchMoveTarget.branch} to {moveDestBranch} under {branchMoveTarget.dealer_name}? The current mobile session will be logged out.
              {movePending.has_pending ? ' Pending requests will be released first using the existing release process.' : ''}
            </div>
          )}
          <div className="flex justify-end gap-2">
            <Button type="button" variant="outline" disabled={branchMoveBusy} onClick={closeTransfer}>Cancel</Button>
            <Button type="button" disabled={!moveReady || branchMoveBusy} onClick={performChangeBranch} style={hubTheme ? { backgroundColor: HUB_COLORS.button } : undefined}>
              {branchMoveBusy ? 'Moving…' : (moveConfirm ? 'Confirm Move' : 'Move')}
            </Button>
          </div>
        </div>
      )}
    </NmtsModal>

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

const Card=({title,children,themed=false})=><div className={themed ? 'rounded-2xl p-5 shadow-sm h-full overflow-hidden' : 'bg-white rounded-xl border p-5 h-full'} style={themed ? { backgroundColor: HUB_COLORS.card, border: `1px solid ${HUB_COLORS.border}`, color: HUB_COLORS.text } : undefined}><h2 className="font-bold text-lg mb-4">{title}</h2>{children}</div>;
const Tab=({active,onClick,icon:Icon,title})=><button onClick={onClick} className={`border rounded-xl p-4 flex gap-3 items-center ${active?'bg-green-50 ring-2 ring-green-500':'bg-white'}`}><Icon className="h-5 w-5 text-green-600"/><b>{title}</b></button>;
const Info=({label,value})=><div className="border rounded-lg p-3 bg-gray-50"><div className="text-xs text-gray-500 mb-1">{label}</div><div className="font-semibold">{value}</div></div>;
const SimpleTable=({headers,rows})=><div className="overflow-x-auto"><table className="w-full text-sm"><thead><tr>{headers.map(h=><th key={h} className="text-left p-3">{h}</th>)}</tr></thead><tbody>{rows.map((r,i)=><tr className="border-t" key={i}>{r.map((v,j)=><td className="p-3" key={j}>{v}</td>)}</tr>)}{!rows.length&&<tr><td colSpan={headers.length} className="text-center py-8 text-gray-500">No records yet.</td></tr>}</tbody></table></div>;
function AuditAllocationDetail({suggestion, userFilter, onFilterUser, onClose, onSend, sendBusy}){
  const items = suggestion.items || [];
  const grouped = {};
  items.forEach((it)=>{
    const id = it.mobile_user_id || 'unassigned';
    if (!grouped[id]) grouped[id] = { mobile_user_id: id, name: it.mobile_user_name || id, task_id: it.task_id, items: [] };
    grouped[id].items.push(it);
    if (it.mobile_user_name) grouped[id].name = it.mobile_user_name;
    if (it.task_id) grouped[id].task_id = it.task_id;
  });
  const users = Object.values(grouped);
  const visibleItems = userFilter ? (grouped[userFilter]?.items || []) : items;
  const showingUser = userFilter && grouped[userFilter];
  return <div className="fixed inset-0 bg-black/50 z-50 p-4 overflow-auto"><div className="bg-white rounded-xl max-w-6xl mx-auto p-5 border shadow-lg">
    <div className="flex justify-between mb-4"><div>
      <h2 className="text-xl font-bold">{suggestion.audit_id || suggestion.suggestion_number}</h2>
      <p className="text-gray-500">{suggestion.status} · {suggestion.allocation_date} · {suggestion.total_items} items</p>
      {showingUser && <p className="text-sm mt-1">Showing {showingUser.name} · {showingUser.task_id || showingUser.mobile_user_id}</p>}
    </div><div className="flex gap-2">{userFilter && <Button variant="outline" onClick={()=>onFilterUser('')}>All users</Button>}<Button variant="outline" onClick={onClose}><X className="h-4 w-4"/></Button>{suggestion.status==='DRAFT'&&<Button onClick={onSend} disabled={sendBusy}>Send to Mobile Users</Button>}</div></div>
    {!userFilter && <div className="overflow-x-auto mb-4"><table className="w-full text-sm"><thead><tr>{['User','Task ID','Assigned','Completed','Pending'].map(h=><th key={h} className="text-left p-2">{h}</th>)}</tr></thead><tbody>
      {users.map(u=>{
        const assigned=u.items.length;
        const completed=u.items.filter(it=>String(it.verification_status||'').toLowerCase()==='completed' || String(it.verification_status||'').toLowerCase()==='verified').length;
        return <tr key={u.mobile_user_id} className="border-t"><td className="p-2"><button className="text-emerald-700 underline font-semibold" onClick={()=>onFilterUser(u.mobile_user_id)}>{u.name}</button><div className="text-xs text-gray-500 font-mono">{u.mobile_user_id}</div></td><td className="p-2 font-mono text-xs">{u.task_id||'-'}</td><td className="p-2">{assigned}</td><td className="p-2">{completed}</td><td className="p-2">{Math.max(0,assigned-completed)}</td></tr>;
      })}
      {!users.length && <tr><td colSpan={5} className="text-center py-6 text-gray-500">No user allocation yet.</td></tr>}
    </tbody></table></div>}
    <div className="overflow-x-auto max-h-[60vh]"><table className="w-full text-sm min-w-[1100px]"><thead><tr>{['Part','Description','Sys Qty','Location','Type','User','Task','Status','Edits'].map(h=><th key={h} className="text-left p-2 whitespace-nowrap">{h}</th>)}</tr></thead><tbody>
      {visibleItems.map((it,i)=>(
        <Fragment key={`${it.part_number}-${i}`}>
        <tr className="border-t"><td className="p-2">{it.part_number}</td><td className="p-2">{it.part_name}</td><td className="p-2">{it.system_qty}</td><td className="p-2">{it.system_location}</td><td className="p-2">{it.suggestion_type}</td><td className="p-2"><button className="text-emerald-700 underline" onClick={()=>it.mobile_user_id && onFilterUser(it.mobile_user_id)}>{it.mobile_user_name||it.mobile_user_id||'-'}</button></td><td className="p-2 font-mono text-xs">{it.task_id||'-'}</td><td className="p-2">{it.verification_status||'-'}</td><td className="p-2">{it.edit_count||0}{it.quantity_edit_status?` · ${it.quantity_edit_status}`:''}</td></tr>
        {!!(it.quantity_edit_history||[]).length && <tr><td colSpan={9} className="p-2 bg-gray-50 text-xs">Original: {it.original_physical_quantity ?? '-'} · Current: {it.physical_quantity ?? '-'}
          <ul className="mt-1 list-disc pl-5">{it.quantity_edit_history.map((h,idx)=><li key={idx}>{h.status}: {h.previous_quantity} → {h.new_quantity} · {h.correction_reason} · {h.user_name||h.mobile_user_id} · {h.device_id||'-'} · {h.edited_at?fmt(h.edited_at):'-'}</li>)}</ul>
        </td></tr>}
        </Fragment>
      ))}
      {!visibleItems.length && <tr><td colSpan={9} className="text-center py-8 text-gray-500">No parts for this view.</td></tr>}
    </tbody></table></div>
  </div></div>;
}
function DetailTable({rows=[],removable=false,onRemove,empty='No verified items',canManage=false,onCorrection}){return <div className="overflow-x-auto"><table className="w-full text-sm min-w-[1680px]"><thead><tr>{['Part Number','Part Name','MAV','System Qty','Physical Qty','Difference','Shortage Qty','Excess Qty','Shortage Value','Excess Value','PIN Location','Status','Edits','Correction Status','Correction Method','Remarks',...(removable?['Action']:[]),...(canManage?['Correction']:[])].map(h=><th key={h} className="text-left p-3 whitespace-nowrap">{h}</th>)}</tr></thead><tbody>{rows.map((r,i)=>(
  <Fragment key={r.id||r.part_number||i}>
  <tr className="border-t"><td className="p-3 font-medium">{r.part_number}</td><td>{r.part_name||r.item_name||r.description||r.part_description||'-'}</td><td>{money(r.mav)}</td><td>{r.system_quantity}</td><td>{r.physical_quantity}</td><td>{r.difference}</td><td>{r.shortage_qty}</td><td>{r.excess_qty}</td><td>{money(r.shortage_value)}</td><td>{money(r.excess_value)}</td><td>{r.pin_location||r.system_location||'-'}</td><td>{r.overall_status||r.quantity_status||'-'}</td><td>{r.edit_count||0}{r.quantity_edit_status?` · ${r.quantity_edit_status}`:''}</td><td>{r.correction_status||'-'}</td><td>{r.correction_method||'-'}</td><td>{r.remarks||r.remark||'-'}</td>{removable&&<td><Button size="sm" variant="outline" onClick={()=>onRemove(i)}><Trash2 className="h-4 w-4"/></Button></td>}{canManage&&<td><select className="border rounded h-9 px-2" defaultValue="" onChange={e=>{if(e.target.value)onCorrection(r,e.target.value);e.target.value=''}}><option value="">Update...</option><option value="system_corrected">System corrected</option><option value="physical_relocated">Physical relocated</option><option value="both">Both corrected</option><option value="no_action">No action</option></select></td>}</tr>
  {!!(r.quantity_edit_history||[]).length && <tr><td colSpan={18} className="p-3 bg-gray-50 text-xs">Original qty: {r.original_physical_quantity ?? '-'}
    <ul className="mt-1 list-disc pl-5">{r.quantity_edit_history.map((h,idx)=><li key={idx}>{h.status}: {h.previous_quantity} → {h.new_quantity} · {h.correction_reason} · {h.user_name||h.mobile_user_id} · {h.device_id||'-'} · {h.edited_at?fmt(h.edited_at):'-'}</li>)}</ul>
  </td></tr>}
  </Fragment>
))}{!rows.length&&<tr><td colSpan={18} className="text-center py-8 text-gray-500">{empty}</td></tr>}</tbody></table></div>}
