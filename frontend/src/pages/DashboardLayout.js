import React, { useState, useEffect } from 'react';
import { Outlet, useLocation, useNavigate } from 'react-router-dom';
import { useAuth, API, canAccessPermission } from '../App';
import axios from 'axios';
import {
  Bell,
  Package,
  Users,
  LogOut,
  X,
  User,
  Home,
  MapPin,
  Building,
  Shield,
  Upload,
  ClipboardList,
  ClipboardCheck,
  BarChart3,
  Globe,
  Megaphone,
  HelpCircle,
  FileSpreadsheet,
  Smartphone
} from 'lucide-react';
import { APPLICATION_MENU_ITEMS } from '../config/menuConfig';
import { Button } from '../components/ui/button';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '../components/ui/dropdown-menu';
import { toast } from 'sonner';
import { NoticeLoginPopup } from '../components/NoticeLoginPopup';

export function DashboardLayout() {
  const { user, logout } = useAuth();
  const location = useLocation();
  const navigate = useNavigate();

  const [unreadCount, setUnreadCount] = useState(0);
  const [menuOpen, setMenuOpen] = useState(false);
  const [tabs, setTabs] = useState([]);
  const [activeTabId, setActiveTabId] = useState(null);

  const [brandOptions, setBrandOptions] = useState(["All Brands"]);
  const [dealerOptions, setDealerOptions] = useState(["All Dealers"]);
  const [branchOptions, setBranchOptions] = useState(["All Branches"]);

  const isMaster = user?.role === "master";
  const isAdmin = user?.role === "admin";

  const getUserBrand = () => user?.brand || "N/A";
  const getUserDealer = () => user?.dealer || user?.group || "N/A";
  const getUserBranch = () => user?.branch || user?.location || "N/A";
  const getDisplayUserName = () => user?.name || user?.username || user?.email || "User";
  const getDisplayRole = () =>
    user?.role === "master" ? "Master Admin" : user?.role === "admin" ? "Admin" : "User";

  const [scopeBrand, setScopeBrand] = useState("N/A");
  const [scopeDealer, setScopeDealer] = useState("N/A");
  const [scopeBranch, setScopeBranch] = useState("N/A");
  const [scopeMasters, setScopeMasters] = useState({ brands: [], dealers: [], branches: [] });

  const isAllScope = (value) => !value || String(value).startsWith("All ") || value === "N/A";
  const normalizeScopeValue = (value) => String(value || "").trim().toLowerCase();
  const uniqueNames = (items, selector) => Array.from(new Set((items || []).map(selector).filter(Boolean))).sort((a, b) => String(a).localeCompare(String(b)));

  const navItems = APPLICATION_MENU_ITEMS;

  const filteredNavItems = navItems.filter((item) => item.allRoles || canAccessPermission(user, item.label));

  useEffect(() => {
    if (!user) return;

    if (isMaster) {
      setScopeBrand("All Brands");
      setScopeDealer("All Dealers");
      setScopeBranch("All Branches");
    } else {
      setScopeBrand(getUserBrand());
      setScopeDealer(getUserDealer());
      // Admin can switch between every branch under the assigned dealer.
      // Normal users stay locked to their assigned branch.
      setScopeBranch(isAdmin ? "All Branches" : getUserBranch());
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [user?.role, user?.brand, user?.dealer, user?.group, user?.branch, user?.location]);

  useEffect(() => {
    const loadScopeOptions = async () => {
      if (!user) return;

      try {
        const res = await axios.get(`${API}/scope/options`);
        const data = res.data || {};

        const dbBrands = Array.isArray(data.brands) ? data.brands : [];
        const dbDealers = Array.isArray(data.dealers) ? data.dealers : [];
        const dbBranches = Array.isArray(data.branches) ? data.branches : [];
        setScopeMasters({ brands: dbBrands, dealers: dbDealers, branches: dbBranches });

        if (isMaster) {
          setBrandOptions(["All Brands", ...uniqueNames(dbBrands, (b) => b.name)]);
          setDealerOptions(["All Dealers", ...uniqueNames(dbDealers, (d) => d.name)]);
          setBranchOptions(["All Branches", ...uniqueNames(dbBranches, (b) => b.name)]);
        } else if (isAdmin) {
          const adminBranches = uniqueNames(dbBranches, (b) => b.name);
          setBrandOptions([getUserBrand()]);
          setDealerOptions([getUserDealer()]);
          setBranchOptions(["All Branches", ...(adminBranches.length ? adminBranches : [getUserBranch()])]);
        } else {
          setBrandOptions([getUserBrand()]);
          setDealerOptions([getUserDealer()]);
          setBranchOptions([getUserBranch()]);
        }
      } catch (error) {
        console.error("Scope options load failed", error);
        setScopeMasters({ brands: [], dealers: [], branches: [] });

        if (isMaster) {
          setBrandOptions(["All Brands"]);
          setDealerOptions(["All Dealers"]);
          setBranchOptions(["All Branches"]);
        } else {
          setBrandOptions([getUserBrand()]);
          setDealerOptions([getUserDealer()]);
          setBranchOptions([getUserBranch()]);
        }
      }
    };

    loadScopeOptions();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [user?.role, user?.brand, user?.dealer, user?.group, user?.branch, user?.location]);

  useEffect(() => {
    if (!isMaster) return;

    const dealersForBrand = (scopeMasters.dealers || []).filter((dealer) => {
      const dealerBrand = dealer.brand || dealer.brand_name;
      return isAllScope(scopeBrand) || !dealerBrand || dealerBrand === scopeBrand;
    });
    const nextDealerOptions = ["All Dealers", ...uniqueNames(dealersForBrand, (d) => d.name)];
    setDealerOptions(nextDealerOptions);
    if (!nextDealerOptions.includes(scopeDealer)) {
      setScopeDealer("All Dealers");
      setScopeBranch("All Branches");
      return;
    }

    const branchesForScope = (scopeMasters.branches || []).filter((branch) => {
      const branchBrand = branch.brand || branch.brand_name;
      const branchDealer = branch.dealer || branch.dealer_name;
      const brandOk = isAllScope(scopeBrand) || !branchBrand || branchBrand === scopeBrand;
      const dealerOk = isAllScope(scopeDealer) || branchDealer === scopeDealer;
      return brandOk && dealerOk;
    });
    const nextBranchOptions = ["All Branches", ...uniqueNames(branchesForScope, (b) => b.name)];
    setBranchOptions(nextBranchOptions);
    if (!nextBranchOptions.includes(scopeBranch)) setScopeBranch("All Branches");
  }, [isMaster, scopeMasters, scopeBrand, scopeDealer, scopeBranch]);

  useEffect(() => {
    if (!isAdmin) return;
    const adminDealer = normalizeScopeValue(getUserDealer());
    const adminBrand = normalizeScopeValue(getUserBrand());
    const adminBranches = (scopeMasters.branches || []).filter((branch) => {
      const branchDealer = normalizeScopeValue(branch.dealer || branch.dealer_name);
      const branchBrand = normalizeScopeValue(branch.brand || branch.brand_name);
      const dealerMatches = !branchDealer || branchDealer === adminDealer;
      const brandMatches = !branchBrand || branchBrand === adminBrand;
      return dealerMatches && brandMatches;
    });
    const nextBranchOptions = ["All Branches", ...uniqueNames(adminBranches, (b) => b.name)];
    setBranchOptions(nextBranchOptions.length > 1 ? nextBranchOptions : ["All Branches", getUserBranch()]);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isAdmin, scopeMasters, user?.group, user?.dealer, user?.location, user?.branch]);

  useEffect(() => {
    fetchUnreadCount();
    const interval = setInterval(fetchUnreadCount, 30000);

    const savedTabs = localStorage.getItem('openTabs');
    const savedActiveTab = localStorage.getItem('activeTab');

    if (savedTabs) {
      try {
        const parsedTabs = JSON.parse(savedTabs);
        const allowedTabs = Array.isArray(parsedTabs)
          ? parsedTabs.filter((tab) => canAccessPermission(user, tab.label) || tab.label === 'My Profile')
          : [];
        setTabs(allowedTabs);
      } catch {
        setTabs([]);
      }

      if (savedActiveTab) {
        setActiveTabId(savedActiveTab);
      }
    }

    return () => clearInterval(interval);
  }, []);

  useEffect(() => {
    if (tabs.length > 0) {
      localStorage.setItem('openTabs', JSON.stringify(tabs));
      if (activeTabId) {
        localStorage.setItem('activeTab', activeTabId);
      }
    } else {
      localStorage.removeItem('openTabs');
      localStorage.removeItem('activeTab');
    }
  }, [tabs, activeTabId]);

  useEffect(() => {
    const currentTab = tabs.find(tab => tab.path === location.pathname);
    if (currentTab && activeTabId !== currentTab.id) {
      setActiveTabId(currentTab.id);
    }
  }, [location.pathname, tabs, activeTabId]);

  const fetchUnreadCount = async () => {
    try {
      const res = await axios.get(`${API}/notifications/unread-count`);
      setUnreadCount(res.data.count || 0);
    } catch {
      setUnreadCount(0);
    }
  };

  const handleLogout = () => {
    logout();
    localStorage.removeItem('openTabs');
    localStorage.removeItem('activeTab');
    navigate('/login');
    toast.success('Logged out successfully');
  };

  const openTab = (id, label, path) => {
    if (!canAccessPermission(user, label) && label !== 'My Profile') {
      toast.error('You do not have permission to open this screen');
      navigate('/');
      return;
    }

    const existingTab = tabs.find(tab => tab.id === id);

    if (!existingTab) {
      setTabs([...tabs, { id, label, path }]);
    }

    setActiveTabId(id);
    navigate(path);
  };

  const closeTab = (tabId, e) => {
    e.stopPropagation();

    const tabIndex = tabs.findIndex(tab => tab.id === tabId);
    const newTabs = tabs.filter(tab => tab.id !== tabId);

    if (activeTabId === tabId) {
      if (newTabs.length > 0) {
        const newActiveTab = newTabs[tabIndex] || newTabs[tabIndex - 1] || newTabs[0];
        setActiveTabId(newActiveTab.id);
        navigate(newActiveTab.path);
      } else {
        setActiveTabId(null);
        navigate('/');
      }
    }

    setTabs(newTabs);
  };

  const goHome = () => {
    setActiveTabId(null);
    navigate('/');
  };

  return (
    <div className="min-h-screen" style={{ backgroundColor: '#D1FAE5' }}>
      <header
        className="sticky top-0 z-50"
        style={{ backgroundColor: '#A7F3D0', borderBottom: '1px solid #D1D5DB' }}
      >
        <div className="flex items-center justify-between px-3 py-2">
          <div className="flex items-center">
            <img src="/sleeping-stock-logo.png" alt="Sleeping Stock" className="h-12 w-auto mr-2" />

            <div className="hidden md:flex items-center mr-3">
              <span className="text-xl font-bold" style={{ color: '#242424' }}>Sleeping</span>
              <span className="text-xl font-bold" style={{ color: '#489232' }}>Stock</span>
            </div>

            <Button variant="ghost" size="sm" className="p-1 mr-2" onClick={goHome}>
              <Home className="h-6 w-6" style={{ color: '#3eb919' }} />
            </Button>

            <div className="flex items-center space-x-1 overflow-x-auto max-w-[55vw]">
              {tabs.map((tab) => (
                <div
                  key={tab.id}
                  onClick={() => {
                    setActiveTabId(tab.id);
                    navigate(tab.path);
                  }}
                  className="flex items-center px-3 py-1.5 cursor-pointer rounded"
                  style={{
                    backgroundColor: activeTabId === tab.id ? '#059669' : '#059669',
                    color: 'white'
                  }}
                >
                  <span className="text-sm font-medium whitespace-nowrap mr-2">{tab.label}</span>
                  <button onClick={(e) => closeTab(tab.id, e)} className="hover:bg-white/20 rounded p-0.5">
                    <X className="h-3 w-3" />
                  </button>
                </div>
              ))}
            </div>
          </div>

          <div className="flex items-center space-x-3">
            <div className="relative cursor-pointer" onClick={() => openTab('dashboard', 'Notice Board', '/notice-board')}>
              <Bell className="h-5 w-5" style={{ color: '#22C55E' }} />
              {unreadCount > 0 && (
                <span
                  className="absolute -top-1 -right-1 h-4 w-4 text-xs flex items-center justify-center rounded-full text-white"
                  style={{ backgroundColor: '#C55959' }}
                >
                  {unreadCount > 9 ? '9+' : unreadCount}
                </span>
              )}
            </div>

            <Button variant="ghost" onClick={() => setMenuOpen(!menuOpen)} className="flex items-center gap-1 px-2">
              <span className="text-sm font-medium" style={{ color: '#374151' }}>Menu</span>
            </Button>

            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button variant="ghost" className="p-1">
                  <div
                    className="h-10 w-10 rounded-full flex items-center justify-center border-2"
                    style={{ backgroundColor: '#E8F5E9', borderColor: '#059669' }}
                  >
                    <User className="h-6 w-6" style={{ color: '#047857' }} />
                  </div>
                </Button>
              </DropdownMenuTrigger>

              <DropdownMenuContent
                align="end"
                className="w-72 p-0 overflow-hidden"
                style={{
                  backgroundColor: '#34D399',
                  borderRadius: '16px',
                  boxShadow: '0 8px 24px rgba(0, 0, 0, 0.2)',
                  border: '2px solid #059669'
                }}
              >
                <div className="p-4" style={{ borderBottom: '1px solid rgba(255,255,255,0.2)' }}>
                  <h3 className="text-lg font-bold" style={{ color: '#FFFFFF' }}>
                    {user?.username || user?.name || 'User'}
                  </h3>
                  <p className="text-sm capitalize" style={{ color: '#D1FAE5' }}>
                    {user?.role || 'User'}
                  </p>
                </div>

                <div className="p-4 space-y-3" style={{ borderBottom: '1px solid rgba(255,255,255,0.2)' }}>
                  <InfoRow icon={Shield} label="Role" value={user?.role || 'N/A'} />
                  <InfoRow icon={Building} label="Brand" value={scopeBrand} />
                  <InfoRow icon={Users} label="Dealer" value={scopeDealer} />
                  <InfoRow icon={MapPin} label="Branch" value={scopeBranch} />
                </div>

                <div className="p-3 space-y-1">
                  <DropdownMenuItem
                    onClick={() => openTab('profile', 'My Profile', '/profile')}
                    className="flex items-center gap-3 px-3 py-3 rounded-lg cursor-pointer"
                    style={{ color: '#FFFFFF' }}
                  >
                    <User className="h-5 w-5" style={{ color: '#047857' }} />
                    <span className="font-medium">My Profile</span>
                  </DropdownMenuItem>

                  <DropdownMenuItem
                    onClick={handleLogout}
                    className="flex items-center gap-3 px-3 py-3 rounded-lg cursor-pointer"
                    style={{ color: '#C55959' }}
                  >
                    <LogOut className="h-5 w-5" style={{ color: '#C55959' }} />
                    <span className="font-medium">Logout</span>
                  </DropdownMenuItem>
                </div>
              </DropdownMenuContent>
            </DropdownMenu>
          </div>
        </div>
      </header>

      <div
        className="mx-4 mt-3 mb-2 rounded-xl px-4 py-3 shadow-sm"
        style={{ backgroundColor: "#F3F7EF", border: "1px solid #C8D5BF" }}
      >
        <div className="flex flex-col xl:flex-row xl:items-center xl:justify-between gap-3">
          <div className="grid grid-cols-1 md:grid-cols-3 gap-3 flex-1">
            <ScopeSelect
              label="Brand"
              value={scopeBrand}
              onChange={(value) => {
                setScopeBrand(value);
                if (isMaster) {
                  setScopeDealer("All Dealers");
                  setScopeBranch("All Branches");
                }
              }}
              options={isMaster ? brandOptions : [scopeBrand]}
              disabled={!isMaster}
            />

            <ScopeSelect
              label="Dealer"
              value={scopeDealer}
              onChange={(value) => {
                setScopeDealer(value);
                if (isMaster || isAdmin) {
                  setScopeBranch("All Branches");
                }
              }}
              options={isMaster ? dealerOptions : [scopeDealer]}
              disabled={!isMaster}
            />

            <ScopeSelect
              label="Branch"
              value={scopeBranch}
              onChange={setScopeBranch}
              options={isMaster || isAdmin ? branchOptions : [scopeBranch]}
              disabled={!isMaster && !isAdmin}
            />
          </div>

          <div
            className="px-4 py-2 rounded-xl border bg-white text-sm font-bold whitespace-nowrap"
            style={{ borderColor: "#C8D5BF", color: "#263326" }}
            title="Current logged-in user"
          >
            👤 {getDisplayUserName()} <span style={{ color: "#6B7280" }}>|</span> 🛡 {getDisplayRole()}
          </div>
        </div>
      </div>

      <main className="flex-1 p-4">
        <NoticeLoginPopup />
        <Outlet context={{ scopeBrand, scopeDealer, scopeBranch }} />
      </main>

      {menuOpen && (
        <div
          className="fixed inset-0 z-40"
          onClick={() => setMenuOpen(false)}
          style={{ backgroundColor: 'rgba(0,0,0,0.3)' }}
        >
          <div
            className="absolute top-16 right-4 rounded-xl shadow-xl p-3 w-56"
            style={{ backgroundColor: '#FFFFFF', border: '1px solid #D1D5DB' }}
            onClick={(e) => e.stopPropagation()}
          >
            <nav className="space-y-1">
              {filteredNavItems.map((item) => {
                const Icon = item.icon;
                const isActive = location.pathname === item.path;

                return (
                  <button
                    key={item.id}
                    onClick={() => {
                      openTab(item.id, item.label, item.path);
                      setMenuOpen(false);
                    }}
                    className="flex items-center w-full px-3 py-2.5 text-left rounded-lg hover:bg-gray-50"
                    style={{
                      color: '#374151',
                      backgroundColor: isActive ? '#D1FAE5' : 'transparent'
                    }}
                  >
                    <Icon className="h-5 w-5 mr-3" style={{ color: '#22C55E' }} />
                    <span className="text-sm font-medium">{item.label}</span>
                  </button>
                );
              })}

              <div className="border-t pt-2 mt-2" style={{ borderColor: '#E5E7EB' }}>
                <button
                  onClick={() => {
                    openTab('profile', 'My Profile', '/profile');
                    setMenuOpen(false);
                  }}
                  className="flex items-center w-full px-3 py-2.5 text-left rounded-lg hover:bg-gray-50"
                  style={{ color: '#374151' }}
                >
                  <User className="h-5 w-5 mr-3" style={{ color: '#22C55E' }} />
                  <span className="text-sm font-medium">My Profile</span>
                </button>

                <button
                  onClick={handleLogout}
                  className="flex items-center w-full px-3 py-2.5 text-left rounded-lg hover:bg-red-50"
                  style={{ color: '#C55959' }}
                >
                  <LogOut className="h-5 w-5 mr-3" style={{ color: '#C55959' }} />
                  <span className="text-sm font-medium">Logout</span>
                </button>
              </div>
            </nav>
          </div>
        </div>
      )}
    </div>
  );
}

function ScopeSelect({ label, value, onChange, options, disabled }) {
  return (
    <div className="flex items-center gap-3">
      <span className="text-sm font-bold min-w-[70px]" style={{ color: "#263326" }}>
        {label}:
      </span>

      <select
        value={value}
        disabled={disabled}
        onChange={(e) => onChange(e.target.value)}
        className="w-full px-4 py-2 rounded-lg border text-sm font-medium"
        style={{
          backgroundColor: disabled ? "#EEF2EA" : "#FFFFFF",
          borderColor: "#C8D5BF",
          color: "#263326",
        }}
      >
        {options.map((item) => (
          <option key={item} value={item}>{item}</option>
        ))}
      </select>
    </div>
  );
}

function InfoRow({ icon: Icon, label, value }) {
  return (
    <div className="flex items-center gap-3">
      <Icon className="h-4 w-4" style={{ color: '#D1FAE5' }} />
      <span style={{ color: '#D1FAE5' }}>{label}:</span>
      <span className="font-medium capitalize" style={{ color: '#FFFFFF' }}>
        {value}
      </span>
    </div>
  );
}

export default DashboardLayout;
