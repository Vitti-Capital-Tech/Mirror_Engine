'use client';
import React, { useState } from 'react';
import { useCreateAccount, useAccounts, verifyCredentials } from '@/hooks/useAccounts';
import { useAuth } from '@/context/AuthContext';
import { useAccountScope, effectiveScope, ALL_ACCOUNTS } from '@/context/AccountScopeContext';
import { Select } from '@/components/shared/Select';
import { X, Eye, EyeOff, ChevronDown, Crown, CornerDownRight, RefreshCw, ShieldCheck, AlertTriangle } from 'lucide-react';
import toast from 'react-hot-toast';

/**
 * Add an account.
 *
 * The previous version was one long form that asked everything at once —
 * connection details, who owns it, which master, role, copy mode, allocation
 * value, allocated balance, max position, leverage — with conditionals bolted
 * on to hide the parts that did not apply. Adding a copy meant scrolling past
 * nine fields to fill in four, and answering "which role?" after already
 * clicking "Add a copy".
 *
 * Four changes, all of them about asking less and saving less that is wrong:
 *
 *   * WHAT is being added is decided before the form opens, not inside it. The
 *     title says so, and the role picker is gone on that path.
 *   * The fields are grouped by what they are for — connecting the account,
 *     then how much it should copy — instead of arriving in schema order.
 *   * Everything with a sensible default is behind "Advanced", and the
 *     defaults are real prefilled values rather than greyed-out placeholders,
 *     so what will happen is readable without reading the docs.
 *   * The account cannot be saved until its keys have actually reached Delta.
 *     Nothing on the list looks more like a working account than a broken one,
 *     so a mistyped key used to stay invisible until a trade needed it.
 */

const MAX_MASTERS = 5;
const MAX_FOLLOWERS = 2;
const DEFAULT_TP_SL_OFFSET = '50';
// What the backend stores when the form leaves it blank (AccountCreate's own
// default), shown as a real value so the number in force is the number on
// screen rather than a greyed-out hint.
const DEFAULT_LEVERAGE_LIMIT = '10';

const LABEL = 'text-[10px] font-semibold uppercase tracking-wider text-text-muted';
const HINT = 'text-[10.5px] text-text-muted';
const INPUT =
  'w-full bg-bg-primary border border-bg-border rounded-lg px-3 py-2.5 text-[13px] ' +
  'text-text-primary placeholder:text-text-muted/70 outline-none focus:border-blue-500/70 transition-colors';

/** How a copy is sized. One dropdown rather than four stacked cards: it is a
 *  single decision, the recommended answer is right for almost everyone, and
 *  the cards made the least interesting part of the form the tallest. */
const COPY_MODES = [
  { id: 'auto_ratio', title: 'Match balance automatically', hint: 'Sizes each copy by the balance ratio between the two accounts. Right for most setups.', unit: null, recommended: true },
  { id: 'multiplier', title: 'Multiple of the master', hint: 'e.g. 2 copies twice the master’s size, whatever that size is.', unit: '×', recommended: false },
  { id: 'fixed', title: 'Fixed size', hint: 'The same number of contracts every time, ignoring the master’s size.', unit: 'lots', recommended: false },
  { id: 'capital_pct', title: 'Percentage of capital', hint: 'Sizes by a share of this account’s own balance.', unit: '%', recommended: false },
] as const;

