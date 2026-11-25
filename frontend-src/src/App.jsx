import { useState, useEffect, lazy, Suspense } from 'react';
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

// Tabs are split by route: the shell paints immediately and only the active
// tab's chunk is fetched. (The previous eager imports shipped all 14 tabs in
// one 1 MB bundle even though nothing else was on screen.)
const OverviewTab = lazy(() => import('./tabs/OverviewTab'));
const KeysTab = lazy(() => import('./tabs/KeysTab'));
const AccountsTab = lazy(() => import('./tabs/AccountsTab'));
const TokenAnalysisTab = lazy(() => import('./tabs/TokenAnalysisTab'));
const EndpointsTab = lazy(() => import('./tabs/EndpointsTab'));
const PenaltiesTab = lazy(() => import('./tabs/PenaltiesTab'));
const PoolStructureTab = lazy(() => import('./tabs/PoolStructureTab'));
const MyAccountTab = lazy(() => import('./tabs/MyAccountTab'));
const MyUsageTab = lazy(() => import('./tabs/MyUsageTab'));
const SettingsTab = lazy(() => import('./tabs/SettingsTab'));
const ApiHelpTab = lazy(() => import('./tabs/ApiHelpTab'));
const ModelsTab = lazy(() => import('./tabs/ModelsTab'));
const ModelAliasesTab = lazy(() => import('./tabs/ModelAliasesTab'));
const ComboModelsTab = lazy(() => import('./tabs/ComboModelsTab'));

/* Skeletons for the moment a tab chunk is in flight. Sized to the page header
   plus the first card so the layout does not jump when the tab lands. */
function TabFallback() {
  return (
    <div className="animate-fade-in-up" aria-busy="true" aria-live="polite">
      <div className="h-6 w-56 rounded-md mb-2" style={{ background: 'var(--c-surface-2)' }} />
      <div className="h-3 w-80 rounded mb-8" style={{ background: 'var(--c-surface-2)' }} />
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {[0, 1, 2, 3].map(i => (
          <div key={i} className="glass-card rounded-xl p-4 h-24" />
        ))}
      </div>
      <div className="glass-card rounded-xl mt-4 h-64" />
    </div>
  );
}

