import { createContext, useContext, useState, useEffect, useRef, useCallback } from 'react';
import { api } from '../utils/api';
import { useWebSocket } from '../utils/useWebSocket';

const AppContext = createContext();

/* Tabs a non-admin may not see. Kept module-level so the routing guard and the
   login redirect cannot drift apart. */
const ADMIN_TABS = ['ov', 'ks', 'ac', 'us', 'ep', 'pe', 'mu', 'md', 'st'];

const getTabFromPath = (path) => {
  const normalized = path.replace(/\/$/, '').toLowerCase();
  if (normalized === '/stats' || normalized === '/stats/') return 'ov';
  if (normalized === '/stats/overview') return 'ov';
  if (normalized === '/stats/keys') return 'ks';
  if (normalized === '/stats/accounts') return 'ac';
  if (normalized === '/stats/token-analysis') return 'us';
  if (normalized === '/stats/endpoints') return 'ep';
  if (normalized === '/stats/penalties') return 'pe';
  if (normalized === '/stats/pool-structure') return 'mu';
  if (normalized === '/stats/my-account') return 'myacc';
  if (normalized === '/stats/my-usage') return 'myuse';
  if (normalized === '/stats/model-config') return 'md';
  if (normalized === '/stats/model-aliases') return 'ma';
  if (normalized === '/stats/combo-models') return 'cm';
  if (normalized === '/stats/settings') return 'st';
  if (normalized === '/stats/help') return 'help';
  return null;
};

const getPathFromTab = (tab) => {
  switch (tab) {
    case 'ov': return '/stats/overview';
    case 'ks': return '/stats/keys';
    case 'ac': return '/stats/accounts';
    case 'us': return '/stats/token-analysis';
    case 'ep': return '/stats/endpoints';
    case 'pe': return '/stats/penalties';
    case 'mu': return '/stats/pool-structure';
    case 'myacc': return '/stats/my-account';
    case 'myuse': return '/stats/my-usage';
    case 'md': return '/stats/model-config';
    case 'ma': return '/stats/model-aliases';
    case 'cm': return '/stats/combo-models';
    case 'st': return '/stats/settings';
    case 'help': return '/stats/help';
    default: return '/stats';
  }
};

