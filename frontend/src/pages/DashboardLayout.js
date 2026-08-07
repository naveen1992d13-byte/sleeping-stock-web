import React, { useState, useEffect } from 'react';
import { Outlet, useLocation, useNavigate } from 'react-router-dom';
import { useAuth, API, canAccessPermission } from '../App';
import axios from 'axios';
import {
  Bell,
  Users,
  LogOut,
  X,
  User,
  Home,
  MapPin,
  Building,
  Shield,
  Menu,
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
import { clearAuthStorage } from '../utils/sessionActivity';

export function DashboardLayout() {
  const { user, logout } = useAuth();
  const location = useLocation();
  const navigate = useNavigate();

  const [unreadCount, setUnreadCount] = useState(0);
  const [sidebarOpen, setSidebarOpen] = useState(() => {
    try {
      const stored = localStorage.getItem('nmtsSidebarOpen');
      if (stored === 'false') return false;
      if (stored === 'true') return true;
    } catch {
      /* ignore */
    }
    return typeof window !== 'undefined' ? window.innerWidth >= 1024 : true;
  });
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

  const filteredNavItems = navItems.filter(
    (item) => item.allRoles || canAccessPermission(user, item.permissionLabel || item.label)
  );

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
    try {
      localStorage.setItem('nmtsSidebarOpen', sidebarOpen ? 'true' : 'false');
    } catch {
      /* ignore */
    }
  }, [sidebarOpen]);

  const toggleSidebar = () => {
    setSidebarOpen((prev) => !prev);
  };

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
    clearAuthStorage();
    navigate('/login');
    toast.success('Logged out successfully');
  };

  const openTab = (id, label, path, permissionLabel) => {
    const perm = permissionLabel || label;
    if (!canAccessPermission(user, perm) && label !== 'My Profile') {
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
    <div className="nmts-app-shell" data-testid="dashboard-layout">
      {!sidebarOpen ? null : (
        <button
          type="button"
          className="nmts-sidebar-backdrop lg:hidden"
          aria-label="Close navigation menu"
          onClick={() => setSidebarOpen(false)}
        />
      )}

      <aside
        className={`nmts-sidebar${sidebarOpen ? '' : ' nmts-sidebar--collapsed'}`}
        aria-hidden={!sidebarOpen}
      >
        <div className="nmts-sidebar-brand">
          <img src="/sleeping-stock-logo.png" alt="Sleeping Stock" />
          <div className="nmts-sidebar-brand-title">
            Sleeping <span>Stock</span>
          </div>
          <div className="nmts-sidebar-brand-sub">Non Moving Tracking System</div>
        </div>

        <nav className="nmts-sidebar-nav" aria-label="Main navigation">
          {filteredNavItems.map((item) => {
            const Icon = item.icon;
            const isActive =
              location.pathname === item.path ||
              (item.path === '/notice-board' && location.pathname === '/');

            return (
              <button
                key={item.id}
                type="button"
                className={`nmts-sidebar-link${isActive ? ' nmts-sidebar-link--active' : ''}`}
                onClick={() => {
                  openTab(item.id, item.label, item.path, item.permissionLabel);
                  setSidebarOpen(false);
                }}
              >
                <Icon aria-hidden="true" />
                <span>{item.label}</span>
              </button>
            );
          })}
        </nav>

        <div className="nmts-sidebar-footer">© 2026 NMTS. All rights reserved.</div>
      </aside>

      <div className="nmts-main-column">
        <header className="nmts-topbar">
          <div className="nmts-topbar-inner">
            <div className="nmts-topbar-left">
              <button
                type="button"
                className="nmts-menu-toggle"
                data-testid="sidebar-menu-toggle"
                aria-expanded={sidebarOpen}
                aria-label={sidebarOpen ? 'Collapse sidebar' : 'Expand sidebar'}
                onClick={toggleSidebar}
              >
                <Menu className="h-5 w-5" />
              </button>

              <Button variant="ghost" size="sm" className="p-1 hidden sm:flex" onClick={goHome} title="Home">
                <Home className="h-5 w-5" style={{ color: '#059669' }} />
              </Button>

              <div className="nmts-tab-strip">
                {tabs.map((tab) => (
                  <div
                    key={tab.id}
                    className={`nmts-tab-chip${activeTabId === tab.id ? ' nmts-tab-chip--active' : ''}`}
                    onClick={() => {
                      setActiveTabId(tab.id);
                      navigate(tab.path);
                    }}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter' || e.key === ' ') {
                        setActiveTabId(tab.id);
                        navigate(tab.path);
                      }
                    }}
                    role="button"
                    tabIndex={0}
                    title={tab.label}
                  >
                    <span className="nmts-tab-chip-label">{tab.label}</span>
                    <button
                      type="button"
                      className="nmts-tab-chip-close"
                      onClick={(e) => closeTab(tab.id, e)}
                      aria-label={`Close ${tab.label}`}
                    >
                      <X className="h-3 w-3" />
                    </button>
                  </div>
                ))}
              </div>
            </div>

            <div className="nmts-topbar-right">
              <div className="nmts-header-scope">
                <HeaderScopeSelect
                  label="Brand"
                  value={scopeBrand}
                  onChange={(value) => {
                    setScopeBrand(value);
                    if (isMaster) {
                      setScopeDealer('All Dealers');
                      setScopeBranch('All Branches');
                    }
                  }}
                  options={isMaster ? brandOptions : [scopeBrand]}
                  disabled={!isMaster}
                />
                <HeaderScopeSelect
                  label="Dealer"
                  value={scopeDealer}
                  onChange={(value) => {
                    setScopeDealer(value);
                    if (isMaster || isAdmin) {
                      setScopeBranch('All Branches');
                    }
                  }}
                  options={isMaster ? dealerOptions : [scopeDealer]}
                  disabled={!isMaster}
                />
                <HeaderScopeSelect
                  label="Branch"
                  value={scopeBranch}
                  onChange={setScopeBranch}
                  options={isMaster || isAdmin ? branchOptions : [scopeBranch]}
                  disabled={!isMaster && !isAdmin}
                />
              </div>

              <button
                type="button"
                className="nmts-bell-btn"
                onClick={() => openTab('dashboard', 'Notice Board', '/notice-board')}
                aria-label="Notifications"
              >
                <Bell className="h-5 w-5" />
                {unreadCount > 0 && (
                  <span className="nmts-bell-badge">{unreadCount > 9 ? '9+' : unreadCount}</span>
                )}
              </button>

              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <button type="button" className="nmts-user-trigger">
                    <div className="nmts-user-avatar">
                      <User className="h-5 w-5" />
                    </div>
                    <div className="nmts-user-meta">
                      <div className="nmts-user-meta-name">{getDisplayUserName()}</div>
                      <div className="nmts-user-meta-role">{getDisplayRole()}</div>
                    </div>
                  </button>
                </DropdownMenuTrigger>

                <DropdownMenuContent align="end" className="nmts-user-dropdown w-72 p-0">
                  <div className="nmts-user-dropdown-head">
                    <h3>{user?.username || user?.name || 'User'}</h3>
                    <p>{user?.role || 'User'}</p>
                  </div>

                  <div className="nmts-user-dropdown-body">
                    <InfoRow icon={Shield} label="Role" value={user?.role || 'N/A'} />
                    <InfoRow icon={Building} label="Brand" value={scopeBrand} />
                    <InfoRow icon={Users} label="Dealer" value={scopeDealer} />
                    <InfoRow icon={MapPin} label="Branch" value={scopeBranch} />
                  </div>

                  <div className="p-2">
                    <DropdownMenuItem
                      onClick={() => openTab('profile', 'My Profile', '/profile')}
                      className="flex items-center gap-3 px-3 py-2.5 rounded-lg cursor-pointer"
                    >
                      <User className="h-5 w-5 text-emerald-700" />
                      <span className="font-medium">My Profile</span>
                    </DropdownMenuItem>

                    <DropdownMenuItem
                      onClick={handleLogout}
                      className="flex items-center gap-3 px-3 py-2.5 rounded-lg cursor-pointer text-red-600 focus:text-red-600"
                    >
                      <LogOut className="h-5 w-5" />
                      <span className="font-medium">Logout</span>
                    </DropdownMenuItem>
                  </div>
                </DropdownMenuContent>
              </DropdownMenu>
            </div>
          </div>
        </header>

        <main className="nmts-main-content">
          <NoticeLoginPopup />
          <Outlet context={{ scopeBrand, scopeDealer, scopeBranch }} />
        </main>
      </div>
    </div>
  );
}

function HeaderScopeSelect({ label, value, onChange, options, disabled }) {
  return (
    <label className="nmts-header-scope-field">
      <span className="nmts-header-scope-label">{label}</span>
      <select
        value={value}
        disabled={disabled}
        onChange={(e) => onChange(e.target.value)}
        className="nmts-header-scope-select"
        title={label}
      >
        {options.map((item) => (
          <option key={item} value={item}>
            {item}
          </option>
        ))}
      </select>
    </label>
  );
}

function InfoRow({ icon: Icon, label, value }) {
  return (
    <div className="nmts-info-row">
      <Icon aria-hidden="true" />
      <span>{label}:</span>
      <span>{value}</span>
    </div>
  );
}

export default DashboardLayout;
