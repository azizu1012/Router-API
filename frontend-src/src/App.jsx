import React, { useState, useEffect } from 'react';
import { useApp } from './context/AppContext';
import { t } from './utils/i18n';
import CanvasParticles from './components/CanvasParticles';
import ThemeLanguageSelector from './components/ThemeLanguageSelector';
import {
  LayoutDashboard, Key, Users, PieChart, Network,
  AlertTriangle, ShieldCheck, User, BarChart3, LogOut, Lock, Eye, EyeOff, Settings, Terminal, Layers, BookOpen, Sparkles,
  ChevronRight, Cpu, Activity
} from 'lucide-react';
import LogHistoryModal from './components/LogHistoryModal';

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
import ApiHelpTab from './tabs/ApiHelpTab';
import ModelsTab from './tabs/ModelsTab';
import ModelAliasesTab from './tabs/ModelAliasesTab';
import ComboModelsTab from './tabs/ComboModelsTab';

export default function App() {
  const { 
    token, user, activeTab, setActiveTab, lang, login, register, logout, loading,
    foundEggs, unlockEgg, toast
  } = useApp();

  const [devToolsOpen, setDevToolsOpen] = useState(false);
  const [isRegisterMode, setIsRegisterMode] = useState(false);
  const [inviteCode, setInviteCode] = useState('');
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [loginError, setLoginError] = useState('');
  const [isLoggingIn, setIsLoggingIn] = useState(false);

  const [showEggsModal, setShowEggsModal] = useState(false);
  const [statusClicks, setStatusClicks] = useState(0);
  const [showClicker, setShowClicker] = useState(false);
  const [clickerScore, setClickerScore] = useState(0);
  const [sakuraClicks, setSakuraClicks] = useState(0);
  const [showLogsModal, setShowLogsModal] = useState(false);

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

  const handleLoginSubmit = async (e) => {
    e.preventDefault();
    setLoginError('');
    setIsLoggingIn(true);
    
    try {
      if (isRegisterMode) {
        if (!inviteCode.trim() || !username.trim() || !password) {
          setLoginError('Invite code, username, and password are required');
          setIsLoggingIn(false);
          return;
        }
        await register(inviteCode.trim(), username.trim(), password);
      } else {
        if (!username.trim() || !password) {
          setLoginError(t('err_auth_key_required', lang) || 'Username and password are required');
          setIsLoggingIn(false);
          return;
        }
        await login(username.trim(), password);
      }
    } catch (err) {
      setLoginError(err.message || (isRegisterMode ? 'Registration failed' : 'Invalid username or password'));
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
              <h1 className="text-2xl font-black tracking-tight text-base-content text-center">
                {isRegisterMode ? 'Tạo Tài Khoản' : t('login_title', lang)}
              </h1>
              <p className="text-xs text-base-content/60 mt-1 text-center">
                {isRegisterMode ? 'Nhập mã Invite Code để đăng ký.' : t('login_desc', lang)}
              </p>
            </div>

            <form onSubmit={handleLoginSubmit} className="space-y-6">
              <div className="form-control w-full">
                {isRegisterMode && (
                  <>
                    <label className="label">
                      <span className="label-text font-bold text-xs uppercase tracking-wide text-base-content/75">Invite Code</span>
                    </label>
                    <input 
                      type="text"
                      value={inviteCode}
                      onChange={(e) => setInviteCode(e.target.value)}
                      placeholder="4-digit code" 
                      className="input input-bordered w-full text-sm bg-base-200/50 focus:border-indigo-500 focus:outline-none mb-4"
                      autoComplete="off"
                      spellCheck="false"
                    />
                  </>
                )}
                
                <label className="label">
                  <span className="label-text font-bold text-xs uppercase tracking-wide text-base-content/75">Username</span>
                </label>
                <input 
                  type="text"
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                  placeholder="Username" 
                  className="input input-bordered w-full text-sm bg-base-200/50 focus:border-indigo-500 focus:outline-none mb-4"
                  autoComplete="off"
                  spellCheck="false"
                />
                
                <label className="label">
                  <span className="label-text font-bold text-xs uppercase tracking-wide text-base-content/75">Password</span>
                </label>
                <div className="relative flex items-center">
                  <input 
                    type={showPassword ? "text" : "password"}
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    placeholder="Password" 
                    className="input input-bordered w-full pr-12 text-sm bg-base-200/50 focus:border-indigo-500 focus:outline-none"
                    autoComplete="off"
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
                  isRegisterMode ? 'Đăng Ký' : t('login_btn', lang)
                )}
              </button>
            </form>

            <div className="mt-6 text-center">
              <button 
                type="button" 
                onClick={() => {
                  setIsRegisterMode(!isRegisterMode);
                  setLoginError('');
                }}
                className="text-xs text-primary hover:underline font-semibold"
              >
                {isRegisterMode ? 'Đã có tài khoản? Đăng nhập ngay' : 'Bạn chưa có tài khoản? Đăng ký với Invite Code'}
              </button>
            </div>

            <div className="mt-4 text-center text-xs text-base-content/50">
              {t('login_footer', lang)}
            </div>
          </div>
        </div>
      </div>
    );
  }
  const isAdmin = user?.tier === 'admin';

  const navSections = [
    {
      group: 'Operations',
      adminOnly: true,
      items: [
        { id: 'ov', label: t('nav_ov', lang), icon: LayoutDashboard },
        { id: 'ks', label: t('nav_ks', lang), icon: Key },
        { id: 'ac', label: t('nav_ac', lang), icon: Users },
      ]
    },
    {
      group: 'Routing & Models',
      adminOnly: false,
      items: [
        { id: 'md', label: t('nav_md', lang) || 'Model Pools', icon: Layers, adminOnly: true },
        { id: 'ma', label: 'Model Aliases', icon: Sparkles },
        { id: 'cm', label: 'Combo Models', icon: Network },
        { id: 'ep', label: t('nav_ep', lang), icon: Cpu, adminOnly: true },
      ]
    },
    {
      group: 'Observability',
      adminOnly: true,
      items: [
        { id: 'us', label: t('nav_us', lang), icon: PieChart },
        { id: 'pe', label: t('nav_pe', lang), icon: AlertTriangle },
        { id: 'mu', label: t('nav_mu', lang), icon: ShieldCheck },
      ]
    },
    {
      group: 'Workspace & System',
      adminOnly: false,
      items: [
        { id: 'myacc', label: t('nav_myacc', lang), icon: User },
        { id: 'myuse', label: t('nav_myuse', lang), icon: BarChart3 },
        { id: 'st', label: t('nav_st', lang) || 'Cấu hình Hệ thống', icon: Settings, adminOnly: true },
        { id: 'help', label: t('nav_help', lang) || 'API Reference', icon: BookOpen, adminOnly: true },
      ]
    }
  ];

  const currentTabItem = navSections
    .flatMap(s => s.items)
    .find(it => it.id === activeTab) || { label: 'Dashboard', icon: LayoutDashboard };
  const CurrentIcon = currentTabItem.icon;

  // 4. Render Main Dashboard Shell
  return (
    <div className="relative h-screen w-full flex flex-col overflow-hidden">
      <CanvasParticles />
      
      {/* Framework Top Header */}
      <header className="h-14 flex items-center justify-between px-3 sm:px-6 border-b border-base-content/8 glass-nav z-30 fixed top-0 w-full select-none">
        {/* Left: Brand + Breadcrumbs */}
        <div className="flex items-center gap-3">
          <div onClick={handleStatusClick} className="flex items-center gap-2 cursor-pointer group">
            <div className="relative flex items-center justify-center w-8 h-8 rounded-xl bg-primary/10 border border-primary/25 shadow-sm group-hover:scale-105 transition-transform duration-200">
              <Activity className="w-4 h-4 text-primary" />
              <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 absolute -top-0.5 -right-0.5 animate-breathing ring-2 ring-base-100" />
            </div>
            <span className="font-extrabold text-xs sm:text-sm tracking-tight text-base-content group-hover:text-primary transition-colors">
              {t('header_title', lang)}
            </span>
          </div>

          <div className="hidden sm:flex items-center gap-2 text-xs text-base-content/40 pl-2 border-l border-base-content/10">
            <ChevronRight className="w-3.5 h-3.5 opacity-60" />
            <span className="font-semibold text-base-content/80 flex items-center gap-1.5">
              <CurrentIcon className="w-3.5 h-3.5 text-primary" />
              {currentTabItem.label}
            </span>
            {loading && <span className="loading loading-ring loading-xs text-primary ml-1" />}
          </div>
        </div>
        
        {/* Right: Quick Action, Eggs, Theme & Lang */}
        <div className="flex items-center gap-2 sm:gap-3">
          <button
            onClick={() => setShowLogsModal(true)}
            className="btn btn-xs h-7 px-2.5 rounded-lg font-medium gap-1.5 border border-base-content/10 hover:border-emerald-500/30 hover:bg-emerald-500/10 text-base-content/80 hover:text-emerald-400 transition-all active:scale-95"
            title="Open Live Log Stream"
          >
            <Terminal className="w-3.5 h-3.5 text-emerald-400" />
            <span className="hidden md:inline text-[11px]">Live Logs</span>
          </button>

          <button 
            onClick={() => setShowEggsModal(true)}
            className="btn btn-xs h-7 px-2.5 rounded-lg border border-amber-500/20 bg-amber-500/5 hover:bg-amber-500/15 text-amber-500 gap-1 text-[11px] font-bold transition-all active:scale-95"
            title="Easter Eggs Tracker"
          >
            <span>🎯 <span className="hidden sm:inline">Eggs:</span> {foundEggs.length}/9</span>
          </button>
          
          <ThemeLanguageSelector />
        </div>
      </header>

      {/* Main Body */}
      <div className="flex flex-col lg:flex-row flex-1 pt-14 h-[calc(100vh-3.5rem)] overflow-hidden">
        {/* Modern Framed Sidebar */}
        <aside className="w-full lg:w-64 glass-nav flex flex-row lg:flex-col justify-between p-2 lg:p-3 z-20 overflow-x-auto lg:overflow-y-auto h-auto lg:h-full shrink-0 border-b lg:border-b-0 lg:border-r border-base-content/8">
          {/* Navigation Groups */}
          <div className="flex flex-row lg:flex-col gap-1 w-full shrink-0">
            {navSections.map((section, sIdx) => {
              const visibleItems = section.items.filter(it => !it.adminOnly || isAdmin);
              if (section.adminOnly && !isAdmin) return null;
              if (visibleItems.length === 0) return null;

              return (
                <div key={sIdx} className="flex flex-row lg:flex-col shrink-0 gap-0.5">
                  <div className="hidden lg:block text-[10px] font-bold uppercase tracking-wider text-base-content/40 px-3 pt-3 pb-1">
                    {section.group}
                  </div>
                  {visibleItems.map(item => {
                    const Icon = item.icon;
                    const isActive = activeTab === item.id;

                    return (
                      <button
                        key={item.id}
                        onClick={() => setActiveTab(item.id)}
                        className={`relative flex items-center gap-2.5 px-3 py-1.5 lg:py-2 rounded-xl text-xs font-semibold transition-all duration-200 select-none text-left shrink-0 whitespace-nowrap ${
                          isActive
                            ? 'bg-primary/12 text-primary shadow-[inset_0_1px_0_0_rgba(255,255,255,0.08)] font-bold before:hidden lg:before:block before:absolute before:left-0 before:top-2 before:bottom-2 before:w-1 before:bg-primary before:rounded-r-full'
                            : 'text-base-content/70 hover:text-base-content hover:bg-base-content/5 active:scale-[0.98]'
                        }`}
                      >
                        <Icon className={`w-4 h-4 shrink-0 transition-transform duration-200 ${isActive ? 'text-primary scale-105' : 'text-base-content/60'}`} />
                        <span>{item.label}</span>
                      </button>
                    );
                  })}
                </div>
              );
            })}
          </div>

          {/* Bottom Profile Dock (Linear / Vercel style) */}
          <div className="hidden lg:flex flex-col gap-2 pt-3 mt-3 border-t border-base-content/8 shrink-0">
            <div className="flex items-center justify-between p-2 rounded-xl bg-base-content/[0.03] border border-base-content/5">
              <div onClick={handleSakuraClick} className="flex items-center gap-2.5 cursor-pointer min-w-0 flex-1 group">
                <div className="w-8 h-8 rounded-lg bg-primary/15 text-primary font-bold flex items-center justify-center text-xs uppercase border border-primary/25 shadow-sm group-hover:scale-105 transition-transform shrink-0">
                  {user?.name?.substring(0, 2).toUpperCase() || 'US'}
                </div>
                <div className="flex flex-col min-w-0 text-left">
                  <span className="text-xs font-bold text-base-content/90 truncate leading-tight group-hover:text-primary transition-colors">
                    {user?.name || 'Anonymous'}
                  </span>
                  <div className="flex items-center gap-1.5 mt-0.5">
                    <span className={`badge badge-xs text-[9px] font-extrabold uppercase px-1.5 ${
                      user?.tier === 'admin' ? 'badge-primary' : user?.tier === 'premium' ? 'badge-accent' : 'badge-ghost'
                    }`}>
                      {user?.tier || 'free'}
                    </span>
                  </div>
                </div>
              </div>

              <button
                onClick={logout}
                className="btn btn-ghost btn-xs btn-square text-base-content/50 hover:text-error hover:bg-error/10 transition-colors shrink-0"
                title={t('logout_btn', lang) || 'Logout'}
              >
                <LogOut className="w-3.5 h-3.5" />
              </button>
            </div>
          </div>
        </aside>
        {/* Content Area */}
        <main className="flex-1 p-4 lg:p-8 overflow-y-auto z-10 w-full min-w-0 max-w-full relative">
          
          {user?.must_change_password && (
            <div className="mb-6 mx-auto max-w-7xl animate-fade-in-up">
              <div className="alert bg-error/15 border border-error/30 text-error flex items-start gap-4 p-4 rounded-xl shadow-lg">
                <AlertTriangle className="w-5 h-5 shrink-0 mt-0.5" />
                <div className="flex-1">
                  <h3 className="font-bold text-sm">Cảnh báo Bảo mật: Mật khẩu mặc định</h3>
                  <div className="text-xs opacity-90 mt-1">
                    Tài khoản của bạn đang sử dụng mật khẩu mặc định (1234). Vui lòng vào <strong>My Account</strong> và đổi mật khẩu thành một chuỗi dễ nhớ nhưng an toàn hơn.
                  </div>
                </div>
                <button 
                  onClick={() => setActiveTab('myacc')} 
                  className="btn btn-sm btn-error shrink-0"
                >
                  Đổi ngay
                </button>
              </div>
            </div>
          )}

          <div className="max-w-7xl mx-auto animate-tab-in">
            {activeTab === 'ov' && <OverviewTab />}
            {activeTab === 'ks' && <KeysTab />}
            {activeTab === 'ac' && <AccountsTab />}
            {activeTab === 'us' && <TokenAnalysisTab />}
            {activeTab === 'ep' && <EndpointsTab />}
            {activeTab === 'pe' && <PenaltiesTab />}
            {activeTab === 'mu' && <PoolStructureTab />}
            {activeTab === 'md' && <ModelsTab />}
            {activeTab === 'ma' && <ModelAliasesTab />}
            {activeTab === 'cm' && <ComboModelsTab />}
            {activeTab === 'myacc' && <MyAccountTab />}
            {activeTab === 'myuse' && <MyUsageTab />}
            {activeTab === 'st' && <SettingsTab />}
            {activeTab === 'help' && <ApiHelpTab />}
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

      {/* 📟 Log Stream Modal */}
      {showLogsModal && (
        <LogHistoryModal onClose={() => setShowLogsModal(false)} />
      )}
    </div>
  );
}
