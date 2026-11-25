import { useState, useEffect, useRef } from 'react';
import { useApp } from '../context/AppContext';
import { t } from '../utils/i18n';
import { Globe, Sun, Moon, Flower, Monitor, Type } from 'lucide-react';

/* A small token-styled dropdown. DaisyUI's `.dropdown` relies on focus-within
   to stay open, which closes the menu the moment a item takes focus — this
   keeps its own state so keyboard and mouse behave the same way. */
function Menu({ trigger, label, children, align = 'right' }) {
  const [open, setOpen] = useState(false);
  const ref = useRef(null);

  useEffect(() => {
    if (!open) return;
    const onDoc = e => { if (ref.current && !ref.current.contains(e.target)) setOpen(false); };
    const onKey = e => { if (e.key === 'Escape') setOpen(false); };
    document.addEventListener('mousedown', onDoc);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDoc);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  return (
    <div className="relative" ref={ref}>
      <button
        type="button"
        onClick={() => setOpen(o => !o)}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={label}
        title={label}
        className="btn btn-ghost btn-sm btn-square"
        style={{ color: 'var(--c-ink-2)' }}
      >
        {trigger}
      </button>
      {open && (
        <div role="menu"
             className={`absolute top-full mt-1.5 z-50 p-1 rounded-lg min-w-[9rem] animate-fade-in-up ${align === 'right' ? 'right-0' : 'left-0'}`}
             style={{ background: 'var(--c-surface)', border: '1px solid var(--c-line-strong)', boxShadow: 'var(--c-shadow)' }}
             onClick={() => setOpen(false)}>
          {children}
        </div>
      )}
    </div>
  );
}

function MenuItem({ active, icon, children, onClick }) {
  return (
    <button
      type="button"
      role="menuitemradio"
      aria-checked={active}
      onClick={onClick}
      className="w-full flex items-center gap-2 px-2.5 py-1.5 rounded-md text-xs text-left transition-colors"
      style={active
        ? { background: 'var(--c-accent-soft)', color: 'var(--c-accent)', fontWeight: 600 }
        : { color: 'var(--c-ink-2)' }}
      onMouseEnter={e => { if (!active) e.currentTarget.style.background = 'var(--c-surface-2)'; }}
      onMouseLeave={e => { if (!active) e.currentTarget.style.background = 'transparent'; }}
    >
      {icon}
      <span className="flex-1">{children}</span>
      {active && <span aria-hidden="true" className="text-[0.625rem]">✓</span>}
    </button>
  );
}

export default function ThemeLanguageSelector() {
  const { theme, setTheme, lang, changeLanguage, fontSize, setFontSize } = useApp();

  const languages = [
    { code: 'vi', label: 'Tiếng Việt' },
    { code: 'en', label: 'English' },
    { code: 'ja', label: '日本語' },
  ];

  // `theme` may hold a legacy value written by an older build. AppContext
  // renders anything that is not dark/light as the sakura theme, so resolve
  // the label the same way instead of falling back to "Auto".
  const normaliseTheme = code => (code === 'sakura' ? 'valentine' : code);

  const themes = [
    { code: 'auto', label: t('theme_auto', lang), icon: <Monitor className="w-3.5 h-3.5" /> },
    { code: 'dark', label: t('theme_dark', lang), icon: <Moon className="w-3.5 h-3.5" /> },
    { code: 'light', label: t('theme_light', lang), icon: <Sun className="w-3.5 h-3.5" /> },
    { code: 'valentine', label: t('theme_sakura', lang), icon: <Flower className="w-3.5 h-3.5" style={{ color: 'var(--c-accent)' }} /> },
  ];

  const fontSizes = [
    { code: '90%', label: t('opt_font_small', lang) },
    { code: '100%', label: t('opt_font_medium', lang) },
    { code: '115%', label: t('opt_font_large', lang) },
    { code: '130%', label: t('opt_font_xlarge', lang) },
  ];

  const activeTheme = normaliseTheme(theme);
  const currentThemeObj = themes.find(o => o.code === activeTheme) || themes[0];
  const currentFontSizeObj = fontSizes.find(f => f.code === fontSize) || fontSizes[1];

  return (
    <div className="flex items-center gap-0.5">
      <Menu label={`Language: ${languages.find(l => l.code === lang)?.label || lang}`} trigger={<Globe className="w-4 h-4" />}>
        {languages.map(l => (
          <MenuItem key={l.code} active={lang === l.code} onClick={() => changeLanguage(l.code)}>
            {l.label}
          </MenuItem>
        ))}
      </Menu>

      <Menu label={`Theme: ${currentThemeObj.label}`} trigger={currentThemeObj.icon}>
        {themes.map(o => (
          <MenuItem key={o.code} active={activeTheme === o.code} icon={o.icon} onClick={() => setTheme(o.code)}>
            {o.label}
          </MenuItem>
        ))}
      </Menu>

      <Menu label={`${t('lbl_font_size', lang)}: ${currentFontSizeObj.label}`} trigger={<Type className="w-4 h-4" />}>
        {fontSizes.map(f => (
          <MenuItem key={f.code} active={fontSize === f.code} onClick={() => setFontSize(f.code)}>
            {f.label}
          </MenuItem>
        ))}
      </Menu>
    </div>
  );
}