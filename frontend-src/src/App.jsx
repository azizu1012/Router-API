import React, { useState, useEffect } from 'react';
import { useApp } from './context/AppContext';
import { t } from './utils/i18n';
import CanvasParticles from './components/CanvasParticles';
import ThemeLanguageSelector from './components/ThemeLanguageSelector';
import { 
  LayoutDashboard, Key, Users, PieChart, Network, 
  AlertTriangle, ShieldCheck, User, BarChart3, LogOut, Lock, Eye, EyeOff, Settings
} from 'lucide-react';

// Lazy load tab components
import OverviewTab from './tabs/OverviewTab';
import KeysTab from './tabs/KeysTab';
import AccountsTab from './tabs/AccountsTab';
import TokenAnalysisTab from './tabs/TokenAnalysisTab';
import EndpointsTab from './tabs/EndpointsTab';
import PenaltiesTab from './tabs/PenaltiesTab';
import PoolStructureTab from './tabs/PoolStructureTab';
import MyAccountTab from './tabs/MyAccountTab';
import MyUsageTab from './tabs/MyUsageTab';
import SettingsTab from './tabs/SettingsTab';

export default function App() {
  const { 
    token, user, activeTab, setActiveTab, lang, login, logout, loading,
    foundEggs, unlockEgg, toast
  } = useApp();

  const [devToolsOpen, setDevToolsOpen] = useState(false);
  const [authKey, setAuthKey] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [loginError, setLoginError] = useState('');
  const [isLoggingIn, setIsLoggingIn] = useState(false);

  const [showEggsModal, setShowEggsModal] = useState(false);
  const [statusClicks, setStatusClicks] = useState(0);
  const [showClicker, setShowClicker] = useState(false);
  const [clickerScore, setClickerScore] = useState(0);
  const [sakuraClicks, setSakuraClicks] = useState(0);

  // 1. Konami Code Listener & custom egg-unlocked listener
  useEffect(() => {
    let input = [];
    const target = ['arrowup', 'arrowup', 'arrowdown', 'arrowdown', 'arrowleft', 'arrowright', 'arrowleft', 'arrowright', 'b', 'a'];
    
    const handleKeyDown = (e) => {
      const key = e.key.toLowerCase();
      input.push(key);
      if (input.length > 10) input.shift();
      
      if (input.join(',') === target.join(',')) {
        unlockEgg('konami');
        window.dispatchEvent(new CustomEvent('trigger-back-event', { detail: { name: 'disco' } }));
        const main = document.getElementById('root') || document.body;
        if (main) {
          main.style.transition = 'transform 1.5s ease-in-out';
          main.style.transform = 'rotate(360deg)';
          setTimeout(() => {
            main.style.transform = 'none';
          }, 1500);
        }
      }
    };
    
    const handleEggEvent = (e) => {
      if (e.detail?.id) {
        unlockEgg(e.detail.id);
      }
    };

    document.addEventListener('keydown', handleKeyDown);
    window.addEventListener('egg-unlocked-event', handleEggEvent);
    
    return () => {
      document.removeEventListener('keydown', handleKeyDown);
      window.removeEventListener('egg-unlocked-event', handleEggEvent);
    };
  }, [unlockEgg]);

  const handleSakuraClick = () => {
    const activeTheme = document.documentElement.getAttribute('data-theme') || 'dark';
    if (activeTheme === 'valentine') {
      setSakuraClicks(prev => {
        const next = prev + 1;
        if (next >= 5) {
          window.dispatchEvent(new CustomEvent('trigger-back-event', { detail: { name: 'blizzard' } }));
          unlockEgg('blizzard');
          return 0;
        }
        return next;
      });
    }
  };

  const handleStatusClick = () => {
    setStatusClicks(prev => {
      const next = prev + 1;
      if (next >= 3) {
        setShowClicker(true);
        return 0;
      }
      return next;
    });
  };

  const eggsList = [
    { id: 'konami', name: 'Konami Code Disco Party 🕺', hint: 'Cheat code of the retro gaming era: Up, Up, Down, Down, Left, Right...' },
    { id: 'savings', name: 'Saved vs Claude Cash Shower 💸', hint: 'Click the Savings KPI card to shower the screen in cash.' },
    { id: 'sun', name: 'Sun Solar Flare Super-Spin ☀️', hint: 'Click the Golden Sun 5 times in Light theme.' },
    { id: 'moon', name: 'Meteor Storm Summoner ☄️', hint: 'Click the Crescent Moon 5 times in Dark theme.' },
    { id: 'blizzard', name: 'Cherry Blossom Windstorm 🌸', hint: 'Click the Valentine Sakura badge/tabs 5 times in Sakura mode.' },
    { id: 'matrix', name: 'Hacker Matrix Search Console 📟', hint: 'Search accounts/keys for access credentials like "admin", "root", "hacker".' },
    { id: 'gemini', name: 'Gemini Golden Sparkles 🌟', hint: 'Search accounts/keys for a specific modern AI model family name.' },
    { id: 'claude', name: "Claude's Crying Disgrace 😢", hint: 'Double-click the Savings card to flip it and make Claude cry.' },
    { id: 'clicker', name: 'Status Dot Clicker Mini-game 🎮', hint: 'Click the pulsing green online status indicator at the top left 3 times.' },
  ];

  // 1. DevTools Warning Guard
  useEffect(() => {
    const handleContextMenu = e => e.preventDefault();
    document.addEventListener('contextmenu', handleContextMenu);

    const handleKeyDown = e => {
      if (e.key === 'F12') e.preventDefault();
      if (e.ctrlKey && e.shiftKey && 'IJC'.includes(e.key.toUpperCase())) e.preventDefault();
      if (e.ctrlKey && e.key.toLowerCase() === 'u') e.preventDefault();
    };
    document.addEventListener('keydown', handleKeyDown);

    const interval = setInterval(() => {
      const threshold = 160;
      const isOpen = (window.outerWidth - window.innerWidth > threshold) ||
                     (window.outerHeight - window.innerHeight > threshold);
      setDevToolsOpen(isOpen);
    }, 1000);

    return () => {
      document.removeEventListener('contextmenu', handleContextMenu);
      document.removeEventListener('keydown', handleKeyDown);
      clearInterval(interval);
    };
  }, []);

  // 2. Handle Login
  const handleLoginSubmit = async (e) => {
    e.preventDefault();
    if (!authKey.trim()) {
      setLoginError(t('err_auth_key_required', lang));
      return;
    }
    setLoginError('');
    setIsLoggingIn(true);
    try {
      await login(authKey.trim());
    } catch (err) {
      setLoginError(err.message || t('err_invalid_auth_key', lang));
    } finally {
      setIsLoggingIn(false);
    }
  };

  // Render security block screen if DevTools is detected
  if (devToolsOpen) {
    return (
      <div id="dtw-screen">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"></path>
          <line x1="12" y1="8" x2="12" y2="12"></line>
          <line x1="12" y1="16" x2="12.01" y2="16"></line>
        </svg>
        <h2 className="text-xl font-bold mb-2">{t('dtw_title', lang)}</h2>
        <p className="text-sm opacity-80" dangerouslySetInnerHTML={{ __html: t('dtw_desc', lang) }} />
      </div>
    );
  }

  // 3. Render Login Screen if not authenticated
  if (!token) {
    return (
      <div className="relative min-h-screen w-screen flex items-center justify-center p-4">
        <CanvasParticles />
        
        {/* Floating Theme & Language Selector at login */}
        <div className="absolute top-4 right-4 z-20 animate-tab-in cascade-1">
          <ThemeLanguageSelector />
        </div>

        {/* Glowing Gradient Border Card Container */}
        <div className="relative group w-full max-w-md p-[1px] rounded-3xl bg-gradient-to-tr from-indigo-500/10 via-purple-500/5 to-pink-500/10 hover:from-indigo-500/35 hover:via-purple-500/25 hover:to-pink-500/35 transition-all duration-700 shadow-2xl z-10 animate-tab-in">
          <div className="card w-full bg-base-100/35 backdrop-blur-2xl p-8 rounded-[23px] border border-white/5 text-left">
            <div className="flex flex-col items-center mb-8">
              <div className="w-12 h-12 bg-indigo-500/20 text-indigo-400 rounded-xl flex items-center justify-center mb-3 shadow-inner">
                <Lock className="w-6 h-6" />
              </div>
              <h1 className="text-2xl font-black tracking-tight text-base-content text-center">{t('login_title', lang)}</h1>
              <p className="text-xs text-base-content/60 mt-1 text-center">{t('login_desc', lang)}</p>
            </div>

            <form onSubmit={handleLoginSubmit} className="space-y-6">
              <div className="form-control w-full">
                <label className="label">
                  <span className="label-text font-bold text-xs uppercase tracking-wide text-base-content/75">{t('login_label', lang)}</span>
                </label>
                <div className="relative flex items-center">
                  <input 
                    type={showPassword ? "text" : "password"}
                    value={authKey}
                    onChange={(e) => setAuthKey(e.target.value)}
                    placeholder="sk-..." 
                    className="input input-bordered w-full pr-12 text-sm bg-base-200/50 focus:border-indigo-500 focus:outline-none"
                    autoComplete="off"
                    spellCheck="false"
                  />
                  <button 
                    type="button" 
                    onClick={() => setShowPassword(!showPassword)}
                    className="absolute right-3 btn btn-ghost btn-xs btn-circle text-base-content/60"
                  >
                    {showPassword ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                  </button>
                </div>
              </div>

              {loginError && (
                <div className="text-error text-xs font-semibold bg-error/10 p-3 rounded-lg border border-error/20">
                  ⚠️ {loginError}
                </div>
              )}

              <button 
                type="submit" 
                disabled={isLoggingIn}
                className="btn btn-primary w-full shadow-lg shadow-indigo-500/20 hover:scale-[1.01] active:scale-[0.99] transition-all duration-300 font-bold"
              >
                {isLoggingIn ? (
                  <>
                    <span className="loading loading-spinner loading-xs"></span>
                    {t('btn_authenticating', lang)}
                  </>
                ) : (
                  t('login_btn', lang)
                )}
              </button>
            </form>

            <div className="mt-8 text-center text-xs text-base-content/50">
              {t('login_footer', lang)}
            </div>
          </div>
        </div>
      </div>
    );
  }

  const isAdmin = user?.tier === 'admin';

  // 4. Render Main Dashboard Shell
  return (
    <div className="relative min-h-screen w-screen flex flex-col">
      <CanvasParticles />
      
      {/* Header */}
      <header className="h-16 flex items-center justify-between px-6 border-b border-base-content/5 glass-nav z-30 fixed top-0 w-full">
        <div onClick={handleStatusClick} className="flex items-center gap-3 cursor-pointer select-none group">
          <div className="w-2.5 h-2.5 rounded-full bg-emerald-500 animate-pulse group-hover:scale-125 transition-transform"></div>
          <span className="font-extrabold text-sm tracking-tight group-hover:text-primary transition-colors">{t('header_title', lang)}</span>
          {loading && <span className="loading loading-ring loading-xs text-indigo-400"></span>}
        </div>
        
        <div className="flex items-center gap-6">
          <button 
            onClick={() => setShowEggsModal(true)}
            className="btn btn-xs btn-outline hover:bg-amber-500/10 border-amber-500/25 text-amber-500 gap-1.5 rounded-full font-bold transition-all hover:scale-105 active:scale-95"
            title="Click to view easter eggs tracker"
          >
            <span>🎯 Eggs: {foundEggs.length}/9</span>
          </button>
          <ThemeLanguageSelector />
          
          <div onClick={handleSakuraClick} className="flex items-center gap-2 cursor-pointer select-none hover:scale-105 transition-all">
            <div className="w-8 h-8 rounded-full bg-indigo-500/20 text-indigo-400 font-bold flex items-center justify-center text-xs uppercase border border-indigo-500/30">
              {user?.name?.substring(0, 2).toUpperCase()}
            </div>
            <div className="flex flex-col text-left">
              <span className="text-xs font-bold text-base-content/90 leading-tight">{user?.name}</span>
              <span className={`badge badge-xs text-[9px] font-extrabold uppercase mt-0.5 ${
                user?.tier === 'admin' ? 'badge-primary' : user?.tier === 'premium' ? 'badge-accent' : 'badge-ghost'
              }`}>
                {user?.tier}
              </span>
            </div>
          </div>
          
          <button 
            onClick={logout}
            className="btn btn-ghost btn-sm text-error/80 hover:text-error hover:bg-error/10 gap-2 normal-case font-bold"
          >
            <LogOut className="w-4 h-4" />
            <span>{t('logout_btn', lang)}</span>
          </button>
        </div>
      </header>

      {/* Main Body */}
      <div className="flex flex-1 pt-16 h-[calc(100vh-4rem)] overflow-hidden">
        {/* Sidebar */}
        <aside className="w-64 glass-nav flex flex-col p-4 gap-1 z-20 overflow-y-auto h-full">
          {/* Admin Navigation */}
          {isAdmin && (
            <>
              <button 
                onClick={() => setActiveTab('ov')} 
                className={`btn btn-sm btn-ghost justify-start gap-3 normal-case font-medium w-full text-left rounded-xl ${activeTab === 'ov' ? 'bg-primary/15 text-primary font-bold border border-primary/20' : 'text-base-content/75'}`}
              >
                <LayoutDashboard className="w-4 h-4" />
                <span>{t('nav_ov', lang)}</span>
              </button>
              
              <button 
                onClick={() => setActiveTab('ks')} 
                className={`btn btn-sm btn-ghost justify-start gap-3 normal-case font-medium w-full text-left rounded-xl ${activeTab === 'ks' ? 'bg-primary/15 text-primary font-bold border border-primary/20' : 'text-base-content/75'}`}
              >
                <Key className="w-4 h-4" />
                <span>{t('nav_ks', lang)}</span>
              </button>
              
              <button 
                onClick={() => setActiveTab('ac')} 
                className={`btn btn-sm btn-ghost justify-start gap-3 normal-case font-medium w-full text-left rounded-xl ${activeTab === 'ac' ? 'bg-primary/15 text-primary font-bold border border-primary/20' : 'text-base-content/75'}`}
              >
                <Users className="w-4 h-4" />
                <span>{t('nav_ac', lang)}</span>
              </button>
              
              <div className="divider opacity-20 my-2"></div>
              
              <button 
                onClick={() => setActiveTab('us')} 
                className={`btn btn-sm btn-ghost justify-start gap-3 normal-case font-medium w-full text-left rounded-xl ${activeTab === 'us' ? 'bg-primary/15 text-primary font-bold border border-primary/20' : 'text-base-content/75'}`}
              >
                <PieChart className="w-4 h-4" />
                <span>{t('nav_us', lang)}</span>
              </button>
              
              <button 
                onClick={() => setActiveTab('ep')} 
                className={`btn btn-sm btn-ghost justify-start gap-3 normal-case font-medium w-full text-left rounded-xl ${activeTab === 'ep' ? 'bg-primary/15 text-primary font-bold border border-primary/20' : 'text-base-content/75'}`}
              >
                <Network className="w-4 h-4" />
                <span>{t('nav_ep', lang)}</span>
              </button>
              
              <button 
                onClick={() => setActiveTab('pe')} 
                className={`btn btn-sm btn-ghost justify-start gap-3 normal-case font-medium w-full text-left rounded-xl ${activeTab === 'pe' ? 'bg-primary/15 text-primary font-bold border border-primary/20' : 'text-base-content/75'}`}
              >
                <AlertTriangle className="w-4 h-4" />
                <span>{t('nav_pe', lang)}</span>
              </button>
              
              <button 
                onClick={() => setActiveTab('mu')} 
                className={`btn btn-sm btn-ghost justify-start gap-3 normal-case font-medium w-full text-left rounded-xl ${activeTab === 'mu' ? 'bg-primary/15 text-primary font-bold border border-primary/20' : 'text-base-content/75'}`}
              >
                <ShieldCheck className="w-4 h-4" />
                <span>{t('nav_mu', lang)}</span>
              </button>

              <button 
                onClick={() => setActiveTab('st')} 
                className={`btn btn-sm btn-ghost justify-start gap-3 normal-case font-medium w-full text-left rounded-xl ${activeTab === 'st' ? 'bg-primary/15 text-primary font-bold border border-primary/20' : 'text-base-content/75'}`}
              >
                <Settings className="w-4 h-4" />
                <span>{t('nav_st', lang) || 'Cấu hình Hệ thống'}</span>
              </button>
              
              <div className="divider opacity-20 my-2"></div>
            </>
          )}

          {/* User Navigation (Available for both admin and users) */}
          <button 
            onClick={() => setActiveTab('myacc')} 
            className={`btn btn-sm btn-ghost justify-start gap-3 normal-case font-medium w-full text-left rounded-xl ${activeTab === 'myacc' ? 'bg-primary/15 text-primary font-bold border border-primary/20' : 'text-base-content/75'}`}
          >
            <User className="w-4 h-4" />
            <span>{t('nav_myacc', lang)}</span>
          </button>
          
          <button 
            onClick={() => setActiveTab('myuse')} 
            className={`btn btn-sm btn-ghost justify-start gap-3 normal-case font-medium w-full text-left rounded-xl ${activeTab === 'myuse' ? 'bg-primary/15 text-primary font-bold border border-primary/20' : 'text-base-content/75'}`}
          >
            <BarChart3 className="w-4 h-4" />
            <span>{t('nav_myuse', lang)}</span>
          </button>
        </aside>

        {/* Content Area */}
        <main className="flex-1 p-8 overflow-y-auto z-10">
          <div className="max-w-7xl mx-auto animate-tab-in">
            {activeTab === 'ov' && <OverviewTab />}
            {activeTab === 'ks' && <KeysTab />}
            {activeTab === 'ac' && <AccountsTab />}
            {activeTab === 'us' && <TokenAnalysisTab />}
            {activeTab === 'ep' && <EndpointsTab />}
            {activeTab === 'pe' && <PenaltiesTab />}
            {activeTab === 'mu' && <PoolStructureTab />}
            {activeTab === 'myacc' && <MyAccountTab />}
            {activeTab === 'myuse' && <MyUsageTab />}
            {activeTab === 'st' && <SettingsTab />}
          </div>
        </main>
      </div>

      {/* 🎯 Easter Eggs List Modal */}
      {showEggsModal && (
        <div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/60 backdrop-blur-sm p-4 animate-fade-in-up">
          <div className="card glass-card max-w-lg w-full p-6 border border-amber-500/30 text-left rounded-3xl shadow-2xl relative">
            <button 
              onClick={() => setShowEggsModal(false)}
              className="btn btn-ghost btn-xs btn-circle absolute top-4 right-4 text-base-content/65"
            >
              ✕
            </button>
            <h2 className="text-lg font-black text-amber-500 mb-2 flex items-center gap-2">
              <span>🎯 Easter Egg Tracker</span>
              <span className="badge badge-amber badge-sm font-bold">{foundEggs.length} / 9 Found</span>
            </h2>
            <p className="text-xs text-base-content/75 mb-6">Discover secrets hidden throughout the Cockpit dashboard to unlock all trophies.</p>
            
            <div className="space-y-3 max-h-96 overflow-y-auto pr-1">
              {eggsList.map((egg) => {
                const isFound = foundEggs.includes(egg.id);
                return (
                  <div key={egg.id} className={`p-3 rounded-xl border flex items-center gap-3 transition-all duration-300 ${
                    isFound 
                      ? 'bg-emerald-500/10 border-emerald-500/25 text-emerald-400 font-semibold' 
                      : 'bg-base-200/40 border-base-content/5 text-base-content/50'
                  }`}>
                    <div className="text-xl">{isFound ? '✅' : '🔒'}</div>
                    <div className="flex-1 min-w-0">
                      <div className={`text-xs font-bold ${isFound ? 'text-emerald-400' : 'text-base-content/60 blur-[3px] select-none'}`}>
                        {egg.name}
                      </div>
                      <div className="text-[10px] text-base-content/60 mt-0.5 leading-normal">
                        {isFound ? 'Already found!' : `Hint: ${egg.hint}`}
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        </div>
      )}

      {/* 🎮 Status Clicker Game */}
      {showClicker && (
        <div className="fixed bottom-20 left-6 z-[90] w-72 glass-card border border-indigo-500/35 p-6 rounded-2xl shadow-2xl animate-fade-in-up">
          <div className="flex justify-between items-center mb-4">
            <span className="font-extrabold text-sm text-indigo-400">🪙 Token Clicker Arcade</span>
            <button 
              onClick={() => setShowClicker(false)} 
              className="btn btn-ghost btn-xs btn-circle text-base-content/60"
            >
              ✕
            </button>
          </div>
          <p className="text-xs text-base-content/70 mb-4">Click the giant token to mine virtual tokens. Reach score 10 to unlock the secret!</p>
          <div className="flex flex-col items-center gap-4">
            <button 
              onClick={(e) => {
                setClickerScore(prev => {
                  const next = prev + 1;
                  if (next === 10) {
                    unlockEgg('clicker');
                  }
                  return next;
                });
                window.dispatchEvent(new CustomEvent('spawn-custom-particles', {
                  detail: { type: 'sparkles', x: e.clientX, y: e.clientY }
                }));
              }}
              className="w-20 h-20 rounded-full bg-gradient-to-tr from-yellow-400 to-amber-600 hover:from-yellow-350 hover:to-amber-550 flex items-center justify-center text-4xl shadow-lg hover:scale-110 active:scale-95 transition-all duration-200 border-2 border-white/20 select-none cursor-pointer"
            >
              🪙
            </button>
            <div className="text-sm font-bold">
              Score: <span className="text-amber-500 text-lg">{clickerScore}</span> / 10
            </div>
            {clickerScore >= 10 && (
              <div className="text-xs text-emerald-400 font-extrabold animate-pulse">🎉 Target Reached! Egg Unlocked!</div>
            )}
          </div>
        </div>
      )}

      {/* 🎉 Easter Egg Unlocked Toast */}
      {toast && (
        <div className="fixed bottom-6 right-6 z-[100] animate-bounce bg-gradient-to-r from-amber-500 to-purple-650 text-white font-black px-6 py-4 rounded-2xl shadow-2xl border border-white/20 flex items-center gap-3 animate-fade-in-up">
          <span className="text-xl">✨</span>
          <div className="text-left">
            <div className="text-[10px] uppercase tracking-widest opacity-80 font-extrabold text-amber-300">Easter Egg Found!</div>
            <div className="text-xs">{toast.message}</div>
          </div>
        </div>
      )}
    </div>
  );
}
