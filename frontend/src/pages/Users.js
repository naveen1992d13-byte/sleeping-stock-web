import React, { useEffect, useMemo, useState } from "react";
import {
  Users,
  UserPlus,
  Settings,
  Plus,
  Edit,
  Trash2,
  Save,
  Search,
  KeyRound,
  Power,
  Eye,
  EyeOff,
  Building2,
  MapPin,
  ShieldCheck,
  Upload,
  Download,
} from "lucide-react";
import { useOutletContext } from "react-router-dom";
import { Button } from "../components/ui/button";
import { useAuth, API } from "../App";
import { APPLICATION_PERMISSION_LABELS } from "../config/menuConfig";
import { toast } from "sonner";
import { NmtsConfirmDialog } from "../components/NmtsConfirmDialog";
import { NmtsModal } from "../components/NmtsModal";

const COLORS = {
  page: "#D1FAE5",
  card: "#DCE8D1",
  header: "#FFFFFF",
  primary: "#059669",
  active: "#8BC34A",
  button: "#708A5D",
  border: "#C8D5BF",
  text: "#263326",
  muted: "#5F6B5A",
  danger: "#DC2626",
};

const MENU_LIST = APPLICATION_PERMISSION_LABELS;

async function apiErrorMessage(res, fallback) {
  try {
    const data = await res.json();
    const detail = data?.detail;
    if (typeof detail === "string" && detail.trim()) return detail;
    if (Array.isArray(detail) && detail.length) {
      return detail.map((item) => item?.msg || item?.detail || String(item)).filter(Boolean).join("; ");
    }
  } catch {
    /* ignore non-JSON bodies */
  }
  return fallback;
}

function networkErrorMessage(error) {
  const msg = String(error?.message || error || "");
  if (error?.name === "TypeError" || msg.toLowerCase().includes("failed to fetch")) {
    return "Could not reach the backend. Confirm the API is running in this same environment.";
  }
  return msg || "Request failed";
}

