import React from 'react';
import { useApp } from '../context/AppContext';
import { t } from '../utils/i18n';
import { Globe, Sun, Moon, Flower, Monitor, Type } from 'lucide-react';

export default function ThemeLanguageSelector() {
  const { theme, setTheme, lang, changeLanguage, fontSize, setFontSize } = useApp();

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

  const fontSizes = [
    { code: '90%', label: t('opt_font_small', lang) },
    { code: '100%', label: t('opt_font_medium', lang) },
    { code: '115%', label: t('opt_font_large', lang) },
    { code: '130%', label: t('opt_font_xlarge', lang) }
  ];

  const currentThemeObj = themes.find(t => t.code === theme) || themes[0];
  const currentLangObj = languages.find(l => l.code === lang) || languages[0];
  const currentFontSizeObj = fontSizes.find(f => f.code === fontSize) || fontSizes[1];

  return (
    <div className="flex items-center gap-2 sm:gap-3">
      {/* Language Selector */}
      <div className="dropdown dropdown-end">
        <label tabIndex={0} className="btn btn-ghost btn-sm gap-1.5 sm:gap-2 normal-case font-medium px-2 sm:px-3">
          <Globe className="w-4 h-4" />
          <span className="hidden sm:inline">{currentLangObj.label}</span>
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
        <label tabIndex={0} className="btn btn-ghost btn-sm gap-1.5 sm:gap-2 normal-case font-medium px-2 sm:px-3">
          {currentThemeObj.icon}
          <span className="hidden sm:inline">{currentThemeObj.label}</span>
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

      {/* Font Size Selector */}
      <div className="dropdown dropdown-end">
        <label tabIndex={0} className="btn btn-ghost btn-sm gap-1.5 sm:gap-2 normal-case font-medium px-2 sm:px-3" title={t('lbl_font_size', lang)}>
          <Type className="w-4 h-4" />
          <span className="hidden sm:inline">{currentFontSizeObj.label}</span>
          <svg className="fill-current" xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24">
            <path d="M7.41,8.58L12,13.17L16.59,8.58L18,10L12,16L6,10L7.41,8.58Z"/>
          </svg>
        </label>
        <ul tabIndex={0} className="dropdown-content menu p-2 shadow-2xl bg-base-100 rounded-box w-40 border border-base-200 z-50">
          {fontSizes.map(f => (
            <li key={f.code}>
              <button 
                onClick={() => setFontSize(f.code)}
                className={`text-sm ${fontSize === f.code ? 'active font-bold text-primary' : ''}`}
              >
                {f.label}
              </button>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
