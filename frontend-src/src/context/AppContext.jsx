import React, { createContext, useContext, useState, useEffect, useRef } from 'react';
import { api } from '../utils/api';
import { useWebSocket } from '../utils/useWebSocket';

const AppContext = createContext();

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
  if (normalized === '/stats/settings') return 'st';
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
    case 'st': return '/stats/settings';
    default: return '/stats';
  }
};

export function AppProvider({ children }) {
  const [token, setToken] = useState(() => sessionStorage.getItem('_rt') || null);
  const [user, setUser] = useState(null);
  const [activeTab, setActiveTab] = useState(() => {
    const tab = getTabFromPath(window.location.pathname);
    return tab || 'ov';
  });
  const [theme, setTheme] = useState(() => localStorage.getItem('_rtm') || 'auto');
  const [lang, setLang] = useState(() => localStorage.getItem('_rl') || 'vi');
  const [fontSize, setFontSize] = useState(() => localStorage.getItem('_rfs') || '100%');
  
  // Easter egg tracker states
  const [foundEggs, setFoundEggs] = useState(() => {
    try {
      return JSON.parse(localStorage.getItem('_eggs') || '[]');
    } catch {
      return [];
    }
  });
  const [toast, setToast] = useState(null);

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
      blizzard: 'Cherry Blossom windstorm 🌸',
      matrix: 'Hacker matrix search console 📟',
      gemini: 'Gemini golden sparkles 🌟',
      claude: "Claude's hidden crying face 😢",
      clicker: 'Status dot clicker mini-game 🎮'
    };

    const eggName = eggsMap[eggId] || eggId;
    setToast({ message: `🎉 Unlocked Egg: ${eggName} (${newEggs.length}/9)!`, type: 'egg' });
    setTimeout(() => setToast(null), 4000);

    // Dispatch custom window event
    window.dispatchEvent(new CustomEvent('egg-unlocked', { detail: { id: eggId } }));
  };


  // Sync token to session storage
  useEffect(() => {
    if (token) {
      sessionStorage.setItem('_rt', token);
    } else {
      sessionStorage.removeItem('_rt');
      setUser(null);
    }
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

  // Validate activeTab accessibility when user/tier loads or changes
  useEffect(() => {
    if (user) {
      const isAdminTab = ['ov', 'ks', 'ac', 'us', 'ep', 'pe', 'mu', 'st'].includes(activeTab);
      if (isAdminTab && user.tier !== 'admin') {
        setActiveTab('myacc');
      }
    }
  }, [user, activeTab]);

  // Helper to fetch data for the active tab
  const fetchTabData = async (tabName, isSilent = false) => {
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
        case 'ep':
          const eps = await api('/dashboard/endpoints', {}, token);
          const accsForEp = await api('/dashboard/accounts', {}, token);
          setTabData(prev => ({ 
            ...prev, 
            ep: eps.endpoints || [], 
            ac: accsForEp.accounts || [] 
          }));
          break;
        case 'pe':
          data = await api('/dashboard/penalties', {}, token);
          setTabData(prev => ({ ...prev, pe: data.penalties || [] }));
          break;
        case 'mu':
          const costData = await api('/api/stats?days=30', {}, token);
          const poolDetail = await api('/api/model-pools-detail', {}, token);
          setTabData(prev => ({ 
            ...prev, 
            mu: {
              savings: costData.savings || {},
              pools: poolDetail.pools || []
            } 
          }));
          break;
        case 'myacc':
          data = await api('/dashboard/me', {}, token);
          setTabData(prev => ({ ...prev, myacc: data }));
          // If we loaded current user profile, sync it to context user state too
          setUser({ name: data.name, tier: data.tier });
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
  };

  // Re-fetch when active tab changes or user changes
  useEffect(() => {
    if (token) {
      fetchTabData(activeTab);
    }
  }, [activeTab, token]);

  // Load user profile on startup to determine tier/admin menu rendering
  useEffect(() => {
    if (token && !user) {
      fetchTabData('myacc', true); // Silent load
    }
  }, [token, user]);

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
  }, [activeTab, token]);

  // Global shared WebSocket
  const wsHook = useWebSocket(token);

  // Auth operations
  const login = async (authKey) => {
    const response = await fetch('/dashboard/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ auth_key: authKey }),
    });
    
    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}));
      throw new Error(errorData.error || 'Invalid auth key');
    }
    
    const data = await response.json();
    setToken(data.token);
    setUser({ name: data.name, tier: data.tier });
    // Default routing: preserve requested tab if authorized, otherwise redirect
    const isAdminTab = ['ov', 'ks', 'ac', 'us', 'ep', 'pe', 'mu', 'st'].includes(activeTab);
    if (data.tier === 'admin') {
      if (!activeTab || !['ov', 'ks', 'ac', 'us', 'ep', 'pe', 'mu', 'myacc', 'myuse', 'st'].includes(activeTab)) {
        setActiveTab('ov');
      }
    } else {
      if (isAdminTab || !['myacc', 'myuse'].includes(activeTab)) {
        setActiveTab('myacc');
      }
    }
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
      foundEggs, unlockEgg, toast, wsHook, fontSize, setFontSize,
      setActiveTab, setTheme, changeLanguage, login, logout, refreshTab: () => fetchTabData(activeTab, false)
    }}>
      {children}
    </AppContext.Provider>
  );
}

export function useApp() {
  const context = useContext(AppContext);
  if (!context) {
    throw new Error('useApp must be used within an AppProvider');
  }
  return context;
}
