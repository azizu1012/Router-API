import { useEffect, useRef, useState } from 'react';
import { Eye, EyeOff, Copy, Check, AlertTriangle } from 'lucide-react';
import { api } from '../utils/api';
import { useApp } from '../context/AppContext';
import { t } from '../utils/i18n';
import Button from './Button';
import { copyText } from '../utils/clipboard';

/**
 * Reveal a user's password, two presses deep.
 *
 * The first press arms the button and changes its label. Nothing is fetched.
 * The second press calls the API. That separation is the point: an operator
 * sweeping the mouse across a table of accounts would otherwise fire a request
 * per row, and a screenshot taken mid-sweep would capture whatever happened to
 * be under the cursor.
 *
 * Once revealed the value hides itself after a short delay, because a password
 * left on screen outlives the reason it was fetched.
 */

const ARM_WINDOW_MS = 8000;
const AUTO_HIDE_MS = 15000;

export default function PasswordReveal({ token, accountName }) {
  const { lang } = useApp();
  const [phase, setPhase] = useState('idle');   // idle | armed | loading | shown
  const [result, setResult] = useState(null);
  const [copied, setCopied] = useState(false);
  const armTimer = useRef(null);
  const hideTimer = useRef(null);

  useEffect(() => () => {
    clearTimeout(armTimer.current);
    clearTimeout(hideTimer.current);
  }, []);

  // Disarm when the panel unmounts while armed, so no timer fires into a gone
  // component. (The old effect re-ran on every phase change and clobbered the
  // loading label.)
  useEffect(() => () => {
    clearTimeout(armTimer.current);
  }, []);

  const disarm = () => {
    clearTimeout(armTimer.current);
    setPhase('idle');
  };

  const hide = () => {
    clearTimeout(hideTimer.current);
    setResult(null);
    setPhase('idle');
  };

  const press = async () => {
    if (phase === 'shown' || phase === 'loading') {
      hide();
      return;
    }

    if (phase === 'armed') {
      clearTimeout(armTimer.current);
      setPhase('loading');
      try {
        const res = await api(
          `/dashboard/admin/accounts/recovery?name=${encodeURIComponent(accountName)}`,
          {}, token
        );
        setResult(res);
      } catch (e) {
        setResult({ available: false, reason: e.message || t('pw_read_err', lang) });
      }
      setPhase('shown');
      hideTimer.current = setTimeout(hide, AUTO_HIDE_MS);
      return;
    }

    // First press: arm only, no request.
    setPhase('armed');
    armTimer.current = setTimeout(disarm, ARM_WINDOW_MS);
  };

  const copy = async () => {
    if (!result?.password) return;
    const ok = await copyText(result.password);
    setCopied(ok);
    setTimeout(() => setCopied(false), 1600);
  };

  // Copy is not offered on the failure path: there is nothing to copy.
  const isFailure = phase === 'shown' && !result?.available;

  /* The server sends a stable `reason_code` alongside its English `reason`.
     Codes stay language-independent; the English string is only the last resort
     for a code this build does not know yet — otherwise a Vietnamese reason
     would appear on an English dashboard. */
  const failureText = result?.reason_code
    ? (t(`pw_err_${result.reason_code}`, lang) === `pw_err_${result.reason_code}`
      ? result.reason
      : t(`pw_err_${result.reason_code}`, lang))
    : result?.reason;

  return (
    <div className="flex flex-col items-start gap-1.5 min-w-0 w-full">
      <Button
        size="xs"
        variant={phase === 'armed' ? 'danger' : phase === 'shown' ? 'success' : 'ghost'}
        icon={phase === 'shown' ? EyeOff : Eye}
        onClick={press}
        onBlur={phase === 'armed' ? disarm : undefined}
        loading={phase === 'loading'}
        title={phase === 'armed' ? t('pw_arm_title', lang) : t('pw_title', lang)}
        aria-live="polite"
      >
        {
          phase === 'idle' ? t('pw_reveal', lang)
            : phase === 'armed' ? t('pw_armed', lang)
              : phase === 'loading' ? t('pw_loading', lang)
                : t('pw_hide', lang)
        }
      </Button>

      {phase === 'shown' && (
        <div className="flex items-start gap-1 w-full min-w-0">
          {isFailure ? (
            <span className="text-[0.6875rem] flex items-start gap-1.5"
                  style={{ color: 'var(--c-warn)' }}>
              <AlertTriangle className="w-3 h-3 shrink-0 mt-px" />
              {failureText}
            </span>
          ) : (
            <>
              <code className="text-xs font-mono min-w-0 truncate px-2 py-1 rounded select-all"
                    style={{ background: 'var(--c-surface-3)', color: 'var(--c-ink)' }}>
                {result?.password}
              </code>
              <Button size="xs" variant="ghost" onClick={copy}
                      icon={copied ? Check : Copy}
                      aria-label={copied ? t('pw_copied', lang) : t('pw_copy', lang)}
                      className="shrink-0" />
            </>
          )}
        </div>
      )}
      {phase === 'shown' && !isFailure && (
        <span className="text-[0.625rem]" style={{ color: 'var(--c-ink-3)' }}>
          {t('pw_auto_hide', lang).replace('{s}', Math.round(AUTO_HIDE_MS / 1000))}
        </span>
      )}
    </div>
  );
}