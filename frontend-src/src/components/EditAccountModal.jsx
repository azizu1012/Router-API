import { useState } from 'react';
import { api } from '../utils/api';
import { useApp } from '../context/AppContext';
import { t } from '../utils/i18n';
import Modal from './Modal';
import Button from './Button';
import Alert from './Alert';

const TIER_CAPS = {
  free: { rpm: 30, tpm: 200000, rpd: 1000 },
  premium: { rpm: 120, tpm: 800000, rpd: 5000 },
  admin: { rpm: 300, tpm: 6000000, rpd: 20000 },
};

function LimitField({ label, hint, value, onChange, disabled }) {
  return (
    <label className="block">
      <span className="label-micro block mb-1.5">{label}</span>
      <input
        type="number"
        inputMode="numeric"
        value={value}
        disabled={disabled}
        onChange={e => onChange(e.target.value)}
        className="input input-sm w-full text-xs font-mono tabular-nums"
        autoComplete="off"
      />
      <span className="block text-[0.6875rem] mt-1" style={{ color: 'var(--c-ink-3)' }}>{hint}</span>
    </label>
  );
}

/* The form is a separate component keyed on the account name, so selecting a
   different account remounts it with that account's values instead of syncing
   them through an effect. */
function AccountForm({ account, onSaved, onClose }) {
  const { token, lang } = useApp();
  const [tier, setTier] = useState(account.tier || 'free');
  const [rpm, setRpm] = useState(account.rpm != null ? String(account.rpm) : '');
  const [tpm, setTpm] = useState(account.tpm != null ? String(account.tpm) : '');
  const [rpd, setRpd] = useState(account.rpd != null ? String(account.rpd) : '');
  const [loading, setLoading] = useState(false);
  const [msg, setMsg] = useState({ text: '', type: '' });

  const caps = TIER_CAPS[tier];
  const overCap = caps && (
    (rpm !== '' && Number(rpm) > caps.rpm)
    || (tpm !== '' && Number(tpm) > caps.tpm)
    || (rpd !== '' && Number(rpd) > caps.rpd)
  );

  const handleSave = async (e) => {
    e.preventDefault();
    setLoading(true);
    setMsg({ text: t('ac_saving', lang), type: 'info' });

    const body = { name: account.name, tier };
    if (rpm.trim() !== '') body.rpm = parseInt(rpm, 10);
    if (tpm.trim() !== '') body.tpm = parseInt(tpm, 10);
    if (rpd.trim() !== '') body.rpd = parseInt(rpd, 10);

    try {
      await api('/dashboard/admin/accounts/update', {
        method: 'POST',
        body: JSON.stringify(body)
      }, token);
      setMsg({ text: t('ac_saved', lang), type: 'success' });
      setTimeout(() => { onSaved(); onClose(); }, 700);
    } catch (err) {
      setMsg({ text: `${t('ac_save_err', lang)}${err.message}`, type: 'error' });
    } finally {
      setLoading(false);
    }
  };

  return (
    <form id="account-edit-form" onSubmit={handleSave} className="space-y-3">
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        <label className="block">
          <span className="label-micro block mb-1.5">{t('ac_name_field', lang)}</span>
          <input type="text" value={account.name} disabled readOnly
                 className="input input-sm w-full text-xs font-mono"
                 style={{ color: 'var(--c-ink-3)' }} />
        </label>
        <label className="block">
          <span className="label-micro block mb-1.5">{t('lbl_tier', lang)}</span>
          <select value={tier} onChange={e => setTier(e.target.value)} disabled={loading}
                  className="select select-sm w-full text-xs">
            <option value="free">Free</option>
            <option value="premium">Premium</option>
            <option value="admin">Admin</option>
          </select>
        </label>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
        <LimitField label="RPM" hint={t('ac_edit_hint', lang)} value={rpm} onChange={setRpm} disabled={loading} />
        <LimitField label="TPM" hint={t('ac_edit_hint', lang)} value={tpm} onChange={setTpm} disabled={loading} />
        <LimitField label="RPD" hint={t('ac_edit_hint', lang)} value={rpd} onChange={setRpd} disabled={loading} />
      </div>

      {overCap && <Alert type="warning" message={t('ac_capped_warn', lang).replace('{tier}', tier)} />}
      {msg.text && <Alert type={msg.type} message={msg.text} />}

      <div className="flex items-center justify-end gap-2 pt-3"
           style={{ borderTop: '1px solid var(--c-line)' }}>
        <Button type="button" variant="ghost" onClick={onClose} disabled={loading}>
          {t('btn_cancel', lang)}
        </Button>
        <Button type="submit" loading={loading}>{t('ac_save_changes', lang)}</Button>
      </div>
    </form>
  );
}

/* Changing a tier without touching the numbers would leave the account above
   its new ceiling, so the form warns when a typed value crosses it. The server
   clamps regardless — this only spares a surprise. */
export default function EditAccountModal({ account, isOpen, onClose, onSaveSuccess }) {
  const { lang } = useApp();
  const open = Boolean(isOpen && account);

  return (
    <Modal
      isOpen={open}
      onClose={onClose}
      title={t('ac_edit_title', lang)}
      size="md"
    >
      {open && (
        <AccountForm
          key={account.name}
          account={account}
          onSaved={onSaveSuccess}
          onClose={onClose}
        />
      )}
    </Modal>
  );
}