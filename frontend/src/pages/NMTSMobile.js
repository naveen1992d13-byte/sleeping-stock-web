import React, { useEffect, useMemo, useRef, useState } from 'react';
import { useOutletContext } from 'react-router-dom';
import axios from 'axios';
import { API, useAuth } from '../App.js';
import { Smartphone, Download, Ban, Trash2, ScanLine, Copy, ClipboardCheck, Camera, History, Eye, Plus, Upload, X, UserPlus, RefreshCw, Settings2, QrCode, CheckCircle2 } from 'lucide-react';
import { Button } from '../components/ui/button';
import { toast } from 'sonner';

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

export default function NMTSMobile(){
  const { user } = useAuth();
  const { scopeBrand, scopeDealer, scopeBranch } = useOutletContext();
  const brand=clean(scopeBrand), dealer=clean(scopeDealer), branch=clean(scopeBranch);
  const scopeReady=exact(brand)&&exact(dealer)&&exact(branch);
  const canManage=['master','admin'].includes(user?.role);
  const isMaster = user?.role==='master';
  const [section,setSection]=useState('mobile');

  // ==================== Mobile User Management (new model) ====================
  const [mobileUsers,setMobileUsers]=useState([]);
  const [devices,setDevices]=useState([]);
  const [newUserName,setNewUserName]=useState('');
  const [newUserMobile,setNewUserMobile]=useState('');
  const [creatingUser,setCreatingUser]=useState(false);
  const [pairingFor,setPairingFor]=useState(null); // mobile_user_id currently generating a code for
  const [pairingResult,setPairingResult]=useState(null); // { mobile_user_id, pairing_code, qr_payload, expires_at }

  // ==================== App & Settings (new model) ====================
  const [notificationInterval,setNotificationInterval]=useState(30);
  const [savingInterval,setSavingInterval]=useState(false);
  const [appVersions,setAppVersions]=useState([]);
  const [newVersion,setNewVersion]=useState({version_name:'',version_code:'',apk_filename:'',apk_path:'',release_notes:'',min_supported_version_code:1,mandatory:false});
  const [publishingVersion,setPublishingVersion]=useState(false);

  // ==================== Perpetual Stock + History (unchanged existing feature) ====================
  const [partNumber,setPartNumber]=useState('');
  const [snapshot,setSnapshot]=useState(null);
  const [physicalQty,setPhysicalQty]=useState('');
  const [physicalLocation,setPhysicalLocation]=useState('');
  const [remarks,setRemarks]=useState('');
  const [pending,setPending]=useState([]);
  const [sessions,setSessions]=useState([]);
  const [viewSession,setViewSession]=useState(null);
  const [filters,setFilters]=useState({date_from:'',date_to:'',user_filter:'',status_filter:'all'});
  const cameraRef=useRef(null);

  const loadMobileUsers=async()=>{
    try{
      const params = isMaster ? {brand_name:brand,dealer_name:dealer,branch} : (user?.role==='admin' ? {branch} : {});
      const r=await axios.get(`${API}/mobile/users`,{params});
      setMobileUsers(r.data||[]);
    }catch(e){toast.error(e.response?.data?.detail||'Mobile user list load failed')}
  };
  const loadDevices=async()=>{
    try{
      const r=await axios.get(`${API}/mobile/devices`);
      setDevices(r.data||[]);
    }catch(e){toast.error(e.response?.data?.detail||'Device list load failed')}
  };
  const loadNotificationInterval=async()=>{
    try{const r=await axios.get(`${API}/mobile/settings/notification-interval`);setNotificationInterval(r.data?.interval_minutes||30);}catch(e){/* non-blocking */}
  };
  const loadAppVersions=async()=>{
    try{const r=await axios.get(`${API}/mobile/app-versions`);setAppVersions(r.data||[]);}catch(e){/* non-blocking */}
  };

  const loadSessions=async()=>{if(!scopeReady){setSessions([]);return;}try{const r=await axios.get(`${API}/mobile/perpetual-stock/sessions`,{params:{brand,dealer,branch,...filters}});const base=r.data||[];const today=base.filter(s=>todayKey(s.created_at)===todayKey());const detailed=await Promise.all(today.map(async s=>{try{const d=await axios.get(`${API}/mobile/perpetual-stock/sessions/${s.session_id}`);return d.data}catch{return s}}));const byId=new Map(detailed.map(s=>[s.session_id,s]));setSessions(base.map(s=>byId.get(s.session_id)||s));}catch(e){toast.error(e.response?.data?.detail||'Verification history load failed')}};

  useEffect(()=>{
    setPairingResult(null);setPairingFor(null);setPending([]);setSnapshot(null);
    loadMobileUsers();loadDevices();loadNotificationInterval();loadAppVersions();loadSessions();
    /* eslint-disable-next-line */
  },[scopeBrand,scopeDealer,scopeBranch,user?.id]);

  const createMobileUser=async()=>{
    if(user?.role!=='user' && !scopeReady) return toast.error('Select an exact Brand, Dealer and Branch in the Dashboard filter first');
    if(!clean(newUserName)||!clean(newUserMobile)) return toast.error('Enter Name and Mobile Number');
    setCreatingUser(true);
    try{
      const payload = isMaster
        ? {name:newUserName,mobile_number:newUserMobile,brand_name:brand,dealer_name:dealer,branch}
        : (user?.role==='admin' ? {name:newUserName,mobile_number:newUserMobile,branch} : {name:newUserName,mobile_number:newUserMobile});
      const r=await axios.post(`${API}/mobile/users`,payload);
      toast.success(`Mobile User ${r.data.mobile_user_id} created`);
      setNewUserName('');setNewUserMobile('');
      loadMobileUsers();
    }catch(e){toast.error(e.response?.data?.detail||'Mobile user creation failed')}
    finally{setCreatingUser(false)}
  };

  const toggleUserStatus=async(mu)=>{
    try{
      await axios.put(`${API}/mobile/users/${mu.mobile_user_id}/status`,{status: mu.status==='active'?'inactive':'active'});
      toast.success(`${mu.mobile_user_id} set to ${mu.status==='active'?'inactive':'active'}`);
      loadMobileUsers();loadDevices();
    }catch(e){toast.error(e.response?.data?.detail||'Status update failed')}
  };

  const generatePairing=async(mu)=>{
    if(user?.role!=='user' && !exact(branch)) return toast.error('Select an exact Branch in the Dashboard filter before generating a pairing code');
    setPairingFor(mu.mobile_user_id);
    try{
      const r=await axios.post(`${API}/mobile/pairing/generate`,{mobile_user_id:mu.mobile_user_id,branch});
      setPairingResult({mobile_user_id:mu.mobile_user_id,...r.data});
      toast.success('Pairing code generated — valid for 10 minutes, one-time use');
    }catch(e){toast.error(e.response?.data?.detail||'Pairing code generation failed')}
    finally{setPairingFor(null)}
  };

  const setDeviceStatus=async(d,status)=>{
    try{await axios.put(`${API}/mobile/devices/${d.device_id}/status`,{status});toast.success(`Device set to ${status}`);loadDevices();loadMobileUsers();}
    catch(e){toast.error(e.response?.data?.detail||'Device update failed')}
  };

  const saveNotificationInterval=async()=>{
    setSavingInterval(true);
    try{await axios.put(`${API}/mobile/settings/notification-interval`,{interval_minutes:Number(notificationInterval)});toast.success('Notification interval updated')}
    catch(e){toast.error(e.response?.data?.detail||'Update failed')}
    finally{setSavingInterval(false)}
  };

  const publishVersion=async()=>{
    if(!clean(newVersion.version_name)||!newVersion.version_code||!clean(newVersion.apk_filename)||!clean(newVersion.apk_path))
      return toast.error('Version Name, Version Code, APK Filename and APK Path are required');
    setPublishingVersion(true);
    try{
      await axios.post(`${API}/mobile/app-versions`,{...newVersion,version_code:Number(newVersion.version_code),min_supported_version_code:Number(newVersion.min_supported_version_code)||1});
      toast.success('App version published');
      setNewVersion({version_name:'',version_code:'',apk_filename:'',apk_path:'',release_notes:'',min_supported_version_code:1,mandatory:false});
      loadAppVersions();
    }catch(e){toast.error(e.response?.data?.detail||'Publish failed')}
    finally{setPublishingVersion(false)}
  };

  const lookup=async()=>{if(!scopeReady)return toast.error('Select exact Dashboard Brand, Dealer and Branch');if(!clean(partNumber))return toast.error('Enter Part Number');try{const r=await axios.get(`${API}/mobile/perpetual-stock/lookup`,{params:{part_number:clean(partNumber),brand,dealer,branch}});setSnapshot(r.data);setPhysicalLocation(r.data.pin_location||'');setPhysicalQty('');setRemarks('')}catch(e){setSnapshot(null);toast.error(e.response?.data?.detail||'Part not found')}};
  const scan=async(e)=>{const file=e.target.files?.[0];e.target.value='';if(!file)return;if(!('TextDetector'in window))return toast.info('OCR is not supported in this browser. Enter Part Number manually.');try{const bitmap=await createImageBitmap(file);const result=await new window.TextDetector().detect(bitmap);const candidates=result.map(x=>x.rawValue).join(' ').match(/[A-Z0-9][A-Z0-9\-_/]{4,}/gi)||[];if(!candidates.length)return toast.error('Part Number not detected');setPartNumber(candidates.sort((a,b)=>b.length-a.length)[0])}catch{toast.error('OCR scan failed')}};
  const calc=useMemo(()=>{if(!snapshot||physicalQty==='')return null;const sys=Number(snapshot.system_quantity||0),phy=Number(physicalQty),mav=Number(snapshot.mav||0),diff=phy-sys;return{difference:diff,shortage_qty:diff<0?-diff:0,excess_qty:diff>0?diff:0,shortage_value:(diff<0?-diff:0)*mav,excess_value:(diff>0?diff:0)*mav}},[snapshot,physicalQty]);
  const addItem=()=>{if(!snapshot||!calc||Number.isNaN(Number(physicalQty)))return toast.error('Enter valid Physical Quantity');if(pending.some(x=>clean(x.part_number).toLowerCase()===clean(snapshot.part_number).toLowerCase()))return toast.error('Part already added');setPending(p=>[...p,{...snapshot,physical_quantity:Number(physicalQty),scanned_location:physicalLocation,remarks,...calc}]);setPartNumber('');setSnapshot(null);setPhysicalQty('');setPhysicalLocation('');setRemarks('');toast.success('Added to temporary verification list')};
  const uploadSession=async()=>{if(!pending.length)return toast.error('Add at least one item');try{const r=await axios.post(`${API}/mobile/perpetual-stock/sessions`,{brand,dealer,branch,items:pending.map(x=>({part_number:x.part_number,physical_quantity:x.physical_quantity,scanned_location:x.scanned_location,remarks:x.remarks}))});toast.success(`Uploaded ${r.data.session_id}`);setPending([]);loadSessions()}catch(e){toast.error(e.response?.data?.detail||'Upload failed')}};
  const openSession=async(s)=>{try{const r=await axios.get(`${API}/mobile/perpetual-stock/sessions/${s.session_id}`);setViewSession(r.data)}catch(e){toast.error(e.response?.data?.detail||'Session load failed')}};
  const updateCorrection=async(row,method)=>{try{const status=method==='no_action'?'pending':'corrected';const r=await axios.put(`${API}/mobile/perpetual-stock/${row.id}/correction`,{correction_status:status,correction_method:method,correction_remarks:''});setViewSession(v=>({...v,items:(v.items||[]).map(x=>x.id===row.id?r.data:x)}));toast.success('Correction updated')}catch(e){toast.error(e.response?.data?.detail||'Correction update failed')}};
  const excel=async(s)=>{try{const r=await axios.get(`${API}/mobile/perpetual-stock/sessions/${s.session_id}/excel`,{responseType:'blob'});const url=URL.createObjectURL(r.data);const a=document.createElement('a');a.href=url;a.download=`${s.session_id}.xlsx`;a.click();URL.revokeObjectURL(url)}catch(e){toast.error('Excel download failed')}};
  const todayItems=useMemo(()=>sessions.filter(s=>todayKey(s.created_at)===todayKey()).reduce((n,s)=>n+Number(s.total_items||0),0),[sessions]);

  const latestVersion = appVersions[0];

  return <div className="space-y-6">
    <div><h1 className="text-3xl font-bold">Sleeping Stock Mobile</h1><p className="text-gray-500">Mobile User &amp; device management, notifications, app versioning, and Perpetual Stock verification.</p></div>
    <div className="grid md:grid-cols-4 gap-3"><Tab active={section==='mobile'} onClick={()=>setSection('mobile')} icon={Smartphone} title="Mobile Users"/><Tab active={section==='settings'} onClick={()=>setSection('settings')} icon={Settings2} title="App &amp; Settings"/><Tab active={section==='perpetual'} onClick={()=>setSection('perpetual')} icon={ClipboardCheck} title="Perpetual Stock"/><Tab active={section==='history'} onClick={()=>setSection('history')} icon={History} title="Verification History"/></div>
    {!scopeReady && user?.role!=='user' && <div className="border border-amber-300 bg-amber-50 p-3 rounded-lg text-amber-800">Select an exact Brand, Dealer and Branch in the Dashboard filter. Mobile User creation, pairing, and Perpetual Stock all use only that selected scope.</div>}

    {section==='mobile'&&<div className="space-y-5">
      {canManage && <Card title="Onboard a Mobile User"><div className="grid md:grid-cols-3 gap-3 items-end">
        <div><label className="text-xs text-gray-500 block mb-1">Name</label><input className="border rounded h-10 px-3 w-full" value={newUserName} onChange={e=>setNewUserName(e.target.value)} placeholder="Full name"/></div>
        <div><label className="text-xs text-gray-500 block mb-1">Mobile Number</label><input className="border rounded h-10 px-3 w-full" value={newUserMobile} onChange={e=>setNewUserMobile(e.target.value)} placeholder="10-digit mobile number"/></div>
        <Button onClick={createMobileUser} disabled={creatingUser}><UserPlus className="h-4 w-4 mr-2"/>{creatingUser?'Creating...':'Create Mobile User'}</Button>
      </div>{isMaster && <p className="text-xs text-gray-500 mt-2">Uses the Dashboard's Brand / Dealer / Branch selection above.</p>}</Card>}

      {pairingResult && <div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/75 backdrop-blur-sm p-4" onMouseDown={()=>setPairingResult(null)}>
        <div className="relative w-full max-w-[720px] overflow-hidden rounded-[28px] border border-white/20 bg-gradient-to-b from-[#1b1d1c] to-[#080a09] p-6 md:p-8 text-white shadow-2xl" onMouseDown={e=>e.stopPropagation()}>
          <button aria-label="Close pairing QR" onClick={()=>setPairingResult(null)} className="absolute right-5 top-5 rounded-full border border-white/30 bg-black/30 p-2 hover:bg-white/10"><X className="h-6 w-6"/></button>
          <div className="flex items-center justify-center gap-4 pr-10">
            <img src="/sleeping-stock-logo-transparent.png" alt="Sleeping Stock" className="h-20 w-20 object-contain"/>
            <div><div className="text-3xl font-black tracking-tight"><span className="text-white">Sleeping</span><span className="text-lime-400">Stock</span></div><div className="text-sm text-gray-300">Non moving Tracking System</div></div>
          </div>
          <div className="mt-5 text-center"><h2 className="text-3xl md:text-4xl font-black">Pair Your <span className="text-lime-400">Device</span></h2><p className="mt-2 text-sm md:text-base text-gray-300">Use Sleeping Stock Mobile to scan this code or enter the manual code below.</p></div>
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

      <Card title="Mobile Users"><div className="overflow-x-auto"><table className="w-full text-sm min-w-[1400px]"><thead><tr>{['Mobile User ID','Name','Mobile','Brand','Dealer','Branch','Created By','Creator Role','Created Date','Devices','Active','Last Active','Status','Actions'].map(h=><th key={h} className="text-left p-3">{h}</th>)}</tr></thead><tbody>
        {mobileUsers.map(mu=><tr className="border-t" key={mu.mobile_user_id}>
          <td className="p-3 font-mono">{mu.mobile_user_id}</td><td>{mu.name}</td><td>{mu.mobile_number}</td><td>{mu.brand_name}</td><td>{mu.dealer_name}</td><td>{mu.branch}</td>
          <td>{mu.created_by_name}</td><td className="capitalize">{mu.created_by_role}</td><td>{fmt(mu.created_at)}</td>
          <td>{mu.paired_device_count||0}</td><td>{mu.active_device_count||0}</td><td>{mu.last_active_at?fmt(mu.last_active_at):'-'}</td>
          <td><span className={`px-2 py-1 rounded text-xs ${mu.status==='active'?'bg-green-100 text-green-700':'bg-gray-200 text-gray-600'}`}>{mu.status}</span></td>
          <td><div className="flex gap-2 flex-wrap">
            <Button size="sm" variant="outline" onClick={()=>generatePairing(mu)} disabled={pairingFor===mu.mobile_user_id || mu.status!=='active'}><QrCode className="h-4 w-4"/></Button>
            <Button size="sm" variant="outline" onClick={()=>toggleUserStatus(mu)}>{mu.status==='active'?<Ban className="h-4 w-4"/>:<CheckCircle2 className="h-4 w-4"/>}</Button>
          </div></td>
        </tr>)}
        {!mobileUsers.length && <tr><td colSpan="14" className="text-center py-8 text-gray-500">No mobile users yet for this scope.</td></tr>}
      </tbody></table></div></Card>

      <Card title="Linked Mobile Devices"><div className="overflow-x-auto"><table className="w-full text-sm min-w-[1200px]"><thead><tr>{['Mobile User','Device User Name','Device User Mobile','Device Name','Device Info','Brand','Dealer','Branch','App Version','Paired','Last Active','Status','Actions'].map(h=><th key={h} className="text-left p-3">{h}</th>)}</tr></thead><tbody>
        {devices.map(d=><tr className="border-t" key={d.device_id}>
          <td className="p-3 font-mono">{d.mobile_user_id}</td><td>{d.device_user_name||'-'}</td><td>{d.device_user_mobile||'-'}</td><td>{d.device_name}</td><td>{d.device_info}</td><td>{d.brand_name}</td><td>{d.dealer_name}</td><td>{d.branch}</td>
          <td>{d.app_version||'-'}</td><td>{fmt(d.paired_at)}</td><td>{d.last_active_at?fmt(d.last_active_at):'-'}</td>
          <td><span className={`px-2 py-1 rounded text-xs ${d.status==='active'?'bg-green-100 text-green-700':d.status==='removed'?'bg-red-100 text-red-700':'bg-gray-200 text-gray-600'}`}>{d.status}</span></td>
          <td><div className="flex gap-2">
            {d.status==='active'?<Button size="sm" variant="outline" onClick={()=>setDeviceStatus(d,'inactive')}><Ban className="h-4 w-4"/></Button>:d.status==='inactive'?<Button size="sm" onClick={()=>setDeviceStatus(d,'active')}>Activate</Button>:null}
            {d.status!=='removed' && <Button size="sm" variant="outline" onClick={()=>{if(window.confirm('Remove this device permanently? The mobile user will need a new pairing code to reconnect.'))setDeviceStatus(d,'removed')}}><Trash2 className="h-4 w-4"/></Button>}
          </div></td>
        </tr>)}
        {!devices.length && <tr><td colSpan="13" className="text-center py-8 text-gray-500">No paired devices yet.</td></tr>}
      </tbody></table></div></Card>
    </div>}

    {section==='settings'&&<div className="space-y-5">
      <Card title="Notification Repeat Interval"><div className="flex items-center gap-3">
        <input type="number" min="1" className="border rounded h-10 px-3 w-32" value={notificationInterval} disabled={!isMaster} onChange={e=>setNotificationInterval(e.target.value)}/>
        <span className="text-sm text-gray-500">minutes between repeat alerts for an unaccepted branch request</span>
        {isMaster && <Button onClick={saveNotificationInterval} disabled={savingInterval}><RefreshCw className="h-4 w-4 mr-2"/>{savingInterval?'Saving...':'Save'}</Button>}
      </div></Card>

      {isMaster && <Card title="Publish App Version"><div className="grid md:grid-cols-3 gap-3">
        <input className="border rounded h-10 px-3" placeholder="Version Name (1.0.1)" value={newVersion.version_name} onChange={e=>setNewVersion({...newVersion,version_name:e.target.value})}/>
        <input type="number" className="border rounded h-10 px-3" placeholder="Android Version Code" value={newVersion.version_code} onChange={e=>setNewVersion({...newVersion,version_code:e.target.value})}/>
        <input className="border rounded h-10 px-3" placeholder="APK Filename" value={newVersion.apk_filename} onChange={e=>setNewVersion({...newVersion,apk_filename:e.target.value})}/>
        <input className="border rounded h-10 px-3 md:col-span-2" placeholder="APK Download Path / URL" value={newVersion.apk_path} onChange={e=>setNewVersion({...newVersion,apk_path:e.target.value})}/>
        <input type="number" className="border rounded h-10 px-3" placeholder="Min Supported Version Code" value={newVersion.min_supported_version_code} onChange={e=>setNewVersion({...newVersion,min_supported_version_code:e.target.value})}/>
        <input className="border rounded h-10 px-3 md:col-span-2" placeholder="Release Notes" value={newVersion.release_notes} onChange={e=>setNewVersion({...newVersion,release_notes:e.target.value})}/>
        <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={newVersion.mandatory} onChange={e=>setNewVersion({...newVersion,mandatory:e.target.checked})}/>Mandatory update</label>
        <Button onClick={publishVersion} disabled={publishingVersion} className="md:col-span-3"><Upload className="h-4 w-4 mr-2"/>{publishingVersion?'Publishing...':'Publish Version'}</Button>
      </div></Card>}

      <Card title="APK Download"><div className="flex items-center justify-between">
        <div>{latestVersion ? <>Current version: <b>{latestVersion.version_name}</b> (code {latestVersion.version_code}){latestVersion.mandatory && <span className="ml-2 text-xs bg-red-100 text-red-700 px-2 py-1 rounded">Mandatory</span>}<div className="text-xs text-gray-500 mt-1">{latestVersion.release_notes}</div></> : 'No app version published yet.'}</div>
        {latestVersion && <a href={latestVersion.apk_path} target="_blank" rel="noreferrer"><Button><Download className="h-4 w-4 mr-2"/>Download APK</Button></a>}
      </div></Card>

      <Card title="Published Versions"><SimpleTable headers={['Version','Code','Filename','Mandatory','Released']} rows={appVersions.map(v=>[v.version_name,v.version_code,v.apk_filename,v.mandatory?'Yes':'No',fmt(v.release_date)])}/></Card>
    </div>}

    {section==='perpetual'&&<div className="space-y-5">
      <Card title="Perpetual Stock Lookup"><div className="flex flex-wrap gap-2"><input className="border rounded h-10 px-3 flex-1 min-w-64" value={partNumber} onChange={e=>setPartNumber(e.target.value.toUpperCase())} placeholder="Manual Part Number"/><Button onClick={lookup}><ScanLine className="h-4 w-4 mr-2"/>Lookup</Button><Button variant="outline" onClick={()=>cameraRef.current?.click()}><Camera className="h-4 w-4 mr-2"/>Camera Scan</Button><input ref={cameraRef} type="file" accept="image/*" capture="environment" className="hidden" onChange={scan}/></div>
      {snapshot&&<div className="mt-4 space-y-4"><div className="grid md:grid-cols-4 gap-3"><Info label="Part Number" value={snapshot.part_number}/><Info label="Part Name" value={snapshot.part_name}/><Info label="System Quantity" value={snapshot.system_quantity}/><Info label="MAV" value={money(snapshot.mav)}/><Info label="PIN Location" value={snapshot.pin_location||'-'}/><Info label="Physical Quantity" value={<input type="number" className="border rounded h-9 px-2 w-full" value={physicalQty} onChange={e=>setPhysicalQty(e.target.value)}/>}/><Info label="Physical Location" value={<input className="border rounded h-9 px-2 w-full" value={physicalLocation} onChange={e=>setPhysicalLocation(e.target.value)}/>}/><Info label="Remarks" value={<input className="border rounded h-9 px-2 w-full" value={remarks} onChange={e=>setRemarks(e.target.value)}/>} /></div>{calc&&<div className="grid md:grid-cols-5 gap-3"><Info label="Difference" value={calc.difference}/><Info label="Shortage Qty" value={calc.shortage_qty}/><Info label="Excess Qty" value={calc.excess_qty}/><Info label="Shortage Value" value={money(calc.shortage_value)}/><Info label="Excess Value" value={money(calc.excess_value)}/></div>}<Button onClick={addItem}><Plus className="h-4 w-4 mr-2"/>Add to Verification List</Button></div>}</Card>
      <Card title={`Temporary Verification List (${pending.length})`}><DetailTable rows={pending} removable onRemove={i=>setPending(p=>p.filter((_,x)=>x!==i))}/><div className="flex justify-end mt-4"><Button onClick={uploadSession} disabled={!pending.length}><Upload className="h-4 w-4 mr-2"/>Finish Upload</Button></div></Card>
      <Card title="Today's Verification"><p className="text-sm text-gray-500 mb-3">All verified line items for today. Total items: {todayItems}</p><DetailTable rows={sessions.filter(s=>todayKey(s.created_at)===todayKey()).flatMap(s=>s.items||[])} empty="Open History and View a session to inspect its items."/></Card>
    </div>}

    {section==='history'&&<div className="space-y-5"><Card title="Verification History Filters"><div className="grid md:grid-cols-4 gap-3"><input type="date" className="border rounded h-10 px-3" value={filters.date_from} onChange={e=>setFilters({...filters,date_from:e.target.value})}/><input type="date" className="border rounded h-10 px-3" value={filters.date_to} onChange={e=>setFilters({...filters,date_to:e.target.value})}/><select className="border rounded h-10 px-3" value={filters.user_filter} onChange={e=>setFilters({...filters,user_filter:e.target.value})}><option value="">All Users</option>{mobileUsers.map(u=><option key={u.mobile_user_id} value={u.mobile_user_id}>{u.name}</option>)}</select><select className="border rounded h-10 px-3" value={filters.status_filter} onChange={e=>setFilters({...filters,status_filter:e.target.value})}><option value="all">All Status</option><option value="submitted">Submitted</option></select></div><Button className="mt-3" onClick={loadSessions}>Apply Filters</Button></Card>
      <Card title="Perpetual Verification History"><div className="overflow-x-auto"><table className="w-full text-sm min-w-[1350px]"><thead><tr>{['Session ID','Date','Brand','Dealer','Branch','User','Total Items','Shortage Qty','Shortage Value','Excess Qty','Excess Value','Status','View','Excel'].map(h=><th key={h} className="text-left p-3">{h}</th>)}</tr></thead><tbody>{sessions.map(s=><tr key={s.session_id} className="border-t"><td className="p-3 font-semibold">{s.session_id}</td><td>{fmt(s.created_at)}</td><td>{s.brand_name}</td><td>{s.dealer_name}</td><td>{s.branch}</td><td>{s.verified_by_name}</td><td>{s.total_items}</td><td>{s.total_shortage_qty}</td><td>{money(s.total_shortage_value)}</td><td>{s.total_excess_qty}</td><td>{money(s.total_excess_value)}</td><td>{s.status}</td><td><Button size="sm" variant="outline" onClick={()=>openSession(s)}><Eye className="h-4 w-4"/></Button></td><td><Button size="sm" variant="outline" onClick={()=>excel(s)}><Download className="h-4 w-4"/></Button></td></tr>)}</tbody></table></div></Card></div>}

    {viewSession&&<div className="fixed inset-0 bg-black/50 z-50 p-4 overflow-auto"><div className="bg-white rounded-xl max-w-7xl mx-auto p-5"><div className="flex justify-between mb-4"><div><h2 className="text-xl font-bold">{viewSession.session_id}</h2><p className="text-gray-500">{fmt(viewSession.created_at)}</p></div><Button variant="outline" onClick={()=>setViewSession(null)}><X className="h-4 w-4"/></Button></div><DetailTable rows={viewSession.items||[]} canManage={canManage} onCorrection={updateCorrection}/></div></div>}
  </div>
}

const Card=({title,children})=><div className="bg-white rounded-xl border p-5"><h2 className="font-bold text-lg mb-4">{title}</h2>{children}</div>;
const Tab=({active,onClick,icon:Icon,title})=><button onClick={onClick} className={`border rounded-xl p-4 flex gap-3 items-center ${active?'bg-green-50 ring-2 ring-green-500':'bg-white'}`}><Icon className="h-5 w-5 text-green-600"/><b>{title}</b></button>;
const Info=({label,value})=><div className="border rounded-lg p-3 bg-gray-50"><div className="text-xs text-gray-500 mb-1">{label}</div><div className="font-semibold">{value}</div></div>;
const SimpleTable=({headers,rows})=><div className="overflow-x-auto"><table className="w-full text-sm"><thead><tr>{headers.map(h=><th key={h} className="text-left p-3">{h}</th>)}</tr></thead><tbody>{rows.map((r,i)=><tr className="border-t" key={i}>{r.map((v,j)=><td className="p-3" key={j}>{v}</td>)}</tr>)}{!rows.length&&<tr><td colSpan={headers.length} className="text-center py-8 text-gray-500">No records yet.</td></tr>}</tbody></table></div>;
function DetailTable({rows=[],removable=false,onRemove,empty='No verified items',canManage=false,onCorrection}){return <div className="overflow-x-auto"><table className="w-full text-sm min-w-[1500px]"><thead><tr>{['Part Number','Part Name','MAV','System Qty','Physical Qty','Difference','Shortage Qty','Excess Qty','Shortage Value','Excess Value','PIN Location','Status','Remarks',...(removable?['Action']:[]),...(canManage?['Correction']:[])].map(h=><th key={h} className="text-left p-3">{h}</th>)}</tr></thead><tbody>{rows.map((r,i)=><tr className="border-t" key={r.id||r.part_number||i}><td className="p-3 font-medium">{r.part_number}</td><td>{r.part_name}</td><td>{money(r.mav)}</td><td>{r.system_quantity}</td><td>{r.physical_quantity}</td><td>{r.difference}</td><td>{r.shortage_qty}</td><td>{r.excess_qty}</td><td>{money(r.shortage_value)}</td><td>{money(r.excess_value)}</td><td>{r.pin_location||r.system_location||'-'}</td><td>{r.overall_status||r.quantity_status||'-'}</td><td>{r.remarks||'-'}</td>{removable&&<td><Button size="sm" variant="outline" onClick={()=>onRemove(i)}><Trash2 className="h-4 w-4"/></Button></td>}{canManage&&<td><select className="border rounded h-9 px-2" defaultValue="" onChange={e=>{if(e.target.value)onCorrection(r,e.target.value);e.target.value=''}}><option value="">Update...</option><option value="system_corrected">System corrected</option><option value="physical_relocated">Physical relocated</option><option value="both">Both corrected</option><option value="no_action">No action</option></select></td>}</tr>)}{!rows.length&&<tr><td colSpan="14" className="text-center py-8 text-gray-500">{empty}</td></tr>}</tbody></table></div>}