export function AppProvider({ children }) {
  const [token, setToken] = useState(() => sessionStorage.getItem('_rt') || null);
  const [user, setUser] = useState(null);
  const [requestedTab, setActiveTab] = useState(() => {
    const tab = getTabFromPath(window.location.pathname);
    return tab || 'ov';
  });
  const [theme, setTheme] = useState(() => localStorage.getItem('_rtm') || 'auto');
  const [lang, setLang] = useState(() => localStorage.getItem('_rl') || 'vi');
  const [fontSize, setFontSize] = useState(() => localStorage.getItem('_rfs') || '100%');
  
  /* The tab the user asked for is not always the tab they may see: a session
     whose tier drops, or a bookmarked admin URL opened by a free user. Resolving
     that here, as a derivation, means one render paints the right tab - no
     flash of an admin panel followed by a redirect. */
  const activeTab = user && user.tier !== 'admin' && ADMIN_TABS.includes(requestedTab)
    ? 'myacc'
    : requestedTab;

  // Easter egg tracker states
  const [foundEggs, setFoundEggs] = useState(() => {
    try {
      return JSON.parse(localStorage.getItem('_eggs') || '[]');
    } catch {
      return [];
    }
  });
  const [toast, setToast] = useState(null);
  const [eggShakeId, setEggShakeId] = useState(null);
  const eggShakeTimer = useRef(null);

  // Tab-specific data stores
  const [tabData, setTabData] = useState({
    ov: null,     // Overview stats
    ks: null,     // Gemini Keys
    ac: null,     // Accounts
    us: null,     // Token analysis rankings
    ep: null,     // Endpoints
    pe: null,     // Penalties
    mu: null,     // Model pools detail
    myacc: null,  // Current user account and pools
    myuse: null,  // Current user usage stats
    ma: null,     // Model aliases
    st: null,     // System Settings
  });

  const [loading, setLoading] = useState(false);
  const refreshTimerRef = useRef(null);

  const unlockEgg = (eggId) => {
    if (foundEggs.includes(eggId)) return;
    const newEggs = [...foundEggs, eggId];
    setFoundEggs(newEggs);
    localStorage.setItem('_eggs', JSON.stringify(newEggs));

    const eggsMap = {
      konami: 'Konami Code Disco Party 🕺',
      savings: 'Saved vs Claude Cash Shower 💸',
      sun: 'Sun Solar Flare super spin ☀️',
      moon: 'Meteor Storm summoner ☄️',
      claude: "Claude's hidden crying face 😢"
    };

    // Brief shake feedback so the player knows the input landed.
    setEggShakeId(eggId);
    if (eggShakeTimer.current) clearTimeout(eggShakeTimer.current);
    eggShakeTimer.current = setTimeout(() => setEggShakeId(null), 700);

    const eggName = eggsMap[eggId] || eggId;
    setToast({ message: `🎉 Unlocked Egg: ${eggName} (${newEggs.length}/5)!`, type: 'egg' });
    setTimeout(() => setToast(null), 4000);

    // Dispatch custom window event
    window.dispatchEvent(new CustomEvent('egg-unlocked', { detail: { id: eggId } }));
  };


  // Mirror the token into sessionStorage. `user` is cleared by every path that
  // clears the token (login, register, logout), so it is not mirrored here.
  useEffect(() => {
    if (token) sessionStorage.setItem('_rt', token);
    else sessionStorage.removeItem('_rt');
  }, [token]);

  // Handle Theme switching
  useEffect(() => {
    localStorage.setItem('_rtm', theme);
    const root = document.documentElement;
    root.classList.remove('theme-light', 'theme-sakura');
    
    let active = theme;
    if (theme === 'auto') {
      active = window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
    }
    
    // Set html element attributes for Tailwind and custom styles
    root.setAttribute('data-theme', active === 'dark' ? 'dark' : active === 'light' ? 'light' : 'valentine');
    if (active === 'light') root.classList.add('theme-light');
    if (active === 'sakura' || active === 'valentine') root.classList.add('theme-sakura');
  }, [theme]);

  // Handle Font Size scaling
  useEffect(() => {
    localStorage.setItem('_rfs', fontSize);
    document.documentElement.style.fontSize = fontSize;
  }, [fontSize]);

  // Listen for system theme changes if 'auto'
  useEffect(() => {
    const mediaQuery = window.matchMedia('(prefers-color-scheme: dark)');
    const handleChange = () => {
      if (theme === 'auto') {
        const root = document.documentElement;
        root.classList.remove('theme-light', 'theme-sakura');
        const active = mediaQuery.matches ? 'dark' : 'light';
        root.setAttribute('data-theme', active);
        if (active === 'light') root.classList.add('theme-light');
      }
    };
    mediaQuery.addEventListener('change', handleChange);
    return () => mediaQuery.removeEventListener('change', handleChange);
  }, [theme]);

  // Sync language selection
  const changeLanguage = (newLang) => {
    setLang(newLang);
    localStorage.setItem('_rl', newLang);
  };

  // Sync tab changes to browser URL history
  useEffect(() => {
    const newPath = getPathFromTab(activeTab);
    if (window.location.pathname !== newPath) {
      window.history.pushState(null, '', newPath);
    }
  }, [activeTab]);

  // Listen to popstate event (back/forward browser buttons)
  useEffect(() => {
    const handlePopState = () => {
      const tab = getTabFromPath(window.location.pathname);
      if (tab) {
        setActiveTab(tab);
      }
    };
    window.addEventListener('popstate', handlePopState);
    return () => window.removeEventListener('popstate', handlePopState);
  }, []);

  /* Helper to fetch data for the active tab. `isCurrent` is passed only by the
     effects, so a response that arrives after logout or a token change cannot
     resurrect a stale profile. */
  const fetchTabData = useCallback(async (tabName, isSilent = false, isCurrent = () => true) => {
    if (!token) return;
    if (!isSilent) setLoading(true);
    
    try {
      let data = null;
      switch (tabName) {
        case 'ov':
          data = await api('/api/stats?days=30', {}, token);
          setTabData(prev => ({ ...prev, ov: data }));
          break;
        case 'ks':
          data = await api('/dashboard/keys', {}, token);
          setTabData(prev => ({ ...prev, ks: data.keys || [] }));
          break;
        case 'ac':
          data = await api('/dashboard/accounts', {}, token);
          setTabData(prev => ({ ...prev, ac: data.accounts || [] }));
          break;
        case 'us':
          data = await api('/api/stats?days=30', {}, token);
          setTabData(prev => ({ ...prev, us: data.top_keys || [] }));
          break;
        case 'ep': {
          const eps = await api('/dashboard/endpoints', {}, token);
          const accsForEp = await api('/dashboard/accounts', {}, token);
          if (!isCurrent()) return;
          setTabData(prev => ({
            ...prev,
            ep: eps.endpoints || [],
            ac: accsForEp.accounts || []
          }));
          break;
        }
        case 'pe':
          data = await api('/dashboard/penalties', {}, token);
          setTabData(prev => ({ ...prev, pe: data.penalties || [] }));
          break;
        case 'mu': {
          const costData = await api('/api/stats?days=30', {}, token);
          const poolDetail = await api('/api/model-pools-detail', {}, token);
          if (!isCurrent()) return;
          setTabData(prev => ({
            ...prev,
            mu: {
              savings: costData.savings || {},
              pools: poolDetail.pools || [],
              standalone_models: poolDetail.standalone_models || [],
            }
          }));
          break;
        }
        case 'myacc':
          data = await api('/dashboard/me', {}, token);
          if (!isCurrent()) return;
          setTabData(prev => ({ ...prev, myacc: data }));
          // If we loaded current user profile, sync it to context user state too
          setUser({ name: data.name, tier: data.tier, must_change_password: data.must_change_password });
          break;
        case 'myuse':
          data = await api('/dashboard/my-stats?days=30', {}, token);
          setTabData(prev => ({ ...prev, myuse: data }));
          break;
        case 'st':
          data = await api('/dashboard/admin/settings', {}, token);
          setTabData(prev => ({ ...prev, st: data }));
          break;
        default:
          break;
      }
    } catch (err) {
      console.error(`Error loading tab ${tabName}:`, err);
    } finally {
      if (!isSilent) setLoading(false);
    }
  }, [token]);

  // Re-fetch when active tab changes or user changes
  useEffect(() => {
    if (!token) return;
    let alive = true;
    (async () => { await fetchTabData(activeTab, false, () => alive); })();
    return () => { alive = false; };
  }, [activeTab, token, fetchTabData]);

  // Load user profile on startup to determine tier/admin menu rendering
  useEffect(() => {
    if (!token) return;
    let alive = true;
    (async () => {
      if (!user) await fetchTabData('myacc', true, () => alive); // Silent load
    })();
    return () => { alive = false; };
  }, [token, user, fetchTabData]);

  // Setup dynamic auto-refresh loop depending on the active tab
  useEffect(() => {
    if (refreshTimerRef.current) clearInterval(refreshTimerRef.current);
    if (!token) return;

    const intervalMap = {
      myacc: 1500,  // 1.5s
      ov: 1500,     // 1.5s
      ks: 2000,     // 2s
      pe: 2000,     // 2s
      myuse: 2000,  // 2s
    };
    const interval = intervalMap[activeTab] || 10000; // 10s default

    refreshTimerRef.current = setInterval(() => {
      fetchTabData(activeTab, true);
    }, interval);

    return () => {
      if (refreshTimerRef.current) clearInterval(refreshTimerRef.current);
    };
  }, [activeTab, token, fetchTabData]);

  // Global shared WebSocket
  const wsHook = useWebSocket(token);

  // Auth operations
  const login = async (username, password) => {
    const response = await fetch('/dashboard/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password }),
    });
    
    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}));
      throw new Error(errorData.error || 'Invalid auth key');
    }
    
    const data = await response.json();
    setToken(data.token);
    setUser({ name: data.name, tier: data.tier, must_change_password: data.must_change_password });
    // Default routing: preserve requested tab if authorized, otherwise redirect
    const isAdminTab = ADMIN_TABS.includes(activeTab);
    if (data.tier === 'admin') {
      if (!activeTab || !['ov', 'ks', 'ac', 'us', 'ep', 'pe', 'mu', 'md', 'myacc', 'myuse', 'st', 'help'].includes(activeTab)) {
        setActiveTab('ov');
      }
    } else {
      if (isAdminTab || !['myacc', 'myuse'].includes(activeTab)) {
        setActiveTab('myacc');
      }
    }
    return data;
  };

  const register = async (inviteCode, username, password) => {
    const response = await fetch('/dashboard/register', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ invite_code: inviteCode, name: username, password }),
    });
    
    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}));
      throw new Error(errorData.detail || 'Registration failed');
    }
    
    const data = await response.json();
    setToken(data.token);
    setUser({ name: data.name, tier: 'free', must_change_password: data.must_change_password });
    setActiveTab('myacc');
    return data;
  };

  const logout = () => {
    setToken(null);
    setUser(null);
    sessionStorage.removeItem('_rt');
    setTabData({
      ov: null, ks: null, ac: null, us: null, ep: null, pe: null, mu: null, myacc: null, myuse: null, st: null
    });
  };

  return (
    <AppContext.Provider value={{
      token, user, activeTab, theme, lang, tabData, loading,
      foundEggs, unlockEgg, toast, wsHook, fontSize, setFontSize, eggShakeId,
      setActiveTab, setTheme, changeLanguage, login, register, logout, refreshTab: () => fetchTabData(activeTab, false)
    }}>
      {children}
    </AppContext.Provider>
  );
}

// Not a component export, but it belongs beside the context it reads; splitting
// the file in two would cost every consumer an import change for no behaviour.
// eslint-disable-next-line react-refresh/only-export-components
export function useApp() {
  const context = useContext(AppContext);
  if (!context) {
    throw new Error('useApp must be used within an AppProvider');
  }
  return context;
}