export default function UsersPage() {
  const { user } = useAuth();
  const outletScope = useOutletContext() || {};
  const isMaster = user?.role === "master";
  const isAdmin = user?.role === "admin";
  const isNormalUser = user?.role === "user";

  const scopeBrand = outletScope.scopeBrand || "All Brands";
  const scopeDealer = outletScope.scopeDealer || "All Dealers";
  const scopeBranch = outletScope.scopeBranch || "All Branches";
  const isAllValue = (value) => !value || String(value).startsWith("All ") || value === "N/A";
  const recordBrand = (row) => String(row?.brand || row?.brand_name || "").trim();
  const recordDealer = (row) => String(row?.dealer || row?.dealer_name || "").trim();
  const dealerQuery = (name, brand) => {
    const params = new URLSearchParams();
    if (brand) params.set("brand", brand);
    const qs = params.toString();
    return `${API}/masters/dealers/${encodeURIComponent(name)}${qs ? `?${qs}` : ""}`;
  };
  const branchQuery = (name, dealer, brand) => {
    const params = new URLSearchParams();
    if (dealer) params.set("dealer", dealer);
    if (brand) params.set("brand", brand);
    const qs = params.toString();
    return `${API}/masters/branches/${encodeURIComponent(name)}${qs ? `?${qs}` : ""}`;
  };

  const [activeTab, setActiveTab] = useState("list");
  const [settingsTab, setSettingsTab] = useState("brand");
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState("all");

  const [users, setUsers] = useState([]);
  const [states, setStates] = useState([]);
  const [brands, setBrands] = useState([]);
  const [dealers, setDealers] = useState([]);
  const [branches, setBranches] = useState([]);

  const [templates, setTemplates] = useState([]);
  const [templateForm, setTemplateForm] = useState({
    brand: "",
    templateType: "Product Hub",
    file: null,
  });

  const [stateForm, setStateForm] = useState({ code: "", name: "" });
  const [brandForm, setBrandForm] = useState({ code: "", name: "" });
  const [dealerForm, setDealerForm] = useState({ name: "", brand: "" });
  const [branchForm, setBranchForm] = useState({ brand: "", dealer: "", name: "" });

  const [editingState, setEditingState] = useState(null);
  const [editingBrand, setEditingBrand] = useState(null);
  const [editingDealer, setEditingDealer] = useState(null);
  const [editingBranch, setEditingBranch] = useState(null);

  const blankUser = {
    userId: "",
    name: "",
    mobile: "",
    email: "",
    role: "user",
    state: "",
    brand: "",
    dealer: "",
    branch: "",
    password: "",
    confirmPassword: "",
    status: "active",
    permissions: [],
  };

  const [newUser, setNewUser] = useState(blankUser);
  const [editingUser, setEditingUser] = useState(null);

  const [confirmDlg, setConfirmDlg] = useState(null);
  const [confirmBusy, setConfirmBusy] = useState(false);
  const [resetTarget, setResetTarget] = useState(null);
  const [resetPasswordValue, setResetPasswordValue] = useState("");
  const [resetPasswordConfirm, setResetPasswordConfirm] = useState("");
  const [showResetPassword, setShowResetPassword] = useState(false);
  const [showResetPasswordConfirm, setShowResetPasswordConfirm] = useState(false);
  const [resetSubmitting, setResetSubmitting] = useState(false);

 const token =
  localStorage.getItem("token") ||
  localStorage.getItem("access_token") ||
  localStorage.getItem("accessToken") ||
  localStorage.getItem("jwt") ||
  "";
  const headers = {
    "Content-Type": "application/json",
    Authorization: `Bearer ${token}`,
  };

  const authHeaders = {
    Authorization: `Bearer ${token}`,
  };

  const allowedPermissionList = useMemo(() => {
    if (isMaster) return MENU_LIST;

    if (isAdmin) {
      const p = user?.permissions;

      if (Array.isArray(p)) return p;

      if (p && typeof p === "object") {
        return Object.keys(p).filter((key) => Boolean(p[key]));
      }

      return [];
    }

    return [];
  }, [isMaster, isAdmin, user]);

  const loadData = async () => {
    try {
      const [stRes, bRes, dRes, brRes, uRes] = await Promise.all([
        fetch(`${API}/masters/states`, { headers }),
        fetch(`${API}/masters/brands`, { headers }),
        fetch(`${API}/masters/dealers`, { headers }),
        fetch(`${API}/masters/branches`, { headers }),
        fetch(`${API}/users/list`, { headers }),
      ]);

      const [st, b, d, br, u] = await Promise.all([
        stRes.json().catch(() => []),
        bRes.json().catch(() => []),
        dRes.json().catch(() => []),
        brRes.json().catch(() => []),
        uRes.json().catch(() => []),
      ]);

      setStates(Array.isArray(st) ? st : []);
      setBrands(Array.isArray(b) ? b : []);
      setDealers(Array.isArray(d) ? d : []);
      setBranches(Array.isArray(br) ? br : []);
      setUsers(Array.isArray(u) ? u : []);
      window.dispatchEvent(new Event("nmts-masters-changed"));
    } catch (error) {
      console.error("User Hub data load failed", error);
      toast.error(networkErrorMessage(error));
    }
  };

  const loadTemplates = async () => {
    try {
      const res = await fetch(`${API}/templates`, { headers });
      const data = await res.json();
      setTemplates(Array.isArray(data) ? data : []);
    } catch {
      setTemplates([]);
    }
  };

  useEffect(() => {
    loadData();
    loadTemplates();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (isAdmin) {
      setNewUser((prev) => ({
        ...prev,
        role: "user",
        state: user?.state || "",
        brand: user?.brand || "",
        dealer: user?.dealer || user?.group || "",
        branch: user?.branch || user?.location || "",
      }));
    }
  }, [isAdmin, user]);

  const visibleUsers = useMemo(() => {
    if (isMaster) return users;

    if (isAdmin) {
      const adminBrand = user?.brand || "";
      const adminDealer = user?.dealer || user?.group || "";

      return users.filter((u) => {
        const uDealer = u.dealer || u.group || "";
        return u.brand === adminBrand && uDealer === adminDealer;
      });
    }

    if (isNormalUser) {
      const ownBrand = user?.brand || "";
      const ownDealer = user?.dealer || user?.group || "";
      const ownBranch = user?.branch || user?.location || "";

      return users.filter((u) => {
        const uDealer = u.dealer || u.group || "";
        const uBranch = u.branch || u.location || "";
        return u.brand === ownBrand && uDealer === ownDealer && uBranch === ownBranch;
      });
    }

    return [];
  }, [users, isMaster, isAdmin, isNormalUser, user]);

  const scopedUsers = useMemo(() => visibleUsers.filter((u) => {
    const uBrand = u.brand || "";
    const uDealer = u.dealer || u.group || "";
    const uBranch = u.branch || u.location || "";
    return (isAllValue(scopeBrand) || uBrand === scopeBrand) &&
      (isAllValue(scopeDealer) || uDealer === scopeDealer) &&
      (isAllValue(scopeBranch) || uBranch === scopeBranch);
  }), [visibleUsers, scopeBrand, scopeDealer, scopeBranch]);

  const stats = useMemo(() => {
    const scopedDealers = dealers.filter((d) => (isAllValue(scopeBrand) || recordBrand(d) === scopeBrand) && (isAllValue(scopeDealer) || d.name === scopeDealer));
    const scopedBranches = branches.filter((b) => (
      (isAllValue(scopeBrand) || recordBrand(b) === scopeBrand) &&
      (isAllValue(scopeDealer) || recordDealer(b) === scopeDealer) &&
      (isAllValue(scopeBranch) || b.name === scopeBranch)
    ));
    const scopedBrands = brands.filter(b => isAllValue(scopeBrand) || b.name === scopeBrand);
    const scopedStates = states.filter(st => isAllValue(scopeBrand) || scopedUsers.some(u => u.state === st.name || u.state === st.code));
    return {
      totalUsers: scopedUsers.length,
      activeUsers: scopedUsers.filter((x) => String(x.status || "active").toLowerCase() === "active").length,
      inactiveUsers: scopedUsers.filter((x) => String(x.status || "active").toLowerCase() !== "active").length,
      states: scopedStates.length, brands: scopedBrands.length, dealers: scopedDealers.length, branches: scopedBranches.length,
    };
  }, [scopedUsers, states, brands, dealers, branches, scopeBrand, scopeDealer, scopeBranch]);

  const filteredUsers = scopedUsers.filter((u) => {
    const s = search.toLowerCase();
    const uBrand = u.brand || "";
    const uDealer = u.dealer || u.group || "";
    const uBranch = u.branch || u.location || "";
    const userStatus = String(u.status || "active").toLowerCase();

    const statusOk =
      statusFilter === "all" ||
      (statusFilter === "active" && userStatus === "active") ||
      (statusFilter === "inactive" && userStatus !== "active");

    const brandOk = isAllValue(scopeBrand) || uBrand === scopeBrand;
    const dealerOk = isAllValue(scopeDealer) || uDealer === scopeDealer;
    const branchOk = isAllValue(scopeBranch) || uBranch === scopeBranch;

    return statusOk && brandOk && dealerOk && branchOk && (
      (u.userId || u.user_id || "").toLowerCase().includes(s) ||
      (u.name || u.username || "").toLowerCase().includes(s) ||
      (u.mobile || u.phone || "").includes(s) ||
      (u.email || "").toLowerCase().includes(s) ||
      (u.state || "").toLowerCase().includes(s) ||
      uBrand.toLowerCase().includes(s) ||
      uDealer.toLowerCase().includes(s) ||
      uBranch.toLowerCase().includes(s)
    );
  });

  const availableDealersForUser = useMemo(() => {
    const selectedBrand = newUser.brand || user?.brand || "";
    if (!selectedBrand) return [];
    return dealers
      .filter((d) => recordBrand(d) === selectedBrand)
      .map((d) => d.name);
  }, [dealers, newUser.brand, user]);

  const availableBranchesForNewUser = useMemo(() => {
    const selectedDealer = newUser.dealer || user?.dealer || user?.group || "";
    const selectedBrand = newUser.brand || user?.brand || "";
    if (!selectedDealer || !selectedBrand) return [];
    return branches
      .filter((b) => recordDealer(b) === selectedDealer && recordBrand(b) === selectedBrand)
      .map((b) => b.name);
  }, [branches, newUser.dealer, newUser.brand, user]);

  const availableDealersForBranchForm = useMemo(() => {
    if (!branchForm.brand) return [];
    return dealers.filter((d) => recordBrand(d) === branchForm.brand).map((d) => d.name);
  }, [dealers, branchForm.brand]);

  const getStateCode = (stateName) => {
    const value = String(stateName || "").trim();
    if (!value) return "";
    const selectedState = states.find(
      (st) => String(st.name || "").trim() === value || String(st.code || "").trim().toUpperCase() === value.toUpperCase()
    );
    return selectedState?.code ? String(selectedState.code).trim().toUpperCase() : "";
  };

  const getBrandCode = (brandName) => {
    const value = String(brandName || "").trim();
    if (!value) return "";
    const selectedBrand = brands.find(
      (b) => String(b.name || "").trim() === value || String(b.code || "").trim().toUpperCase() === value.toUpperCase()
    );
    return selectedBrand?.code ? String(selectedBrand.code).trim().toUpperCase() : "";
  };

  const generateUserId = async (stateName, brandName) => {
    try {
      const stateCode = getStateCode(stateName);
      const brandCode = getBrandCode(brandName);
      if (!stateCode || !brandCode) return "";

      const params = new URLSearchParams({
        state_code: stateCode,
        brand_code: brandCode,
      });
      const res = await fetch(`${API}/users/generate-id?${params.toString()}`, { headers });
      const data = await res.json().catch(() => ({}));
      return res.ok ? data.user_id || "" : "";
    } catch {
      return "";
    }
  };

  const handleStateChange = async (stateName) => {
    if (editingUser) {
      setNewUser({ ...newUser, state: stateName });
      return;
    }
    const id = newUser.brand ? await generateUserId(stateName, newUser.brand) : "";
    setNewUser({ ...newUser, state: stateName, userId: id });
  };

  const handleBrandChange = async (brandName) => {
    const id = (!editingUser && newUser.state) ? await generateUserId(newUser.state, brandName) : (editingUser ? newUser.userId : "");
    setNewUser({ ...newUser, brand: brandName, dealer: "", branch: "", userId: id || newUser.userId });
  };

  const togglePermission = (menu) => {
    setNewUser((prev) => ({
      ...prev,
      permissions: prev.permissions.includes(menu)
        ? prev.permissions.filter((x) => x !== menu)
        : [...prev.permissions, menu],
    }));
  };

  const resetUserForm = () => {
    setEditingUser(null);
    if (isAdmin) {
      setNewUser({
        ...blankUser,
        role: "user",
        state: user?.state || "",
        brand: user?.brand || "",
        dealer: user?.dealer || user?.group || "",
        branch: user?.branch || user?.location || "",
      });
      return;
    }

    setNewUser(blankUser);
  };

  const openConfirm = (cfg) => setConfirmDlg(cfg);

  const handleConfirmDialog = async () => {
    if (!confirmDlg?.onConfirm || confirmBusy) return;
    const action = confirmDlg.onConfirm;
    setConfirmDlg(null);
    setConfirmBusy(true);
    try {
      await action();
    } catch (error) {
      toast.error(networkErrorMessage(error));
    } finally {
      setConfirmBusy(false);
    }
  };

  const addOrUpdateState = async () => {
    if (!stateForm.code || !stateForm.name) return toast.warning("State Code and Name required");

    try {
      const method = editingState ? "PUT" : "POST";
      const url = editingState
        ? `${API}/masters/states/${encodeURIComponent(editingState.code)}`
        : `${API}/masters/states`;

      const res = await fetch(url, { method, headers, body: JSON.stringify(stateForm) });
      if (!res.ok) return toast.error(await apiErrorMessage(res, "State save failed"));

      setStateForm({ code: "", name: "" });
      setEditingState(null);
      await loadData();
    } catch (error) {
      toast.error(networkErrorMessage(error));
    }
  };

  const performDeleteState = async (code) => {
    try {
      const res = await fetch(`${API}/masters/states/${encodeURIComponent(code)}`, { method: "DELETE", headers });
      if (!res.ok) return toast.error(await apiErrorMessage(res, "State delete failed"));
      await loadData();
    } catch (error) {
      toast.error(networkErrorMessage(error));
    }
  };

  const deleteState = (code) => {
    openConfirm({
      title: "Delete State",
      message: "Delete this State?",
      confirmLabel: "Delete",
      variant: "danger",
      onConfirm: () => performDeleteState(code),
    });
  };

  const addOrUpdateBrand = async () => {
    if (!brandForm.code || !brandForm.name) return toast.warning("Brand Code and Name required");

    try {
      const method = editingBrand ? "PUT" : "POST";
      const url = editingBrand
        ? `${API}/masters/brands/${encodeURIComponent(editingBrand.code)}`
        : `${API}/masters/brands`;

      const res = await fetch(url, { method, headers, body: JSON.stringify(brandForm) });
      if (!res.ok) return toast.error(await apiErrorMessage(res, "Brand save failed"));

      setBrandForm({ code: "", name: "" });
      setEditingBrand(null);
      await loadData();
    } catch (error) {
      toast.error(networkErrorMessage(error));
    }
  };

  const performDeleteBrand = async (code) => {
    try {
      const res = await fetch(`${API}/masters/brands/${encodeURIComponent(code)}`, { method: "DELETE", headers });
      if (!res.ok) return toast.error(await apiErrorMessage(res, "Brand delete failed"));
      await loadData();
    } catch (error) {
      toast.error(networkErrorMessage(error));
    }
  };

  const deleteBrand = (code) => {
    openConfirm({
      title: "Delete Brand",
      message: "Delete this Brand?",
      confirmLabel: "Delete",
      variant: "danger",
      onConfirm: () => performDeleteBrand(code),
    });
  };

  const addOrUpdateDealer = async () => {
    if (!dealerForm.brand) return toast.warning("Brand is required");
    if (!dealerForm.name) return toast.warning("Dealer Name required");

    try {
      const method = editingDealer ? "PUT" : "POST";
      const url = editingDealer
        ? dealerQuery(editingDealer.name, editingDealer.brand || editingDealer.brand_name || "")
        : `${API}/masters/dealers`;

      const res = await fetch(url, { method, headers, body: JSON.stringify(dealerForm) });
      if (!res.ok) return toast.error(await apiErrorMessage(res, "Dealer save failed"));

      setDealerForm({ name: "", brand: "" });
      setEditingDealer(null);
      await loadData();
    } catch (error) {
      toast.error(networkErrorMessage(error));
    }
  };

  const performDeleteDealer = async (row) => {
    const name = typeof row === "string" ? row : row?.name;
    const brand = typeof row === "string" ? "" : (row?.brand || row?.brand_name || "");
    const res = await fetch(dealerQuery(name, brand), { method: "DELETE", headers });
    if (!res.ok) return toast.error((await res.json()).detail || "Dealer delete failed");
    loadData();
  };

  const deleteDealer = (row) => {
    openConfirm({
      title: "Delete Dealer",
      message: "Delete this Dealer?",
      confirmLabel: "Delete",
      variant: "danger",
      onConfirm: () => performDeleteDealer(row),
    });
  };

  const addOrUpdateBranch = async () => {
    if (!branchForm.brand) return toast.warning("Please select Brand");
    if (!branchForm.dealer || !branchForm.name) return toast.warning("Dealer and Branch required");

    const method = editingBranch ? "PUT" : "POST";
    const url = editingBranch
      ? branchQuery(editingBranch.name, editingBranch.dealer || editingBranch.dealer_name, editingBranch.brand || editingBranch.brand_name || branchForm.brand)
      : `${API}/masters/branches`;

    const res = await fetch(url, { method, headers, body: JSON.stringify(branchForm) });
    if (!res.ok) return toast.error((await res.json()).detail || "Branch save failed");

    setBranchForm({ brand: "", dealer: "", name: "" });
    setEditingBranch(null);
    loadData();
  };

  const performDeleteBranch = async (row) => {
    const name = typeof row === "string" ? row : row?.name;
    const dealer = typeof row === "string" ? "" : (row?.dealer || row?.dealer_name || "");
    const brand = typeof row === "string" ? "" : (row?.brand || row?.brand_name || "");
    const res = await fetch(branchQuery(name, dealer, brand), { method: "DELETE", headers });
    if (!res.ok) return toast.error((await res.json()).detail || "Branch delete failed");
    loadData();
  };

  const deleteBranch = (row) => {
    openConfirm({
      title: "Delete Branch",
      message: "Delete this Branch?",
      confirmLabel: "Delete",
      variant: "danger",
      onConfirm: () => performDeleteBranch(row),
    });
  };

  const openEditUser = (item) => {
    if (isNormalUser) return toast.warning("You are not allowed to edit users");
    if (isAdmin && item.role !== "user") return toast.warning("Admin can edit only User role");
    setEditingUser(item);
    setNewUser({
      userId: item.userId || item.user_id || "",
      name: item.name || item.username || "",
      mobile: item.mobile || item.phone || "",
      email: item.email || "",
      role: item.role || "user",
      state: item.state || "",
      brand: item.brand || "",
      dealer: item.dealer || item.group || "",
      branch: item.branch || item.location || "",
      password: "",
      confirmPassword: "",
      status: item.status || "active",
      permissions: Array.isArray(item.permissions) ? item.permissions : [],
    });
    setActiveTab("add");
  };

  const saveUser = async () => {
    if (isNormalUser) return toast.warning("You are not allowed to create users");
    if (isAdmin && newUser.role !== "user") return toast.warning("Admin can create only User role");
    if (!editingUser && newUser.password !== newUser.confirmPassword) return toast.warning("Password not matching");

    const payload = isAdmin
      ? {
          ...newUser,
          role: "user",
          state: user?.state || newUser.state,
          brand: user?.brand || newUser.brand,
          dealer: user?.dealer || user?.group || newUser.dealer,
          branch: newUser.branch || user?.branch || user?.location || "",
        }
      : newUser;

    if (!payload.state) return toast.warning("Please select State");
    if (!payload.brand) return toast.warning("Please select Brand");
    if (!payload.dealer) return toast.warning("Please select Dealer");
    if (!payload.branch) return toast.warning("Please select Branch");

    if (editingUser) {
      const updateBody = {
        name: payload.name,
        mobile: payload.mobile,
        email: payload.email,
        role: payload.role,
        state: payload.state,
        brand: payload.brand,
        dealer: payload.dealer,
        branch: payload.branch,
        status: payload.status,
        permissions: payload.permissions,
      };
      const res = await fetch(`${API}/users/hub/${editingUser.id}`, {
        method: "PUT",
        headers,
        body: JSON.stringify(updateBody),
      });
      if (!res.ok) return toast.error((await res.json()).detail || "User update failed");
      toast.success("User updated successfully");
      resetUserForm();
      setActiveTab("list");
      loadData();
      return;
    }

    const res = await fetch(`${API}/users/create`, {
      method: "POST",
      headers,
      body: JSON.stringify(payload),
    });

    if (!res.ok) return toast.error((await res.json()).detail || "User save failed");

    toast.success("User created successfully");
    resetUserForm();
    setActiveTab("list");
    loadData();
  };

  const toggleUserStatus = async (item) => {
    const id = item.id;
    if (!id) return toast.error("User ID not found");

    const res = await fetch(`${API}/profile/${id}/status`, {
      method: "PUT",
      headers,
    });

    if (!res.ok) return toast.error((await res.json()).detail || "Status update failed");
    loadData();
  };

  const performDeleteUser = async (item) => {
    const res = await fetch(`${API}/users/${item.id}`, {
      method: "DELETE",
      headers,
    });

    if (!res.ok) return toast.error((await res.json()).detail || "User delete failed");
    loadData();
  };

  const deleteUser = (item) => {
    if (!isMaster) return toast.warning("Only Master can delete users");
    openConfirm({
      title: "Delete User",
      message: "Delete this user?",
      confirmLabel: "Delete",
      variant: "danger",
      onConfirm: () => performDeleteUser(item),
    });
  };

  const openResetPasswordModal = (item) => {
    if (!isMaster && !isAdmin) {
      return toast.warning("Not allowed");
    }
    setResetTarget(item);
    setResetPasswordValue("");
    setResetPasswordConfirm("");
    setShowResetPassword(false);
    setShowResetPasswordConfirm(false);
  };

  const closeResetPasswordModal = () => {
    if (resetSubmitting) return;
    setResetTarget(null);
    setResetPasswordValue("");
    setResetPasswordConfirm("");
  };

  const submitResetPassword = async () => {
    if (!resetTarget?.id || resetSubmitting) return;
    if (resetPasswordValue !== resetPasswordConfirm) {
      return toast.warning("Password not matching");
    }
    if (!resetPasswordValue) return;

    setResetSubmitting(true);
    try {
      const res = await fetch(`${API}/users/${resetTarget.id}/reset-password`, {
        method: "PUT",
        headers,
        body: JSON.stringify({
          password: resetPasswordValue,
        }),
      });

      if (!res.ok) {
        toast.error((await res.json()).detail || "Password reset failed");
        return;
      }

      toast.success("Password reset successfully");
      closeResetPasswordModal();
    } finally {
      setResetSubmitting(false);
    }
  };

  const uploadTemplate = async () => {
    if (!templateForm.brand || !templateForm.templateType || !templateForm.file) {
      return toast.warning("Brand, Template Type and File required");
    }

    const form = new FormData();
    form.append("brand", templateForm.brand);
    form.append("templateType", templateForm.templateType);
    form.append("template_type", templateForm.templateType);
    form.append("file", templateForm.file);

    const res = await fetch(`${API}/templates/upload`, {
      method: "POST",
      headers: authHeaders,
      body: form,
    });

    if (!res.ok) return toast.error((await res.json()).detail || "Template upload failed");

    toast.success("Template uploaded successfully");
    setTemplateForm({ brand: "", templateType: "Product Hub", file: null });
    loadTemplates();
  };

  const downloadTemplate = async (item) => {
    const id = item.id || item.templateId || item.template_id;
    if (!id) return toast.error("Template ID not found");
    try {
      const res = await fetch(`${API}/templates/download/${id}`, { headers: authHeaders });
      if (!res.ok) {
        let detail = "Template download failed";
        try { detail = (await res.json()).detail || detail; } catch {}
        return toast.error(detail);
      }
      const blob = await res.blob();
      const disposition = res.headers.get("content-disposition") || "";
      const match = /filename="?([^"]+)"?/i.exec(disposition);
      const fileName = match?.[1] || item.fileName || item.file_name || "template.xlsx";
      const blobUrl = window.URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = blobUrl;
      link.setAttribute("download", fileName);
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.URL.revokeObjectURL(blobUrl);
    } catch {
      toast.error("Template download failed");
    }
  };

  const performDeleteTemplate = async (item) => {
    const id = item.id || item.templateId || item.template_id;
    if (!id) return toast.error("Template ID not found");

    const res = await fetch(`${API}/templates/${id}`, {
      method: "DELETE",
      headers,
    });

    if (!res.ok) return toast.error((await res.json()).detail || "Template delete failed");
    loadTemplates();
  };

  const deleteTemplate = (item) => {
    const id = item.id || item.templateId || item.template_id;
    if (!id) return toast.error("Template ID not found");
    openConfirm({
      title: "Delete Template",
      message: "Delete this template?",
      confirmLabel: "Delete",
      variant: "danger",
      onConfirm: () => performDeleteTemplate(item),
    });
  };

  return (
    <div className="space-y-6 p-0 md:p-0" style={{ color: COLORS.text }}>

      <div className="grid grid-cols-2 xl:grid-cols-7 gap-4 mb-5">
        <Stat title="Total Users" value={stats.totalUsers} icon={Users} />
        <Stat title="Active Users" value={stats.activeUsers} icon={Power} />
        <Stat title="Inactive" value={stats.inactiveUsers} icon={Power} />
        <Stat title="States" value={stats.states} icon={MapPin} />
        <Stat title="Brands" value={stats.brands} icon={Building2} />
        <Stat title="Dealers" value={stats.dealers} icon={Users} />
        <Stat title="Branches" value={stats.branches} icon={MapPin} />
      </div>

      <div className="flex flex-wrap gap-3 mb-5">
        <Tab active={activeTab === "list"} onClick={() => setActiveTab("list")} icon={Users} label="User List" />
        {(isMaster || isAdmin) && (
          <Tab active={activeTab === "add"} onClick={() => setActiveTab("add")} icon={UserPlus} label={editingUser ? "Edit User" : "Add New User"} />
        )}
        {isMaster && (
          <Tab active={activeTab === "settings"} onClick={() => setActiveTab("settings")} icon={Settings} label="Settings" />
        )}
      </div>

      {activeTab === "list" && (
        <Panel>
          <div className="flex justify-between mb-4 gap-3">
            <h2 className="text-xl font-bold">Created User List <span className="text-xs font-medium" style={{ color: COLORS.muted }}>(Top filter applied)</span></h2>
            <div className="flex flex-wrap gap-2">
              <div className="relative">
                <Search className="h-4 w-4 absolute left-3 top-3" />
                <input
                  className="pl-9 pr-4 py-2 rounded-xl border"
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  placeholder="Search user"
                />
              </div>

              <select
                className="px-3 py-2 rounded-xl border font-bold"
                value={statusFilter}
                onChange={(e) => setStatusFilter(e.target.value)}
                style={{
                  backgroundColor:
                    statusFilter === "active"
                      ? "#DCFCE7"
                      : statusFilter === "inactive"
                      ? "#FEE2E2"
                      : "#FFFFFF",
                  color:
                    statusFilter === "active"
                      ? "#166534"
                      : statusFilter === "inactive"
                      ? "#991B1B"
                      : COLORS.text,
                  borderColor:
                    statusFilter === "active"
                      ? "#22C55E"
                      : statusFilter === "inactive"
                      ? "#EF4444"
                      : COLORS.border,
                }}
              >
                <option value="all">All Users</option>
                <option value="active">🟢 Active Users</option>
                <option value="inactive">🔴 Inactive Users</option>
              </select>
            </div>
          </div>

          <div className="nmts-users-table-wrap overflow-x-auto rounded-xl bg-white border">
            <table className="w-full text-sm nmts-users-table">
              <thead>
                <tr style={{ backgroundColor: COLORS.primary, color: "#fff" }}>
                  {["User ID", "Name", "Mobile", "Email", "Role", "State", "Brand", "Dealer", "Branch", "Status", "Action"].map((h) => (
                    <th key={h} className="p-3 text-left whitespace-nowrap">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
            {filteredUsers.map((u, i) => {
              const dealer = u.dealer || u.group || "-";
              const branch = u.branch || u.location || "-";
              return (
                <tr key={i} className="border-b">
                  <td className="p-3 whitespace-nowrap">{u.userId || u.user_id || "-"}</td>
                  <td className="p-3 min-w-[120px]">{u.name || u.username || "-"}</td>
                  <td className="p-3 whitespace-nowrap">{u.mobile || u.phone || "-"}</td>
                  <td className="p-3 min-w-[160px]">{u.email || "-"}</td>
                  <td className="p-3 capitalize whitespace-nowrap">{u.role || "-"}</td>
                  <td className="p-3 whitespace-nowrap">{u.state || "-"}</td>
                  <td className="p-3">{u.brand || "-"}</td>
                  <td className="p-3 min-w-[100px]">{dealer}</td>
                  <td className="p-3 min-w-[100px]">{branch}</td>
                  <td className="p-3 status-cell">
                    <span
                      className="inline-flex px-2.5 py-1 rounded-full text-xs font-bold whitespace-nowrap"
                      style={{
                        backgroundColor: (u.status || "active") === "active" ? "#DCFCE7" : "#FEE2E2",
                        color: (u.status || "active") === "active" ? "#166534" : "#991B1B",
                        border: `1px solid ${(u.status || "active") === "active" ? "#22C55E" : "#EF4444"}`,
                      }}
                    >
                      {(u.status || "active") === "active" ? "Active" : "Inactive"}
                    </span>
                  </td>
                  <td className="p-3 whitespace-nowrap">
                    <div className="flex gap-3 items-center">
                    <Eye size={16} title="View" />

                    {(isMaster || isAdmin) && (
                      <>
                        <Edit
                          size={16}
                          title="Edit User"
                          style={{ cursor: "pointer", color: "#0F766E" }}
                          onClick={() => openEditUser(u)}
                        />

                        <KeyRound
                          size={16}
                          title="Reset Password"
                          style={{ cursor: "pointer", color: "#2563EB" }}
                          onClick={() => openResetPasswordModal(u)}
                        />

                        <Power
                          size={17}
                          title={(u.status || "active") === "active" ? "Deactivate User" : "Activate User"}
                          style={{
                            cursor: "pointer",
                            color: (u.status || "active") === "active" ? "#16A34A" : "#DC2626",
                          }}
                          onClick={() => toggleUserStatus(u)}
                        />
                      </>
                    )}

                    {isMaster && (
                      <Trash2
                        size={16}
                        title="Delete"
                        style={{ cursor: "pointer", color: COLORS.danger }}
                        onClick={() => deleteUser(u)}
                      />
                    )}
                    </div>
                  </td>
                </tr>
              );
            })}
              </tbody>
            </table>
          </div>
        </Panel>
      )}

      {activeTab === "add" && (isMaster || isAdmin) && (
        <Panel>
          <h2 className="text-xl font-bold mb-4">{editingUser ? "Edit User" : "Add New User"}</h2>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <Input label="User ID Auto" value={newUser.userId} disabled />
            <Input label="User Name" value={newUser.name} onChange={(v) => setNewUser({ ...newUser, name: v })} />
            <Input label="Mobile" value={newUser.mobile} onChange={(v) => setNewUser({ ...newUser, mobile: v })} />
            <Input label="Email" value={newUser.email} onChange={(v) => setNewUser({ ...newUser, email: v })} />

            <Select
              label="Role"
              value={newUser.role}
              onChange={(v) => setNewUser({ ...newUser, role: v })}
              options={isMaster ? ["master", "admin", "user"] : ["user"]}
              disabled={isAdmin}
            />

            <Select
              label="State"
              value={newUser.state}
              onChange={handleStateChange}
              options={isMaster ? states.map((st) => st.name) : [user?.state].filter(Boolean)}
              disabled={isAdmin}
            />

            <Select
              label="Brand"
              value={newUser.brand}
              onChange={handleBrandChange}
              options={isMaster ? brands.map((b) => b.name) : [user?.brand || ""]}
              disabled={isAdmin}
            />

            <Select
              label="Dealer"
              value={newUser.dealer}
              onChange={(v) => setNewUser({ ...newUser, dealer: v, branch: "" })}
              options={isMaster ? availableDealersForUser : [user?.dealer || user?.group || ""]}
              disabled={isAdmin}
            />

            <Select
              label="Branch"
              value={newUser.branch}
              onChange={(v) => setNewUser({ ...newUser, branch: v })}
              options={availableBranchesForNewUser}
            />

            {!editingUser && (
              <>
            <Input label="Password" type="password" value={newUser.password} onChange={(v) => setNewUser({ ...newUser, password: v })} />
            <Input label="Confirm Password" type="password" value={newUser.confirmPassword} onChange={(v) => setNewUser({ ...newUser, confirmPassword: v })} />
              </>
            )}
          </div>

          <h3 className="font-bold mt-5 mb-3 flex gap-2"><ShieldCheck /> Permissions</h3>
          <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
            {allowedPermissionList.map((m) => (
              <label key={m} className="p-3 rounded-xl" style={{ backgroundColor: COLORS.header }}>
                <input type="checkbox" checked={newUser.permissions.includes(m)} onChange={() => togglePermission(m)} /> {m}
              </label>
            ))}
          </div>

          <Button className="mt-5" onClick={saveUser} style={{ backgroundColor: COLORS.button }}>
            <Save className="h-4 w-4 mr-2" /> {editingUser ? "Update User" : "Save User"}
          </Button>
        </Panel>
      )}

      {activeTab === "settings" && isMaster && (
        <Panel>
          <div className="flex flex-wrap gap-3 mb-5">
            <MiniTab active={settingsTab === "state"} onClick={() => setSettingsTab("state")} label="State Master" />
            <MiniTab active={settingsTab === "brand"} onClick={() => setSettingsTab("brand")} label="Brand Master" />
            <MiniTab active={settingsTab === "dealer"} onClick={() => setSettingsTab("dealer")} label="Dealer Master" />
            <MiniTab active={settingsTab === "branch"} onClick={() => setSettingsTab("branch")} label="Branch Master" />
            <MiniTab active={settingsTab === "template"} onClick={() => setSettingsTab("template")} label="Template Master" />
            <MiniTab active={settingsTab === "permission"} onClick={() => setSettingsTab("permission")} label="Permission Master" />
          </div>

          {settingsTab === "state" && (
            <MasterSection
              title="State Master"
              fields={
                <>
                  <Input label="State Code" value={stateForm.code} onChange={(v) => setStateForm({ ...stateForm, code: v.toUpperCase().slice(0, 2) })} />
                  <Input label="State Name" value={stateForm.name} onChange={(v) => setStateForm({ ...stateForm, name: v })} />
                </>
              }
              onSave={addOrUpdateState}
              editing={editingState}
              rows={states}
              columns={["code", "name"]}
              onEdit={(st) => { setEditingState(st); setStateForm({ code: st.code, name: st.name }); }}
              onDelete={(st) => deleteState(st.code)}
            />
          )}

          {settingsTab === "brand" && (
            <MasterSection
              title="Brand Master"
              fields={
                <>
                  <Input label="Brand Code" value={brandForm.code} onChange={(v) => setBrandForm({ ...brandForm, code: v.toUpperCase().slice(0, 2) })} />
                  <Input label="Brand Name" value={brandForm.name} onChange={(v) => setBrandForm({ ...brandForm, name: v })} />
                </>
              }
              onSave={addOrUpdateBrand}
              editing={editingBrand}
              rows={brands}
              columns={["code", "name"]}
              onEdit={(b) => { setEditingBrand(b); setBrandForm({ code: b.code, name: b.name }); }}
              onDelete={(b) => deleteBrand(b.code)}
            />
          )}

          {settingsTab === "dealer" && (
            <MasterSection
              title="Dealer Master"
              fields={
                <>
                  <Select
                    label="Brand"
                    value={dealerForm.brand}
                    onChange={(v) => setDealerForm({ ...dealerForm, brand: v })}
                    options={brands.map((b) => b.name)}
                  />
                  <Input label="Dealer Name" value={dealerForm.name} onChange={(v) => setDealerForm({ ...dealerForm, name: v })} />
                </>
              }
              onSave={addOrUpdateDealer}
              editing={editingDealer}
              rows={dealers}
              columns={["brand", "name"]}
              onEdit={(d) => { setEditingDealer(d); setDealerForm({ name: d.name, brand: d.brand || d.brand_name || "" }); }}
              onDelete={(d) => deleteDealer(d)}
            />
          )}

          {settingsTab === "branch" && (
            <MasterSection
              title="Branch Master"
              fields={
                <>
                  <Select
                    label="Brand"
                    value={branchForm.brand}
                    onChange={(v) => setBranchForm({ ...branchForm, brand: v, dealer: "" })}
                    options={brands.map((b) => b.name)}
                  />
                  <Select
                    label="Dealer"
                    value={branchForm.dealer}
                    onChange={(v) => setBranchForm({ ...branchForm, dealer: v })}
                    options={availableDealersForBranchForm}
                  />
                  <Input label="Branch Name" value={branchForm.name} onChange={(v) => setBranchForm({ ...branchForm, name: v })} />
                </>
              }
              onSave={addOrUpdateBranch}
              editing={editingBranch}
              rows={branches}
              columns={["brand", "dealer", "name"]}
              onEdit={(b) => { setEditingBranch(b); setBranchForm({ brand: b.brand || b.brand_name || "", dealer: b.dealer || b.dealer_name || "", name: b.name }); }}
              onDelete={(b) => deleteBranch(b)}
            />
          )}

          {settingsTab === "template" && (
            <TemplateSection
              brands={brands}
              templates={templates}
              templateForm={templateForm}
              setTemplateForm={setTemplateForm}
              uploadTemplate={uploadTemplate}
              downloadTemplate={downloadTemplate}
              deleteTemplate={deleteTemplate}
            />
          )}

          {settingsTab === "permission" && (
            <div>
              <h2 className="text-xl font-bold mb-4">Permission Master</h2>
              <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
                {MENU_LIST.map((m) => <div key={m} className="p-3 rounded-xl bg-white">{m}</div>)}
              </div>
            </div>
          )}
        </Panel>
      )}

      <NmtsConfirmDialog
        open={!!confirmDlg}
        title={confirmDlg?.title || ""}
        message={confirmDlg?.message}
        confirmLabel={confirmDlg?.confirmLabel || "Confirm"}
        cancelLabel="Cancel"
        variant={confirmDlg?.variant || "default"}
        loading={confirmBusy}
        onCancel={() => {
          if (!confirmBusy) setConfirmDlg(null);
        }}
        onConfirm={handleConfirmDialog}
      />

      <NmtsModal
        open={!!resetTarget}
        onClose={closeResetPasswordModal}
        title="Reset Password"
        maxWidth="max-w-md"
      >
        <p className="text-sm mb-4" style={{ color: COLORS.muted }}>
          Reset password for {resetTarget?.name || resetTarget?.userId || resetTarget?.user_id || "user"}.
        </p>
        <div className="space-y-4">
          <div>
            <label className="font-bold block mb-1">New Password</label>
            <div className="relative">
              <input
                type={showResetPassword ? "text" : "password"}
                value={resetPasswordValue}
                disabled={resetSubmitting}
                onChange={(e) => setResetPasswordValue(e.target.value)}
                className="w-full px-4 py-3 pr-10 rounded-xl border bg-white"
                style={{ color: COLORS.text }}
              />
              <button
                type="button"
                className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-500"
                onClick={() => setShowResetPassword((v) => !v)}
                tabIndex={-1}
              >
                {showResetPassword ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
              </button>
            </div>
          </div>
          <div>
            <label className="font-bold block mb-1">Confirm Password</label>
            <div className="relative">
              <input
                type={showResetPasswordConfirm ? "text" : "password"}
                value={resetPasswordConfirm}
                disabled={resetSubmitting}
                onChange={(e) => setResetPasswordConfirm(e.target.value)}
                className="w-full px-4 py-3 pr-10 rounded-xl border bg-white"
                style={{ color: COLORS.text }}
              />
              <button
                type="button"
                className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-500"
                onClick={() => setShowResetPasswordConfirm((v) => !v)}
                tabIndex={-1}
              >
                {showResetPasswordConfirm ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
              </button>
            </div>
          </div>
          <div className="flex justify-end gap-2 pt-2">
            <Button type="button" variant="outline" disabled={resetSubmitting} onClick={closeResetPasswordModal}>
              Cancel
            </Button>
            <Button
              type="button"
              disabled={resetSubmitting || !resetPasswordValue}
              onClick={submitResetPassword}
              style={{ backgroundColor: COLORS.button, color: "#fff" }}
            >
              {resetSubmitting ? "Resetting…" : "Reset Password"}
            </Button>
          </div>
        </div>
      </NmtsModal>
    </div>
  );
}

function Header() {
  return (
    <div className="rounded-2xl p-5 mb-5 shadow-sm" style={{ backgroundColor: COLORS.header, border: `1px solid ${COLORS.border}` }}>
      <h1 className="text-3xl font-bold">User Hub</h1>
      <p className="text-sm mt-1" style={{ color: COLORS.muted }}>Manage users, permissions, brand master, dealer master, branch master and templates.</p>
    </div>
  );
}

function Stat({ title, value, icon: Icon }) {
  return (
    <div className="rounded-2xl p-4 shadow-sm" style={{ backgroundColor: COLORS.card, border: `1px solid ${COLORS.border}` }}>
      <Icon className="h-5 w-5 mb-3" />
      <p className="text-sm">{title}</p>
      <h2 className="text-3xl font-bold">{value}</h2>
    </div>
  );
}

function Panel({ children }) {
  return <div className="rounded-2xl p-5 shadow-sm" style={{ backgroundColor: COLORS.card, border: `1px solid ${COLORS.border}` }}>{children}</div>;
}

function Tab({ active, onClick, icon: Icon, label }) {
  return <Button onClick={onClick} style={{ backgroundColor: active ? COLORS.active : COLORS.primary }}><Icon className="h-4 w-4 mr-2" /> {label}</Button>;
}

function MiniTab({ active, onClick, label }) {
  return <button onClick={onClick} className="px-5 py-3 rounded-xl font-bold" style={{ backgroundColor: active ? COLORS.active : COLORS.primary, color: "#fff" }}>{label}</button>;
}

function Input({ label, value, onChange, type = "text", disabled = false }) {
  return (
    <div>
      <label className="font-bold block mb-1">{label}</label>
      <input type={type} value={value} disabled={disabled} onChange={(e) => onChange && onChange(e.target.value)} className="w-full px-4 py-3 rounded-xl border bg-white" style={{ color: COLORS.text }} />
    </div>
  );
}

function Select({ label, value, onChange, options, disabled = false }) {
  return (
    <div>
      <label className="font-bold block mb-1">{label}</label>
      <select disabled={disabled} value={value} onChange={(e) => onChange(e.target.value)} className="w-full px-4 py-3 rounded-xl border bg-white" style={{ color: COLORS.text }}>
        <option value="">Select</option>
        {(options || []).filter(Boolean).map((x) => <option key={x} value={x}>{x}</option>)}
      </select>
    </div>
  );
}

function Table({ headers, children }) {
  return (
    <div className="overflow-x-auto rounded-xl bg-white">
      <table className="w-full text-sm">
        <thead>
          <tr style={{ backgroundColor: COLORS.primary, color: "#fff" }}>
            {headers.map((h) => <th key={h} className="p-3 text-left">{h}</th>)}
          </tr>
        </thead>
        <tbody>{children}</tbody>
      </table>
    </div>
  );
}

function MasterSection({ title, fields, onSave, editing, rows, columns, onEdit, onDelete }) {
  return (
    <div>
      <h2 className="text-xl font-bold mb-4">{title}</h2>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-4">{fields}</div>
      <Button onClick={onSave} style={{ backgroundColor: COLORS.button }}>
        <Plus className="h-4 w-4 mr-2" /> {editing ? "Update" : "Add"}
      </Button>

      <div className="mt-5 overflow-x-auto rounded-xl bg-white">
        <table className="w-full text-sm">
          <thead>
            <tr style={{ backgroundColor: COLORS.primary, color: "#fff" }}>
              {columns.map((c) => <th key={c} className="p-3 text-left">{c.toUpperCase()}</th>)}
              <th className="p-3 text-left">ACTION</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.id || r.code || `${r.brand || ""}-${r.dealer || ""}-${r.name}`} className="border-b">
                {columns.map((c) => <td key={c} className="p-3">{r[c] || "-"}</td>)}
                <td className="p-3 flex gap-3">
                  <Edit size={17} onClick={() => onEdit(r)} style={{ cursor: "pointer" }} />
                  <Trash2 size={17} onClick={() => onDelete(r)} style={{ cursor: "pointer", color: COLORS.danger }} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function TemplateSection({ brands, templates, templateForm, setTemplateForm, uploadTemplate, downloadTemplate, deleteTemplate }) {
  return (
    <div>
      <h2 className="text-xl font-bold mb-4">Template Master</h2>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-4">
        <Select
          label="Brand"
          value={templateForm.brand}
          onChange={(v) => setTemplateForm({ ...templateForm, brand: v })}
          options={brands.map((b) => b.name)}
        />

        <Select
          label="Template Type"
          value={templateForm.templateType}
          onChange={(v) => setTemplateForm({ ...templateForm, templateType: v })}
          options={["Product Hub", "Order Desk"]}
        />

        <div>
          <label className="font-bold block mb-1">Choose File</label>
          <input
            type="file"
            className="w-full px-4 py-3 rounded-xl border bg-white"
            onChange={(e) => setTemplateForm({ ...templateForm, file: e.target.files?.[0] || null })}
          />
        </div>
      </div>

      <Button onClick={uploadTemplate} style={{ backgroundColor: COLORS.button }}>
        <Upload className="h-4 w-4 mr-2" /> Upload Template
      </Button>

      <div className="mt-5 overflow-x-auto rounded-xl bg-white">
        <table className="w-full text-sm">
          <thead>
            <tr style={{ backgroundColor: COLORS.primary, color: "#fff" }}>
              <th className="p-3 text-left">BRAND</th>
              <th className="p-3 text-left">TYPE</th>
              <th className="p-3 text-left">FILE NAME</th>
              <th className="p-3 text-left">UPLOADED BY</th>
              <th className="p-3 text-left">DATE</th>
              <th className="p-3 text-left">ACTION</th>
            </tr>
          </thead>
          <tbody>
            {(templates || []).map((t, i) => (
              <tr key={i} className="border-b">
                <td className="p-3">{t.brand || "-"}</td>
                <td className="p-3">{t.templateType || t.template_type || "-"}</td>
                <td className="p-3">{t.fileName || t.file_name || "-"}</td>
                <td className="p-3">{t.uploadedBy || t.uploaded_by || "-"}</td>
                <td className="p-3">{t.uploadedAt || t.uploaded_at || t.createdAt || "-"}</td>
                <td className="p-3 flex gap-3">
                  <Download size={17} onClick={() => downloadTemplate(t)} style={{ cursor: "pointer" }} />
                  <Trash2 size={17} onClick={() => deleteTemplate(t)} style={{ cursor: "pointer", color: COLORS.danger }} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
