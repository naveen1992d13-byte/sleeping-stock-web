import React, { useState, useEffect, useCallback } from 'react';
import { Outlet, useLocation, useNavigate } from 'react-router-dom';
import { useAuth, API, canAccessPermission, canAccessMenuItem } from '../App';
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
import { NoticeLoginPopup } from '../components/NoticeLoginPopup';

export function DashboardLayout() {
  const { user, logout } = useAuth();
  const location = useLocation();
  const navigate = useNavigate();

  const [unreadCount, setUnreadCount] = useState(0);
  const [bellOpen, setBellOpen] = useState(false);
  const [alerts, setAlerts] = useState([]);
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

  const [brandOptions, setBrandOptions] = useState([]);
  const [dealerOptions, setDealerOptions] = useState([]);
  const [branchOptions, setBranchOptions] = useState([]);

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
  const isSpecificScope = (value) => Boolean(value) && !isAllScope(value);
  const normalizeScopeValue = (value) => String(value || "").trim().toLowerCase();
  const uniqueNames = (items, selector) => Array.from(new Set((items || []).map(selector).filter(Boolean))).sort((a, b) => String(a).localeCompare(String(b)));
  const recordBrand = (row) => String(row?.brand || row?.brand_name || "").trim();
  const recordDealer = (row) => String(row?.dealer || row?.dealer_name || "").trim();

  const navItems = APPLICATION_MENU_ITEMS;

  const filteredNavItems = navItems.filter((item) => canAccessMenuItem(user, item));

  useEffect(() => {
    if (!user) return;

    if (isMaster) {
      setScopeBrand("");
      setScopeDealer("");
      setScopeBranch("");
    } else {
      setScopeBrand(getUserBrand());
      setScopeDealer(getUserDealer());
      // Admin can switch between every branch under the assigned dealer.
      // Normal users stay locked to their assigned branch.
      setScopeBranch(isAdmin ? "" : getUserBranch());
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [user?.role, user?.brand, user?.dealer, user?.group, user?.branch, user?.location]);

  const loadScopeOptions = useCallback(async () => {
    if (!user) return;

    try {
      const res = await axios.get(`${API}/scope/options`);
      const data = res.data || {};

      const dbBrands = Array.isArray(data.brands) ? data.brands : [];
      const dbDealers = Array.isArray(data.dealers) ? data.dealers : [];
      const dbBranches = Array.isArray(data.branches) ? data.branches : [];
      setScopeMasters({ brands: dbBrands, dealers: dbDealers, branches: dbBranches });

      const brandNames = uniqueNames(dbBrands, (b) => b.name);

      if (isMaster) {
        setBrandOptions(brandNames);
        setScopeBrand((current) => (brandNames.includes(current) ? current : ""));
      } else if (isAdmin) {
        const adminBranches = uniqueNames(dbBranches, (b) => b.name);
        setBrandOptions([getUserBrand()].filter((name) => name && name !== "N/A"));
        setDealerOptions([getUserDealer()].filter((name) => name && name !== "N/A"));
        setBranchOptions(adminBranches);
      } else {
        setBrandOptions([getUserBrand()].filter((name) => name && name !== "N/A"));
        setDealerOptions([getUserDealer()].filter((name) => name && name !== "N/A"));
        setBranchOptions([getUserBranch()].filter((name) => name && name !== "N/A"));
      }
    } catch (error) {
      console.error("Scope options load failed", error);
      setScopeMasters({ brands: [], dealers: [], branches: [] });

      if (isMaster) {
        setBrandOptions([]);
        setDealerOptions([]);
        setBranchOptions([]);
      } else {
        setBrandOptions([getUserBrand()].filter((name) => name && name !== "N/A"));
        setDealerOptions([getUserDealer()].filter((name) => name && name !== "N/A"));
        setBranchOptions([getUserBranch()].filter((name) => name && name !== "N/A"));
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [user?.role, user?.brand, user?.dealer, user?.group, user?.branch, user?.location, isMaster, isAdmin]);

  useEffect(() => {
    loadScopeOptions();
    const reload = () => loadScopeOptions();
    window.addEventListener("nmts-masters-changed", reload);
    window.addEventListener("focus", reload);
    return () => {
      window.removeEventListener("nmts-masters-changed", reload);
      window.removeEventListener("focus", reload);
    };
  }, [loadScopeOptions]);

  useEffect(() => {
    if (!isMaster) return;

    if (!isSpecificScope(scopeBrand)) {
      setDealerOptions([]);
      setBranchOptions([]);
      if (scopeDealer) setScopeDealer("");
      if (scopeBranch) setScopeBranch("");
      return;
    }

    const dealersForBrand = (scopeMasters.dealers || []).filter((dealer) => recordBrand(dealer) === scopeBrand);
    const nextDealerNames = uniqueNames(dealersForBrand, (d) => d.name);
    setDealerOptions(nextDealerNames);

    const dealerSelected = isSpecificScope(scopeDealer) && nextDealerNames.includes(scopeDealer);
    if (!dealerSelected) {
      if (scopeDealer) setScopeDealer("");
      if (scopeBranch) setScopeBranch("");
      setBranchOptions([]);
      return;
    }

    const branchesForDealer = (scopeMasters.branches || []).filter((branch) => recordDealer(branch) === scopeDealer);
    const nextBranchNames = uniqueNames(branchesForDealer, (b) => b.name);
    setBranchOptions(nextBranchNames);
    if (scopeBranch && !nextBranchNames.includes(scopeBranch)) setScopeBranch("");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isMaster, scopeMasters, scopeBrand, scopeDealer, scopeBranch]);

  useEffect(() => {
    if (!isAdmin) return;
    const adminDealer = normalizeScopeValue(getUserDealer());
    const adminBrand = normalizeScopeValue(getUserBrand());
    const adminBranches = (scopeMasters.branches || []).filter((branch) => {
      const branchDealer = normalizeScopeValue(branch.dealer || branch.dealer_name);
      const branchBrand = normalizeScopeValue(branch.brand || branch.brand_name);
      const dealerMatches = branchDealer === adminDealer;
      const brandMatches = !branchBrand || branchBrand === adminBrand;
      return dealerMatches && brandMatches;
    });
    setBranchOptions(uniqueNames(adminBranches, (b) => b.name));
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
          ? parsedTabs.filter(
              (tab) =>
                canAccessPermission(user, tab.permission || tab.label) || tab.label === 'My Profile'
            )
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
    // eslint-disable-next-line react-hooks/exhaustive-deps
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

  // Ensure deep links / first menu click always open the matching module tab.
  // Fixes Storage & Data Cleanup requiring a second click when tabs were empty.
  useEffect(() => {
    const path = location.pathname;
    if (!path || path === '/' || path === '/login') return;
    const menuItem = navItems.find((item) => item.path === path);
    if (!menuItem) return;
    if (!canAccessMenuItem(user, menuItem) && menuItem.id !== 'profile') return;
    setTabs((prev) => {
      if (prev.some((t) => t.id === menuItem.id)) return prev;
      return [
        ...prev,
        {
          id: menuItem.id,
          label: menuItem.label,
          path: menuItem.path,
          permission: menuItem.permissionLabel || menuItem.label,
        },
      ];
    });
    setActiveTabId(menuItem.id);
    // navItems is a stable module constant (APPLICATION_MENU_ITEMS)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [location.pathname, user]);

  const fetchUnreadCount = async () => {
    try {
      const res = await axios.get(`${API}/user-alerts/unread-count`);
      setUnreadCount(res.data.count || 0);
    } catch {
      setUnreadCount(0);
    }
  };

  const fetchAlerts = async () => {
    try {
      const res = await axios.get(`${API}/user-alerts?limit=30`);
      setAlerts(Array.isArray(res.data) ? res.data : []);
    } catch {
      setAlerts([]);
    }
  };

  const handleLogout = () => {
    logout();
    clearAuthStorage();
    navigate('/login');
    toast.success('Logged out successfully');
  };

  const openTab = (id, label, path, permissionLabel) => {
    const menuItem = navItems.find((m) => m.id === id);
    const perm = permissionLabel !== undefined
      ? permissionLabel
      : (menuItem?.permissionLabel || label);
    const allowed =
      label === 'My Profile' ||
      (menuItem ? canAccessMenuItem(user, menuItem) : canAccessPermission(user, perm));
    if (!allowed) {
      toast.error('You do not have permission to open this screen');
      navigate('/');
      return;
    }

    const existingTab = tabs.find(tab => tab.id === id);

    if (!existingTab) {
      setTabs([...tabs, { id, label, path, permission: perm }]);
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
    if (canAccessPermission(user, 'Analytics')) {
      openTab('analytics', 'Analytics', '/analytics', 'Analytics');
    } else {
      const first = filteredNavItems[0];
      if (first) {
        openTab(first.id, first.label, first.path, first.permissionLabel || first.label);
      } else {
        setActiveTabId(null);
        navigate('/');
      }
    }
  };

  const toggleBell = async () => {
    const next = !bellOpen;
    setBellOpen(next);
    if (next) {
      await fetchAlerts();
      await fetchUnreadCount();
    }
  };

  const handleAlertClick = async (alert) => {
    try {
      if (!alert.is_read) {
        await axios.put(`${API}/user-alerts/${alert.id}/read`);
      }
    } catch {
      /* non-blocking */
    }
    setBellOpen(false);
    await fetchUnreadCount();
    const path = alert.link_path || '/';
    const source = alert.source_type;
    if (source === 'request') {
      openTab('requests', 'Request Center', '/requests', 'Request Center');
    } else if (source === 'notice') {
      openTab('dashboard', 'Notice Board', '/notice-board', 'Notice Board');
    } else if (source === 'query') {
      openTab('query', 'Query Desk', '/query', null);
    } else {
      navigate(path);
    }
  };

  const sourceLabel = (t) => {
    if (t === 'request') return 'Request';
    if (t === 'notice') return 'Notice';
    if (t === 'query') return 'Query';
    return t || 'Alert';
  };

  const formatAlertTime = (iso) => {
    if (!iso) return '';
    try {
      const d = new Date(iso);
      if (Number.isNaN(d.getTime())) return '';
      const diffMs = Date.now() - d.getTime();
      const mins = Math.floor(diffMs / 60000);
      if (mins < 1) return 'Just now';
      if (mins < 60) return `${mins}m ago`;
      const hours = Math.floor(mins / 60);
      if (hours < 24) return `${hours}h ago`;
      const days = Math.floor(hours / 24);
      if (days < 7) return `${days}d ago`;
      return d.toLocaleString();
    } catch {
      return '';
    }
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
              (item.path === '/analytics' &&
                (location.pathname === '/' || location.pathname === '/dashboard'));

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
                  placeholder="Select Brand"
                  onChange={(value) => {
                    setScopeBrand(value);
                    if (isMaster) {
                      setScopeDealer("");
                      setScopeBranch("");
                    }
                  }}
                  options={isMaster ? brandOptions : [scopeBrand].filter(Boolean)}
                  disabled={!isMaster}
                />
                <HeaderScopeSelect
                  label="Dealer"
                  value={scopeDealer}
                  placeholder="Select Dealer"
                  onChange={(value) => {
                    setScopeDealer(value);
                    if (isMaster || isAdmin) {
                      setScopeBranch("");
                    }
                  }}
                  options={isMaster ? dealerOptions : [scopeDealer].filter(Boolean)}
                  disabled={isMaster ? !isSpecificScope(scopeBrand) : !isMaster}
                />
                <HeaderScopeSelect
                  label="Branch"
                  value={scopeBranch}
                  placeholder="Select Branch"
                  onChange={setScopeBranch}
                  options={isMaster || isAdmin ? branchOptions : [scopeBranch].filter(Boolean)}
                  disabled={isMaster ? !isSpecificScope(scopeDealer) : (!isMaster && !isAdmin)}
                />
              </div>

              <div className="nmts-bell-wrap">
                <button
                  type="button"
                  className="nmts-bell-btn"
                  onClick={toggleBell}
                  aria-label="Notifications"
                  aria-expanded={bellOpen}
                >
                  <Bell className="h-5 w-5" />
                  {unreadCount > 0 && (
                    <span className="nmts-bell-badge">{unreadCount > 9 ? '9+' : unreadCount}</span>
                  )}
                </button>
                {bellOpen && (
                  <>
                    <button
                      type="button"
                      className="nmts-bell-backdrop"
                      aria-label="Close notifications"
                      onClick={() => setBellOpen(false)}
                    />
                    <div className="nmts-bell-panel" role="dialog" aria-label="Notification centre">
                      <div className="nmts-bell-panel-head">
                        <span>Notifications</span>
                        <span className="nmts-bell-panel-sub">Request · Notice · Query</span>
                      </div>
                      <div className="nmts-bell-panel-list">
                        {alerts.length === 0 ? (
                          <div className="nmts-bell-empty">No notifications</div>
                        ) : (
                          alerts.map((a) => (
                            <button
                              key={a.id}
                              type="button"
                              className={`nmts-bell-item${a.is_read ? '' : ' nmts-bell-item--unread'}`}
                              onClick={() => handleAlertClick(a)}
                            >
                              <div className="nmts-bell-item-type">{sourceLabel(a.source_type)}</div>
                              <div className="nmts-bell-item-title">{a.title || 'Alert'}</div>
                              {a.message ? <div className="nmts-bell-item-msg">{a.message}</div> : null}
                              <div className="nmts-bell-item-time">{formatAlertTime(a.created_at)}</div>
                            </button>
                          ))
                        )}
                      </div>
                    </div>
                  </>
                )}
              </div>

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
          {tabs.length === 0 && (location.pathname === '/' || location.pathname === '') ? (
            <div className="nmts-empty-workspace" aria-label="Empty workspace">
              <img
                src="/sleeping-stock-logo-transparent.png"
                alt="Sleeping Stock"
                className="nmts-empty-workspace-logo"
              />
            </div>
          ) : (
            <Outlet
              context={{
                scopeBrand,
                scopeDealer,
                scopeBranch,
                setScopeBrand,
                setScopeDealer,
                setScopeBranch,
                brandOptions,
                dealerOptions,
                branchOptions,
                isMaster,
                isAdmin,
              }}
            />
          )}
        </main>
      </div>
    </div>
  );
}

function HeaderScopeSelect({ label, value, onChange, options, disabled, placeholder }) {
  const safeOptions = (options || []).filter(Boolean);
  const selectValue = safeOptions.includes(value) ? value : "";
  return (
    <label className="nmts-header-scope-field">
      <span className="nmts-header-scope-label">{label}</span>
      <select
        value={selectValue}
        disabled={disabled}
        onChange={(e) => onChange(e.target.value)}
        className="nmts-header-scope-select"
        title={label}
      >
        <option value="">{placeholder || `Select ${label}`}</option>
        {safeOptions.map((item) => (
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
