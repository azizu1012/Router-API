import React, { useEffect, useRef, useState } from 'react';
import { Eye, EyeOff, Copy, Check, AlertTriangle, Loader2 } from 'lucide-react';
import { api } from '../utils/api';

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
  const [phase, setPhase] = useState('idle');   // idle | armed | loading | shown
  const [result, setResult] = useState(null);
  const [copied, setCopied] = useState(false);
  const armTimer = useRef(null);
  const hideTimer = useRef(null);

  useEffect(() => () => {
    clearTimeout(armTimer.current);
    clearTimeout(hideTimer.current);
  }, []);

  // Leaving the row disarms it, so an armed button cannot sit waiting.
  useEffect(() => {
    return () => {
      if (phase === 'armed') setPhase('idle');
    };
  }, [phase]);

  const disarm = () => {
    clearTimeout(armTimer.current);
    setPhase('idle');
  };

  const press = async () => {
    if (phase === 'shown') {
      setPhase('idle');
      setResult(null);
      clearTimeout(hideTimer.current);
      return;
    }

    if (phase === 'armed') {
      setPhase('loading');
      try {
        const res = await api(
          `/dashboard/admin/accounts/recovery?name=${encodeURIComponent(accountName)}`,
          {}, token
        );
        setResult(res);
        setPhase('shown');
        hideTimer.current = setTimeout(() => {
          setPhase('idle');
          setResult(null);
        }, AUTO_HIDE_MS);
      } catch (e) {
        setResult({ available: false, reason: e.message || 'Không đọc được' });
        setPhase('shown');
        hideTimer.current = setTimeout(() => {
          setPhase('idle');
          setResult(null);
        }, AUTO_HIDE_MS);
      }
      return;
    }

    // first press: arm only. no request.
    setPhase('armed');
    armTimer.current = setTimeout(disarm, ARM_WINDOW_MS);
  };

  const copy = async () => {
    if (!result?.password) return;
    try {
      await navigator.clipboard.writeText(result.password);
      setCopied(true);
      setTimeout(() => setCopied(false), 1600);
    } catch {
      setCopied(false);
    }
  };

  const label = {
    idle: 'Xem MK',
    armed: 'Bấm lần 2 để lộ',
    loading: 'Đang đọc...',
    shown: 'Ẩn lại',
  }[phase];

  const btnClass = phase === 'armed'
    ? 'btn-warning'
    : phase === 'shown'
      ? 'btn-success'
      : 'btn-ghost';

  return (
    <div className="flex flex-col items-start gap-1">
      <button
        onClick={press}
        onBlur={phase === 'armed' ? disarm : undefined}
        disabled={phase === 'loading'}
        className={`btn btn-xs ${btnClass} gap-1 font-bold normal-case`}
        title={
          phase === 'armed'
            ? 'Bấm lần nữa để xác nhận — lần đầu chỉ mở khoá, chưa gọi API'
            : 'Xem mật khẩu (cần bấm 2 lần)'
        }
      >
        {phase === 'loading'
          ? <Loader2 className="w-3 h-3 animate-spin" />
          : phase === 'shown' ? <EyeOff className="w-3 h-3" /> : <Eye className="w-3 h-3" />}
        {label}
      </button>

      {phase === 'shown' && (
        <div className="flex items-center gap-1 bg-base-300/70 border border-base-content/15 rounded px-2 py-1">
          {result?.available ? (
            <>
              <code className="text-[11px] font-mono font-bold text-base-content/90">
                {result.password}
              </code>
              <button onClick={copy} className="btn btn-ghost btn-xs btn-square" title="Copy">
                {copied
                  ? <Check className="w-3 h-3 text-success" />
                  : <Copy className="w-3 h-3" />}
              </button>
            </>
          ) : (
            <span className="text-[10px] text-warning font-bold flex items-center gap-1">
              <AlertTriangle className="w-3 h-3 shrink-0" />
              {result?.reason}
            </span>
          )}
        </div>
      )}
    </div>
  );
}