export default function App() {
  const { 
    token, user, activeTab, setActiveTab, lang, login, register, logout, loading,
    foundEggs, unlockEgg, toast, eggShakeId
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
  const [showLogsModal, setShowLogsModal] = useState(false);

  // 1. Konami Code Listener & custom egg-unlocked listener
  useEffect(() => {
    let input = [];
    // Use e.code so the B/A keys are matched even when a Vietnamese input
    // method is active (e.key would be swallowed/transformed there).
    // Arrow keys map to ArrowUp/Down/Left/Right; B/A map to KeyB/KeyA.
    const target = ['ArrowUp', 'ArrowUp', 'ArrowDown', 'ArrowDown', 'ArrowLeft', 'ArrowRight', 'ArrowLeft', 'ArrowRight', 'KeyB', 'KeyA'];
    const mapping = {
      ArrowUp: 'arrowup', ArrowDown: 'arrowdown',
      ArrowLeft: 'arrowleft', ArrowRight: 'arrowright',
      KeyB: 'b', KeyA: 'a',
    };

    const handleKeyDown = (e) => {
      const token = mapping[e.code];
      if (!token) {
        // Wrong key pressed — reset the sequence so it doesn't accumulate noise.
        input = [];
        return;
      }
      input.push(token);
      if (input.length > 10) input.shift();

      if (input.length === 10 && input.join(',') === target.map(t => mapping[t]).join(',')) {
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
        input = [];
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

  const eggsList = [
    { id: 'konami', name: 'Konami Code Disco Party 🕺', hint: 'Cheat code of the retro gaming era: Up, Up, Down, Down, Left, Right...' },
    { id: 'savings', name: 'Saved vs Claude Cash Shower 💸', hint: 'Click the Savings KPI card to shower the screen in cash.' },
    { id: 'sun', name: 'Sun Solar Flare Super-Spin ☀️', hint: 'Click the Golden Sun 5 times in Light theme.' },
    { id: 'moon', name: 'Meteor Storm Summoner ☄️', hint: 'Click the Crescent Moon 5 times in Dark theme.' },
    { id: 'claude', name: "Claude's Crying Disgrace 😢", hint: 'Double-click the Savings card to flip it and make Claude cry.' },
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
          setLoginError(t('err_register_required', lang));
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

        <div className="absolute top-4 right-4 z-20 animate-tab-in">
          <ThemeLanguageSelector />
        </div>

        <div className="relative w-full max-w-sm glass-card rounded-xl p-7 z-10 animate-tab-in">
          <div className="flex flex-col items-center mb-7 text-center">
            <div className="relative flex items-center justify-center w-11 h-11 rounded-lg mb-3.5"
                 style={{ background: 'var(--c-accent-soft)', border: '1px solid var(--c-line-strong)' }}>
              <Lock className="w-5 h-5" style={{ color: 'var(--c-accent)' }} />
            </div>
            <h1 className="text-xl font-semibold" style={{ color: 'var(--c-ink)' }}>
              {isRegisterMode ? t('login_register_title', lang) : t('login_title', lang)}
            </h1>
            <p className="text-xs mt-1" style={{ color: 'var(--c-ink-3)' }}>
              {isRegisterMode ? t('login_register_desc', lang) : t('login_desc', lang)}
            </p>
          </div>

          <form onSubmit={handleLoginSubmit} className="space-y-4">
            {isRegisterMode && (
              <label className="block">
                <span className="label-micro block mb-1.5">{t('login_invite_label', lang)}</span>
                <input
                  type="text"
                  value={inviteCode}
                  onChange={(e) => setInviteCode(e.target.value)}
                  placeholder="0000"
                  className="input w-full text-sm"
                  autoComplete="off"
                  spellCheck="false"
                />
              </label>
            )}

            <label className="block">
              <span className="label-micro block mb-1.5">{t('login_username', lang)}</span>
              <input
                type="text"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                placeholder={t('login_username', lang)}
                className="input w-full text-sm"
                autoComplete="username"
                spellCheck="false"
              />
            </label>

            <label className="block">
              <span className="label-micro block mb-1.5">{t('login_password', lang)}</span>
              <div className="relative flex items-center">
                <input
                  type={showPassword ? 'text' : 'password'}
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  placeholder="••••••••"
                  className="input w-full pr-10 text-sm"
                  autoComplete="current-password"
                />
                <button
                  type="button"
                  onClick={() => setShowPassword(!showPassword)}
                  className="absolute right-2 btn btn-ghost btn-xs btn-square"
                  style={{ color: 'var(--c-ink-3)' }}
                  aria-label={showPassword ? 'Hide password' : 'Show password'}
                >
                  {showPassword ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                </button>
              </div>
            </label>

            {loginError && (
              <div className="flex items-start gap-2 text-xs p-2.5 rounded-md"
                   style={{ background: 'color-mix(in srgb, var(--c-critical) 10%, transparent)',
                            border: '1px solid color-mix(in srgb, var(--c-critical) 25%, transparent)',
                            color: 'var(--c-critical)' }}>
                <AlertTriangle className="w-3.5 h-3.5 shrink-0 mt-px" />
                <span>{loginError}</span>
              </div>
            )}

            <button
              type="submit"
              disabled={isLoggingIn}
              className="btn btn-primary w-full font-semibold"
            >
              {isLoggingIn ? (
                <>
                  <span className="loading loading-spinner loading-xs"></span>
                  {t('btn_authenticating', lang)}
                </>
              ) : (
                isRegisterMode ? t('login_register_btn', lang) : t('login_btn', lang)
              )}
            </button>
          </form>

          <button
            type="button"
            onClick={() => {
              setIsRegisterMode(!isRegisterMode);
              setLoginError('');
            }}
            className="mt-5 w-full text-center text-xs font-medium transition-colors"
            style={{ color: 'var(--c-accent)' }}
          >
            {isRegisterMode ? t('login_switch_to_login', lang) : t('login_switch_to_register', lang)}
          </button>

          <p className="mt-4 pt-4 text-center text-[0.6875rem] leading-relaxed"
             style={{ borderTop: '1px solid var(--c-line)', color: 'var(--c-ink-3)' }}>
            {t('login_footer', lang)}
          </p>
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
        { id: 'md', label: t('nav_md', lang), icon: Layers, adminOnly: true },
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
        { id: 'st', label: t('nav_st', lang), icon: Settings, adminOnly: true },
        { id: 'help', label: t('nav_help', lang), icon: BookOpen, adminOnly: true },
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
      <header className="h-14 flex items-center justify-between px-3 sm:px-6 border-b glass-nav z-30 fixed top-0 w-full">
        {/* Left: Brand + Breadcrumbs */}
        <div className="flex items-center gap-3 min-w-0">
          <div className="flex items-center gap-2.5 min-w-0">
            <div className="relative flex items-center justify-center w-8 h-8 rounded-lg shrink-0"
                 style={{ background: 'var(--c-accent-soft)', border: '1px solid var(--c-line-strong)' }}>
              <Activity className="w-4 h-4" style={{ color: 'var(--c-accent)' }} />
              <span className="w-1.5 h-1.5 rounded-full absolute -top-0.5 -right-0.5 animate-breathing"
                    style={{ background: 'var(--c-good)', boxShadow: '0 0 0 2px var(--c-surface)' }} />
            </div>
            <span className="font-semibold text-sm tracking-tight truncate hidden sm:block" style={{ color: 'var(--c-ink)' }}>
              {t('header_title', lang)}
            </span>
          </div>

          <div className="hidden sm:flex items-center gap-2 text-xs pl-3 ml-1"
               style={{ borderLeft: '1px solid var(--c-line)', color: 'var(--c-ink-3)' }}>
            <ChevronRight className="w-3.5 h-3.5" />
            <span className="font-medium flex items-center gap-1.5" style={{ color: 'var(--c-ink-2)' }}>
              <CurrentIcon className="w-3.5 h-3.5" style={{ color: 'var(--c-accent)' }} />
              {currentTabItem.label}
            </span>
            {loading && <span className="loading loading-ring loading-xs ml-1" style={{ color: 'var(--c-accent)' }} />}
          </div>
        </div>

        {/* Right: Quick Action, Eggs, Theme & Lang */}
        <div className="flex items-center gap-2 sm:gap-2.5 shrink-0">
          <button
            onClick={() => setShowLogsModal(true)}
            className="btn btn-xs h-8 px-2.5 font-medium gap-1.5 transition-colors"
            style={{ background: 'transparent', border: '1px solid var(--c-line)', color: 'var(--c-ink-2)' }}
            title="Open Live Log Stream"
          >
            <Terminal className="w-3.5 h-3.5" style={{ color: 'var(--c-good)' }} />
            <span className="hidden md:inline text-xs">{t('lbl_live_logs', lang)}</span>
          </button>

          <button
            onClick={() => setShowEggsModal(true)}
            className="btn btn-xs h-8 px-2.5 gap-1 text-xs font-medium transition-colors tabular-nums"
            style={{ background: 'transparent', border: '1px solid var(--c-line)', color: 'var(--c-ink-3)' }}
            title="Easter Eggs Tracker"
          >
            <span aria-hidden="true">🎯</span>
            <span className="hidden sm:inline">{t('lbl_eggs', lang)}</span>
            <span>{foundEggs.length}/{eggsList.length}</span>
          </button>

          <ThemeLanguageSelector />
        </div>
      </header>

      {/* Main Body */}
      <div className="flex flex-col lg:flex-row flex-1 pt-14 h-[calc(100vh-3.5rem)] overflow-hidden">
        {/* Modern Framed Sidebar */}
        <aside className="w-full lg:w-60 glass-nav flex flex-row lg:flex-col justify-between p-2 lg:p-2.5 z-20 overflow-x-auto lg:overflow-y-auto h-auto lg:h-full shrink-0 border-b lg:border-b-0 lg:border-r">
          {/* Navigation Groups */}
          <nav className="flex flex-row lg:flex-col gap-0.5 w-full shrink-0" aria-label="Main">
            {navSections.map((section, sIdx) => {
              const visibleItems = section.items.filter(it => !it.adminOnly || isAdmin);
              if (section.adminOnly && !isAdmin) return null;
              if (visibleItems.length === 0) return null;

              return (
                <div key={sIdx} className="flex flex-row lg:flex-col shrink-0">
                  <div className="hidden lg:block label-micro px-2.5 pt-4 pb-1.5 first:pt-1">
                    {section.group}
                  </div>
                  {visibleItems.map(item => {
                    const Icon = item.icon;
                    const isActive = activeTab === item.id;

                    return (
                      <button
                        key={item.id}
                        onClick={() => setActiveTab(item.id)}
                        aria-current={isActive ? 'page' : undefined}
                        className="relative flex items-center gap-2.5 px-2.5 py-2 rounded-lg text-xs transition-colors shrink-0 whitespace-nowrap text-left"
                        style={isActive
                          ? { background: 'var(--c-accent-soft)', color: 'var(--c-accent)', fontWeight: 600 }
                          : { color: 'var(--c-ink-2)', fontWeight: 500 }}
                        onMouseEnter={e => { if (!isActive) e.currentTarget.style.background = 'var(--c-surface-2)'; }}
                        onMouseLeave={e => { if (!isActive) e.currentTarget.style.background = 'transparent'; }}
                      >
                        {isActive && (
                          <span className="hidden lg:block absolute left-0 top-1.5 bottom-1.5 w-0.5 rounded-full"
                                style={{ background: 'var(--c-accent)' }} />
                        )}
                        <Icon className="w-4 h-4 shrink-0" style={{ opacity: isActive ? 1 : 0.7 }} />
                        <span>{item.label}</span>
                      </button>
                    );
                  })}
                </div>
              );
            })}
          </nav>

          {/* Bottom Profile Dock */}
          <div className="hidden lg:flex flex-col gap-2 pt-3 mt-3 shrink-0"
               style={{ borderTop: '1px solid var(--c-line)' }}>
            <div className="flex items-center justify-between p-2 gap-2 rounded-lg"
                 style={{ background: 'var(--c-surface-2)' }}>
              <div className="flex items-center gap-2.5 min-w-0 flex-1">
                <div className="w-8 h-8 rounded-md font-semibold flex items-center justify-center text-xs uppercase shrink-0"
                     style={{ background: 'var(--c-accent-soft)', color: 'var(--c-accent)' }}>
                  {user?.name?.substring(0, 2).toUpperCase() || 'US'}
                </div>
                <div className="flex flex-col min-w-0 text-left">
                  <span className="text-xs font-semibold truncate leading-tight" style={{ color: 'var(--c-ink)' }}>
                    {user?.name || 'Anonymous'}
                  </span>
                  <span className="text-[0.6875rem] uppercase tracking-wider font-medium mt-0.5" style={{ color: 'var(--c-ink-3)' }}>
                    {user?.tier || 'free'}
                  </span>
                </div>
              </div>

              <button
                onClick={logout}
                className="btn btn-ghost btn-xs btn-square shrink-0 transition-colors"
                style={{ color: 'var(--c-ink-3)' }}
                title={t('logout_btn', lang) || 'Logout'}
                aria-label={t('logout_btn', lang) || 'Logout'}
              >
                <LogOut className="w-3.5 h-3.5" />
              </button>
            </div>
          </div>
        </aside>
        {/* Content Area */}
        <main className="flex-1 p-4 lg:p-8 overflow-y-auto z-10 w-full min-w-0 max-w-full relative">
          
          {user?.must_change_password && (
            <div className="mb-5 mx-auto max-w-7xl animate-fade-in-up">
              <div className="flex items-start gap-3 p-3.5 rounded-xl"
                   style={{ background: 'color-mix(in srgb, var(--c-warn) 10%, transparent)',
                            border: '1px solid color-mix(in srgb, var(--c-warn) 28%, transparent)' }}>
                <AlertTriangle className="w-4 h-4 shrink-0 mt-0.5" style={{ color: 'var(--c-warn)' }} />
                <div className="flex-1 min-w-0">
                  <p className="text-xs font-semibold" style={{ color: 'var(--c-ink)' }}>
                    {t('pw_warn_title', lang)}
                  </p>
                  <p className="text-xs mt-1 leading-relaxed" style={{ color: 'var(--c-ink-2)' }}>
                    {t('pw_warn_body', lang)}
                  </p>
                </div>
                <button
                  onClick={() => setActiveTab('myacc')}
                  className="btn btn-xs shrink-0 font-semibold"
                  style={{ background: 'var(--c-warn)', border: 'none', color: '#241a00' }}
                >
                  {t('pw_warn_action', lang)}
                </button>
              </div>
            </div>
          )}

          <div className="max-w-7xl mx-auto animate-tab-in" key={activeTab}>
            <Suspense fallback={<TabFallback />}>
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
            </Suspense>
          </div>
        </main>
      </div>

      {/* 🎯 Easter Eggs List Modal */}
      {showEggsModal && (
        <div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/55 p-4 animate-fade-in-up"
             onClick={() => setShowEggsModal(false)}>
          <div className="glass-card w-full max-w-md p-5 rounded-xl text-left relative"
               role="dialog" aria-modal="true" aria-label="Easter Egg Tracker"
               onClick={e => e.stopPropagation()}>
            <button
              onClick={() => setShowEggsModal(false)}
              className="btn btn-ghost btn-xs btn-square absolute top-3 right-3"
              style={{ color: 'var(--c-ink-3)' }}
              aria-label="Close"
            >
              ✕
            </button>
            <div className="flex items-center gap-2 mb-1 pr-8">
              <span aria-hidden="true">🎯</span>
              <h2 className="text-sm font-semibold" style={{ color: 'var(--c-ink)' }}>Easter Egg Tracker</h2>
              <span className="badge badge-sm font-semibold tabular-nums"
                    style={{ background: 'var(--c-accent-soft)', border: 'none', color: 'var(--c-accent)' }}>
                {foundEggs.length} / {eggsList.length}
              </span>
            </div>
            <p className="text-xs mb-4" style={{ color: 'var(--c-ink-3)' }}>
              Secrets hidden throughout the Cockpit. Find them all.
            </p>

            <div className="space-y-2 max-h-80 overflow-y-auto pr-1">
              {eggsList.map((egg) => {
                const isFound = foundEggs.includes(egg.id);
                return (
                  <div key={egg.id}
                       className={`p-2.5 rounded-lg flex items-center gap-3 ${eggShakeId === egg.id ? 'animate-shake' : ''}`}
                       style={isFound
                         ? { background: 'color-mix(in srgb, var(--c-good) 9%, transparent)',
                             border: '1px solid color-mix(in srgb, var(--c-good) 25%, transparent)' }
                         : { background: 'var(--c-surface-2)', border: '1px solid var(--c-line)' }}>
                    <span className="text-base shrink-0" aria-hidden="true">{isFound ? '✅' : '🔒'}</span>
                    <div className="flex-1 min-w-0">
                      <div className="text-xs font-semibold"
                           style={{ color: isFound ? 'var(--c-good)' : 'var(--c-ink-2)' }}>
                        {egg.name}
                      </div>
                      <div className="text-[0.6875rem] mt-0.5 leading-normal"
                           style={{ color: 'var(--c-ink-3)', filter: isFound ? 'none' : 'blur(2.5px)', userSelect: isFound ? 'text' : 'none' }}>
                        {isFound ? 'Already found' : egg.hint}
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        </div>
      )}

      {/* 🎉 Easter Egg Unlocked Toast */}
      {toast && (
        <div className="fixed bottom-5 right-5 z-[100] px-4 py-3 rounded-xl flex items-center gap-3 animate-fade-in-up"
             style={{ background: 'var(--c-surface-3)', border: '1px solid var(--c-line-strong)', boxShadow: 'var(--c-shadow)' }}>
          <span className="text-lg" aria-hidden="true">✨</span>
          <div className="text-left">
            <div className="label-micro" style={{ color: 'var(--c-accent)' }}>Easter Egg Found</div>
            <div className="text-xs mt-0.5" style={{ color: 'var(--c-ink)' }}>{toast.message}</div>
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