export function AddAccountModal({ isOpen, onClose, presetMasterId }: {
  isOpen: boolean;
  onClose: () => void;
  /** Set when "Add a copy" was clicked inside a group, so the form already
   *  knows what it is adding and to which master. */
  presetMasterId?: string;
}) {
  const createAccount = useCreateAccount();
  const { data: accounts = [] } = useAccounts();
  const { user } = useAuth();
  const { scope } = useAccountScope();
  const isAdmin = user?.role === 'admin';

  const cameFromGroup = !!presetMasterId;
  const [isMaster, setIsMaster] = useState(!cameFromGroup);

  // Holders, for an admin adding on someone else's behalf.
  const owners = React.useMemo(() => {
    const seen: Record<string, string> = {};
    for (const a of accounts as any[]) {
      const oid = a.owner_id ? String(a.owner_id) : '';
      if (oid && !(oid in seen)) seen[oid] = a.owner_email || oid;
    }
    return Object.keys(seen).map((id) => ({ id, label: seen[id] }));
  }, [accounts]);

  const scoped = effectiveScope(accounts as any[], scope);
  const scopedOwner = (accounts as any[]).find(
    (a) => scoped !== ALL_ACCOUNTS && (a.is_master ? String(a.id) : String(a.master_id || '')) === scoped)?.owner_id;
  const [ownerId, setOwnerId] = useState<string>(String(scopedOwner || (owners.length === 1 ? owners[0].id : '')));
  const targetOwner = isAdmin ? ownerId : (owners[0]?.id ?? '');

  const ownerMasters = (accounts as any[]).filter(
    (a) => a.is_master && (!targetOwner || String(a.owner_id) === String(targetOwner)));
  const countFor = (mid: string) =>
    (accounts as any[]).filter((a) => !a.is_master && String(a.master_id || '') === String(mid)).length;

  const [masterId, setMasterId] = useState<string>(presetMasterId || '');
  // Only auto-pick the sole master when it can actually take another copy —
  // otherwise the form opens with a full master already chosen and the person
  // finds out only when saving fails.
  const soleMaster = ownerMasters.length === 1 && countFor(ownerMasters[0].id) < MAX_FOLLOWERS
    ? String(ownerMasters[0].id) : '';
  const effMasterId = masterId || soleMaster;
  const chosenMaster = ownerMasters.find((m) => String(m.id) === effMasterId);
  const masterLimitReached = ownerMasters.length >= MAX_MASTERS;
  const followerLimitReached = !!effMasterId && countFor(effMasterId) >= MAX_FOLLOWERS;

  // Connection
  const [name, setName] = useState('');
  const [apiKey, setApiKey] = useState('');
  const [apiSecret, setApiSecret] = useState('');
  const [environment, setEnvironment] = useState<'demo' | 'live'>('live');
  const [showKey, setShowKey] = useState(false);
  const [showSecret, setShowSecret] = useState(false);

  // Verification. Its own step, sitting with the keys it checks rather than on
  // the save button: one button asks Delta whether these keys work, the other
  // saves, and the save one stays out of reach until the first has answered.
  // Dropped the moment any part of the connection changes, so what was checked
  // is always what gets saved.
  const [verifyState, setVerifyState] = useState<'idle' | 'checking' | 'ok' | 'fail'>('idle');
  const [verifyMsg, setVerifyMsg] = useState('');
  const verified = verifyState === 'ok';
  const unverify = () => {
    if (verifyState !== 'idle') { setVerifyState('idle'); setVerifyMsg(''); }
  };

  // Copying
  const [copyMode, setCopyMode] = useState<string>('auto_ratio');
  const [copyValue, setCopyValue] = useState('2');

  // Advanced
  const [advOpen, setAdvOpen] = useState(false);
  const [allocatedBalance, setAllocatedBalance] = useState('');
  const [maxPositionSize, setMaxPositionSize] = useState('');
  const [leverageLimit, setLeverageLimit] = useState(DEFAULT_LEVERAGE_LIMIT);
  const [tpSlOffset, setTpSlOffset] = useState(DEFAULT_TP_SL_OFFSET);

  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  if (!isOpen) return null;

  // When the form was opened from a group it already knows what it is adding,
  // so the title says it. Otherwise the Master/Copy picker below answers that
  // question and the title should not try to answer it twice.
  const title = cameFromGroup && chosenMaster
    ? `Add a copy to “${chosenMaster.name}”`
    : 'Add account';

  const runVerify = async () => {
    setError('');
    setVerifyState('checking');
    try {
      const res = await verifyCredentials({ api_key: apiKey, api_secret: apiSecret, environment });
      const msg = res.message || (res.ok ? 'These keys work.' : 'These keys did not work.');
      setVerifyMsg(msg);
      setVerifyState(res.ok ? 'ok' : 'fail');
      // Deliberately never "connected" — nothing has been connected or saved
      // at this point. All that happened is that Delta answered these keys.
      if (res.ok) toast.success(msg); else toast.error(msg);
    } catch (err: any) {
      const msg = err.message || 'Could not reach Delta to check the keys.';
      setVerifyMsg(msg);
      setVerifyState('fail');
      toast.error(msg);
    }
  };

  const save = async () => {
    if (isAdmin && !ownerId) return setError('Choose which account holder this belongs to.');
    if (!isMaster && !effMasterId) return setError('Choose which master this copy should follow.');
    if (!isMaster && followerLimitReached) return setError(`That master already has ${MAX_FOLLOWERS} copies.`);
    if (isMaster && masterLimitReached) return setError(`You already have ${MAX_MASTERS} master accounts.`);

    const payload: any = { name, api_key: apiKey, api_secret: apiSecret, is_master: isMaster, environment };
    if (isAdmin && ownerId) payload.owner_id = ownerId;
    if (!isMaster) {
      payload.master_id = effMasterId;
      payload.allocation_mode = copyMode;
      payload.allocation_value = copyMode === 'auto_ratio' ? 1.0 : parseFloat(copyValue || '1');
      if (maxPositionSize) payload.max_position_size = parseFloat(maxPositionSize);
    }
    if (isMaster && tpSlOffset) payload.tp_sl_offset = parseFloat(tpSlOffset);
    if (allocatedBalance) payload.allocated_balance = parseFloat(allocatedBalance);
    if (leverageLimit) payload.leverage_limit = parseInt(leverageLimit);

    setSaving(true);
    try {
      await createAccount.mutateAsync(payload);
      toast.success(`“${name}” added.`);
      onClose();
    } catch (err: any) {
      setError(err.message || 'Could not add the account.');
    } finally {
      setSaving(false);
    }
  };

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    // Reached by pressing the greyed-out Add button, or by Enter. Say what is
    // missing rather than silently doing nothing.
    if (!verified) {
      toast.error(
        canVerify
          ? 'Verify the keys first — press Verify keys above.'
          : 'Enter the API key and secret, then verify them.');
      return;
    }
    return save();
  };

  const mode = COPY_MODES.find((m) => m.id === copyMode)!;
  const checking = verifyState === 'checking';
  const canVerify = !!apiKey.trim() && !!apiSecret.trim() && !checking;

  return (
    <div className="fixed inset-0 z-50 flex items-start sm:items-center justify-center bg-black/70 backdrop-blur-sm overflow-y-auto p-4">
      <div className="w-full max-w-[520px] my-auto rounded-2xl border border-bg-border bg-bg-panel shadow-2xl">
        <div className="flex items-start justify-between gap-4 px-5 py-4 border-b border-bg-border">
          <div className="min-w-0">
            <h2 className="text-[15px] font-bold text-text-primary truncate">{title}</h2>
            <p className="text-[11px] text-text-muted mt-0.5">
              {isMaster
                ? 'The account you trade yourself. Copies mirror what it does.'
                : chosenMaster
                  ? `Mirrors ${chosenMaster.name}. ${countFor(effMasterId)} of ${MAX_FOLLOWERS} copies used.`
                  : 'Mirrors a master account you choose.'}
            </p>
          </div>
          <button type="button" onClick={onClose}
            className="p-1 rounded-lg text-text-muted hover:text-text-primary hover:bg-bg-secondary/60 transition-colors shrink-0">
            <X className="w-4 h-4" />
          </button>
        </div>

        <form onSubmit={submit} className="px-5 py-4 space-y-5">
          {/* What is being added — only when it was not already decided. */}
          {!cameFromGroup && (
            <div className="grid grid-cols-2 gap-2">
              {[
                { v: true, icon: Crown, t: 'Master', d: 'You trade it' },
                { v: false, icon: CornerDownRight, t: 'Copy', d: 'Mirrors a master' },
              ].map(({ v, icon: Icon, t, d }) => (
                <button key={t} type="button" onClick={() => setIsMaster(v)}
                  className={`flex items-start gap-2.5 p-3 rounded-xl border text-left transition-colors ${
                    isMaster === v
                      ? 'border-blue-500/60 bg-blue-500/10'
                      : 'border-bg-border hover:border-bg-border/80 bg-bg-primary/40'}`}>
                  <Icon className={`w-4 h-4 mt-0.5 shrink-0 ${isMaster === v ? 'text-blue-400' : 'text-text-muted'}`} />
                  <span className="min-w-0">
                    <span className="block text-[12.5px] font-semibold text-text-primary">{t}</span>
                    <span className="block text-[10.5px] text-text-muted">{d}</span>
                  </span>
                </button>
              ))}
            </div>
          )}

          {isAdmin && owners.length > 0 && (
            <div className="flex flex-col gap-1.5">
              <label className={LABEL}>Account holder</label>
              <Select size="lg" value={ownerId} onChange={setOwnerId}
                placeholder="Select a holder…"
                options={owners.map((o) => ({ value: o.id, label: o.label }))} />
            </div>
          )}

          {!isMaster && !cameFromGroup && (
            <div className="flex flex-col gap-1.5">
              <label className={LABEL}>Copies which master</label>
              <Select size="lg" value={effMasterId} onChange={setMasterId}
                placeholder="Select a master…"
                options={ownerMasters.map((m: any) => {
                  const n = countFor(m.id);
                  const full = n >= MAX_FOLLOWERS;
                  return {
                    value: String(m.id),
                    label: `${m.name} — ${n} of ${MAX_FOLLOWERS} copies used`,
                    // Still listed, so it is clear the master exists and why it
                    // cannot take another copy — but faded and not pickable.
                    disabled: full,
                    badge: full ? 'Full' : undefined,
                  };
                })} />
              {ownerMasters.length === 0 && (
                <span className="text-[10.5px] text-amber-400">
                  No master yet — add one first, a copy has nothing to mirror.
                </span>
              )}
            </div>
          )}

          {/* Connection */}
          <div className="space-y-3">
            <div className="text-[10px] font-bold uppercase tracking-wider text-blue-400">Connection</div>

            <div className="flex flex-col gap-1.5">
              <label className={LABEL}>Account name</label>
              <input required value={name} onChange={(e) => setName(e.target.value)}
                placeholder={isMaster ? 'e.g. BTC scalper' : 'e.g. BTC scalper copy'} className={INPUT} />
            </div>

            {/* Environment comes before the keys because it decides which
                account the keys belong to — and because the check at the end
                of this block has to be the last thing in it. */}
            <div className="flex flex-col gap-1.5">
              <label className={LABEL}>Environment</label>
              <div className="grid grid-cols-2 gap-2">
                {(['live', 'demo'] as const).map((env) => (
                  <button key={env} type="button" onClick={() => { setEnvironment(env); unverify(); }}
                    className={`py-2 rounded-lg border text-[12px] font-semibold transition-colors ${
                      environment === env
                        ? 'border-blue-500/60 bg-blue-500/10 text-text-primary'
                        : 'border-bg-border text-text-muted hover:text-text-secondary'}`}>
                    {env === 'live' ? 'Live' : 'Demo (testnet)'}
                  </button>
                ))}
              </div>
            </div>

            {[
              { label: 'API key', v: apiKey, set: setApiKey, show: showKey, toggle: () => setShowKey((x) => !x) },
              { label: 'API secret', v: apiSecret, set: setApiSecret, show: showSecret, toggle: () => setShowSecret((x) => !x) },
            ].map((f) => (
              <div key={f.label} className="flex flex-col gap-1.5">
                <label className={LABEL}>{f.label}</label>
                <div className="relative">
                  <input required type={f.show ? 'text' : 'password'} value={f.v}
                    onChange={(e) => { f.set(e.target.value); unverify(); }}
                    placeholder={`Delta India ${f.label.toLowerCase()}`}
                    className={`${INPUT} pr-10 font-mono`} />
                  <button type="button" onClick={f.toggle} tabIndex={-1}
                    className="absolute right-2.5 top-1/2 -translate-y-1/2 text-text-muted hover:text-text-secondary">
                    {f.show ? <Eye className="w-3.5 h-3.5" /> : <EyeOff className="w-3.5 h-3.5" />}
                  </button>
                </div>
              </div>
            ))}

            {/* The check belongs with the fields it checks, not on the save
                button. Its result stays here as one quiet line — a filled
                green panel on a form that has saved nothing reads as "this
                account is live", which it is not. */}
            <div className="flex items-center gap-3 flex-wrap">
              <button type="button" onClick={runVerify} disabled={!canVerify || verified}
                className={`inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg border text-[12px] font-semibold transition-colors ${
                  verified
                    ? 'border-emerald-500/30 text-emerald-300/80 cursor-default'
                    : 'border-bg-border text-text-secondary hover:text-text-primary hover:border-blue-500/50 disabled:text-text-muted disabled:border-bg-border disabled:cursor-not-allowed'}`}>
                {checking
                  ? <><RefreshCw className="w-3.5 h-3.5 animate-spin" /> Checking…</>
                  : verified
                    ? <><ShieldCheck className="w-3.5 h-3.5" /> Verified</>
                    : <><ShieldCheck className="w-3.5 h-3.5" /> Verify keys</>}
              </button>
              {verifyState === 'idle' && (
                <span className={HINT}>
                  {canVerify ? 'Check these against Delta before saving.' : 'Enter the key and secret first.'}
                </span>
              )}
              {verifyState === 'ok' && <span className={HINT}>{verifyMsg}</span>}
              {verifyState === 'fail' && (
                <span className="text-[10.5px] text-red-300 flex items-start gap-1.5">
                  <AlertTriangle className="w-3.5 h-3.5 shrink-0 mt-px" />
                  <span>{verifyMsg}</span>
                </span>
              )}
            </div>
          </div>

          {/* How much to copy (copies only) */}
          {!isMaster && (
            <div className="space-y-2">
              <div className="text-[10px] font-bold uppercase tracking-wider text-blue-400">How much to copy</div>
              <Select size="lg" value={copyMode} onChange={setCopyMode}
                options={COPY_MODES.map((m) => ({
                  value: m.id,
                  label: m.recommended ? `${m.title} (Recommended)` : m.title,
                }))} />
              <p className={HINT}>{mode.hint}</p>
              {mode.unit && (
                <div className="flex items-center gap-2 pt-1">
                  <input type="number" step="any" min="0" value={copyValue}
                    onChange={(e) => setCopyValue(e.target.value)} className={`${INPUT} w-28 font-mono`} />
                  <span className="text-[12px] text-text-muted">{mode.unit}</span>
                </div>
              )}
            </div>
          )}

          {/* Advanced */}
          <div className="rounded-xl border border-bg-border overflow-hidden">
            <button type="button" onClick={() => setAdvOpen((o) => !o)}
              className="w-full flex items-center justify-between px-3.5 py-2.5 text-left hover:bg-bg-secondary/40 transition-colors">
              <span className="text-[12px] font-semibold text-text-secondary">Advanced — optional</span>
              <ChevronDown className={`w-4 h-4 text-text-muted transition-transform ${advOpen ? 'rotate-180' : ''}`} />
            </button>
            {advOpen && (
              <div className="px-3.5 pb-3.5 pt-1 space-y-3 border-t border-bg-border">
                <div className="flex flex-col gap-1.5">
                  <label className={LABEL}>Allocated balance (USD)</label>
                  <input type="number" step="any" min="0" value={allocatedBalance}
                    onChange={(e) => setAllocatedBalance(e.target.value)}
                    placeholder="Blank = use the real balance" className={`${INPUT} font-mono`} />
                  <span className={HINT}>
                    Overrides the real balance when working out the copy ratio.
                  </span>
                </div>

                {isMaster && (
                  <div className="flex flex-col gap-1.5">
                    <label className={LABEL}>SL / TP offset</label>
                    <input type="number" step="any" min="0" value={tpSlOffset}
                      onChange={(e) => setTpSlOffset(e.target.value)}
                      className={`${INPUT} font-mono`} />
                    <span className={HINT}>
                      Copies straddle this master’s stop — the 1st sits at +{tpSlOffset || DEFAULT_TP_SL_OFFSET},
                      the 2nd at −{tpSlOffset || DEFAULT_TP_SL_OFFSET}, so they don’t all trigger at the same
                      instant. {DEFAULT_TP_SL_OFFSET} suits most setups; change it only if you mean to.
                    </span>
                  </div>
                )}

                {!isMaster && (
                  <div className="flex flex-col gap-1.5">
                    <label className={LABEL}>Max position size</label>
                    <input type="number" step="any" min="0" value={maxPositionSize}
                      onChange={(e) => setMaxPositionSize(e.target.value)} placeholder="No cap"
                      className={`${INPUT} font-mono`} />
                  </div>
                )}

                <div className="flex flex-col gap-1.5">
                  <label className={LABEL}>Leverage limit</label>
                  <input type="number" min="1" max="200" value={leverageLimit}
                    onChange={(e) => setLeverageLimit(e.target.value)}
                    className={`${INPUT} font-mono`} />
                  <span className={HINT}>
                    Only used here, to estimate the margin a copied trade needs before
                    placing it. It is not sent to Delta and does not change the leverage
                    set on the exchange.
                  </span>
                </div>
              </div>
            )}
          </div>

          {error && (
            <div className="flex items-start gap-2 text-[11.5px] text-red-300 bg-red-500/10 border border-red-500/25 rounded-lg px-3 py-2.5">
              <AlertTriangle className="w-4 h-4 shrink-0 mt-px" />
              <span>{error}</span>
            </div>
          )}

          <div className="space-y-2 pt-1">
            <div className="flex gap-2">
              <button type="button" onClick={onClose}
                className="flex-1 py-2.5 rounded-lg border border-bg-border text-[13px] font-semibold text-text-secondary hover:text-text-primary transition-colors">
                Cancel
              </button>
              {/* Looks unavailable until the keys have answered, but is NOT a
                  disabled element: a disabled button fires no click at all, so
                  pressing it would do nothing and explain nothing. It stays
                  live, refuses the submit, and says why. */}
              <button type="submit" disabled={saving} aria-disabled={!verified}
                title={!verified ? 'Verify the keys first' : undefined}
                className={`flex-1 py-2.5 rounded-lg text-[13px] font-semibold transition-colors inline-flex items-center justify-center gap-2 ${
                  verified
                    ? 'bg-blue-600 hover:bg-blue-500 text-white disabled:opacity-60'
                    : 'bg-bg-primary border border-bg-border text-text-muted hover:text-text-secondary cursor-not-allowed'}`}>
                {saving && <RefreshCw className="w-3.5 h-3.5 animate-spin" />}
                {isMaster ? 'Add master' : 'Add copy'}
              </button>
            </div>
            {!verified && (
              <p className={`${HINT} text-center`}>
                Verify the keys above to enable this.
              </p>
            )}
          </div>
        </form>
      </div>
    </div>
  );
}
