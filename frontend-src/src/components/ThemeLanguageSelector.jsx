import React from 'react';
import { useApp } from '../context/AppContext';
import { t } from '../utils/i18n';
import { Globe, Sun, Moon, Flower, Monitor } from 'lucide-react';

export default function ThemeLanguageSelector() {
  const { theme, setTheme, lang, changeLanguage } = useApp();

  const languages = [
    { code: 'vi', label: 'Tiếng Việt' },
    { code: 'en', label: 'English' },
    { code: 'ja', label: '日本語' }
  ];

  const themes = [
    { code: 'auto', label: t('theme_auto', lang), icon: <Monitor className="w-4 h-4" /> },
    { code: 'dark', label: t('theme_dark', lang), icon: <Moon className="w-4 h-4" /> },
    { code: 'light', label: t('theme_light', lang), icon: <Sun className="w-4 h-4" /> },
    { code: 'valentine', label: t('theme_sakura', lang), icon: <Flower className="w-4 h-4 text-pink-500" /> }
  ];

  const currentThemeObj = themes.find(t => t.code === theme) || themes[0];
  const currentLangObj = languages.find(l => l.code === lang) || languages[0];

  return (
    <div className="flex items-center gap-3">
      {/* Language Selector */}
      <div className="dropdown dropdown-end">
        <label tabIndex={0} className="btn btn-ghost btn-sm gap-2 normal-case font-medium">
          <Globe className="w-4 h-4" />
          <span>{currentLangObj.label}</span>
          <svg className="fill-current" xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24">
            <path d="M7.41,8.58L12,13.17L16.59,8.58L18,10L12,16L6,10L7.41,8.58Z"/>
          </svg>
        </label>
        <ul tabIndex={0} className="dropdown-content menu p-2 shadow-2xl bg-base-100 rounded-box w-36 border border-base-200 z-50">
          {languages.map(l => (
            <li key={l.code}>
              <button 
                onClick={() => changeLanguage(l.code)}
                className={`text-sm ${lang === l.code ? 'active font-bold text-primary' : ''}`}
              >
                {l.label}
              </button>
            </li>
          ))}
        </ul>
      </div>

      {/* Theme Selector */}
      <div className="dropdown dropdown-end">
        <label tabIndex={0} className="btn btn-ghost btn-sm gap-2 normal-case font-medium">
          {currentThemeObj.icon}
          <span>{currentThemeObj.label}</span>
          <svg className="fill-current" xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24">
            <path d="M7.41,8.58L12,13.17L16.59,8.58L18,10L12,16L6,10L7.41,8.58Z"/>
          </svg>
        </label>
        <ul tabIndex={0} className="dropdown-content menu p-2 shadow-2xl bg-base-100 rounded-box w-40 border border-base-200 z-50">
          {themes.map(tOption => (
            <li key={tOption.code}>
              <button 
                onClick={() => setTheme(tOption.code)}
                className={`text-sm flex items-center gap-2 ${theme === tOption.code ? 'active font-bold text-primary' : ''}`}
              >
                {tOption.icon}
                <span>{tOption.label}</span>
              </button>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
