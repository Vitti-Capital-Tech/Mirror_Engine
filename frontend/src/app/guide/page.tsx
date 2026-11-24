'use client';
import React, { useEffect, useState } from 'react';
import {
  BookOpen, Users, Scale, Zap, ListOrdered, LogOut, ShieldCheck, RefreshCw,
  Wallet, Bell, LayoutDashboard, PauseCircle, CheckSquare, HelpCircle, Sparkles,
} from 'lucide-react';

/**
 * Trader’s Guide — everything a trader running a master account should know.
 *
 * Written for traders, not engineers: what happens to the followers when you
 * trade, and what to keep in mind. Every number here is read from the engine
 * (risk_engine / copy_engine / telegram_client) — keep it that way when the
 * engine changes. Stops reflect the TEMPORARY "follower always exits after the
 * master" rule (34991c1, 24 Nov); the stopped-out handling is under "Coming
 * soon" until it ships.
 */

const SECTIONS = [
  { id: 'basics', title: 'How copying works', icon: Users },
  { id: 'sizing', title: 'How much a follower trades', icon: Scale },
  { id: 'market', title: 'Market orders', icon: Zap },
  { id: 'limit', title: 'Limit orders', icon: ListOrdered },
  { id: 'exits', title: 'Closing positions', icon: LogOut },
  { id: 'stops', title: 'Stop-loss & take-profit', icon: ShieldCheck },
  { id: 'safety', title: 'The 10-second safety check', icon: RefreshCw },
  { id: 'funds', title: 'Funds & margin', icon: Wallet },
  { id: 'pause', title: 'Pausing & resuming', icon: PauseCircle },
  { id: 'alerts', title: 'Telegram alerts', icon: Bell },
  { id: 'dashboard', title: 'Using the dashboard', icon: LayoutDashboard },
  { id: 'rules', title: 'Do’s and don’ts', icon: CheckSquare },
  { id: 'faq', title: 'Common questions', icon: HelpCircle },
  { id: 'soon', title: 'Coming soon', icon: Sparkles },
];

const B = ({ children }: { children: React.ReactNode }) => (
  <strong className="font-semibold text-text-primary">{children}</strong>
);

function Section({ id, n, children }: { id: string; n: number; children: React.ReactNode }) {
  const s = SECTIONS.find((x) => x.id === id)!;
  const Icon = s.icon;
  return (
    <section id={id} className="scroll-mt-4 rounded-2xl border border-bg-border bg-bg-panel shadow-lg shadow-black/20">
      <div className="flex items-center gap-2.5 px-5 py-3.5 border-b border-bg-border bg-bg-elevated rounded-t-2xl">
        <span className="font-mono text-[11px] text-blue-400/80">{String(n).padStart(2, '0')}</span>
        <Icon className="w-4 h-4 shrink-0 text-blue-400" />
        <h2 className="text-[14.5px] font-bold text-text-primary">{s.title}</h2>
      </div>
      <div className="px-5 py-4 space-y-3 text-[13.5px] leading-relaxed text-text-secondary">{children}</div>
    </section>
  );
}

function Points({ children }: { children: React.ReactNode }) {
  return <ul className="space-y-2.5">{children}</ul>;
}

function P({ children }: { children: React.ReactNode }) {
  return (
    <li className="flex gap-2.5">
      <span className="mt-[8px] h-1.5 w-1.5 rounded-full bg-blue-400/80 shrink-0" />
      <span>{children}</span>
    </li>
  );
}

function Table({ head, rows }: { head?: string[]; rows: React.ReactNode[][] }) {
  return (
    <div className="overflow-x-auto rounded-xl border border-bg-border">
      <table className="w-full text-[13px]">
        {head && (
          <thead>
            <tr className="bg-bg-secondary/60 text-left text-[10.5px] uppercase tracking-wider text-text-muted">
              {head.map((h) => <th key={h} className="px-3.5 py-2 font-semibold">{h}</th>)}
            </tr>
          </thead>
        )}
        <tbody className="divide-y divide-bg-border">
          {rows.map((r, i) => (
            <tr key={i}>
              {r.map((c, j) => (
                <td key={j} className={`px-3.5 py-2.5 align-top ${j === 0 ? 'text-text-primary font-medium min-w-[9rem]' : ''}`}>{c}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Note({ tone = 'info', children }: { tone?: 'info' | 'warn' | 'bad'; children: React.ReactNode }) {
  const cls = tone === 'warn'
    ? 'border-amber-500/25 bg-amber-500/10 text-amber-100/90'
    : tone === 'bad'
      ? 'border-red-500/25 bg-red-500/10 text-red-100/90'
      : 'border-blue-500/25 bg-blue-500/10 text-blue-100/90';
  return <div className={`rounded-xl border px-3.5 py-2.5 ${cls}`}>{children}</div>;
}

function Example({ children }: { children: React.ReactNode }) {
  return (
    <div className="rounded-xl border border-bg-border bg-bg-secondary/50 px-3.5 py-3 font-mono text-[12.5px] leading-6 text-text-primary overflow-x-auto">
      {children}
    </div>
  );
}

/** Right-hand index: stays in view while the guide scrolls, jumps to a
 *  section on click, and highlights the one being read. Desktop only — on a
 *  phone there is no room beside the text, so the contents list at the top
 *  serves instead. */
function StickyIndex() {
  const [active, setActive] = useState<string>(SECTIONS[0].id);

  useEffect(() => {
    const els = SECTIONS.map((s) => document.getElementById(s.id)).filter(Boolean) as HTMLElement[];
    // The section whose top has most recently passed the upper part of the
    // screen is the one being read.
    const obs = new IntersectionObserver(
      (entries) => {
        const visible = entries.filter((e) => e.isIntersecting)
          .sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top);
        if (visible[0]) setActive(visible[0].target.id);
      },
      { rootMargin: '0px 0px -70% 0px', threshold: 0 },
    );
    els.forEach((el) => obs.observe(el));
    return () => obs.disconnect();
  }, []);

  const go = (id: string) => (e: React.MouseEvent) => {
    e.preventDefault();
    document.getElementById(id)?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    history.replaceState(null, '', `#${id}`);
    setActive(id);
  };

  return (
    <aside className="hidden xl:block w-60 shrink-0">
      <div className="sticky top-0 rounded-2xl border border-bg-border bg-bg-panel p-3">
        <div className="px-2 pb-2 text-[10.5px] font-semibold uppercase tracking-wider text-text-muted">On this page</div>
        <nav className="space-y-0.5">
          {SECTIONS.map((s, i) => {
            const on = active === s.id;
            return (
              <a key={s.id} href={`#${s.id}`} onClick={go(s.id)}
                className={`relative flex items-center gap-2 rounded-lg px-2 py-1.5 text-[12.5px] transition-colors ${
                  on ? 'bg-bg-secondary text-text-primary font-semibold' : 'text-text-secondary hover:bg-bg-secondary/60 hover:text-text-primary'
                }`}>
                {on && <span className="absolute left-0 top-1/2 -translate-y-1/2 h-4 w-[3px] rounded-r bg-blue-500" />}
                <span className={`font-mono text-[10px] w-5 ${on ? 'text-blue-400' : 'text-text-muted'}`}>{String(i + 1).padStart(2, '0')}</span>
                <span className="truncate">{s.title}</span>
              </a>
            );
          })}
        </nav>
      </div>
    </aside>
  );
}

export default function GuidePage() {
  let n = 0;
  const next = () => ++n;
  return (
    <div className="flex items-start gap-6">
    <div className="min-w-0 flex-1 max-w-4xl space-y-5">
      {/* Intro + contents */}
      <div className="rounded-2xl border border-bg-border bg-bg-panel px-5 py-4 space-y-3">
        <div className="flex items-center gap-2">
          <BookOpen className="w-4 h-4 text-blue-400" />
          <span className="text-[14.5px] font-bold text-text-primary">Read this before you trade a master account</span>
        </div>
        <p className="text-[13.5px] leading-relaxed text-text-secondary">
          You trade on the <B>master</B> account. Every <B>follower</B> account copies your trades
          automatically, sized to its own balance. This guide explains what happens to the
          followers for every kind of trade you make, what the alerts mean, and what to keep in
          mind so nothing surprises you.
        </p>
        <nav className="grid gap-1 sm:grid-cols-2 pt-1 xl:hidden">
          {SECTIONS.map((s, i) => {
            const Icon = s.icon;
            return (
              <a key={s.id} href={`#${s.id}`}
                className="flex items-center gap-2 rounded-lg px-2.5 py-1.5 text-[12.5px] text-text-secondary hover:bg-bg-secondary hover:text-text-primary transition-colors">
                <span className="font-mono text-[10.5px] text-text-muted w-5">{String(i + 1).padStart(2, '0')}</span>
                <Icon className="w-3.5 h-3.5 text-text-muted" />
                {s.title}
              </a>
            );
          })}
        </nav>
      </div>

      <Section id="basics" n={next()}>
        <Points>
          <P>A <B>group</B> is one master and its followers. You trade only on the master — the followers are traded for you.</P>
          <P>When you buy or sell, each follower places the same trade <B>within about 1–2 seconds</B>, scaled to its size (next section).</P>
          <P>The same goes for resting orders, edits, cancels, exits, stop-losses and take-profits — all are copied.</P>
          <P>Every 10 seconds a <B>safety check</B> compares each follower with you and fixes what went wrong (section 07).</P>
          <P>Problems are sent to your group’s <B>Telegram</B> chat. Normal copies are silent — a message means something needs a look.</P>
        </Points>
      </Section>

      <Section id="sizing" n={next()}>
        <p>Each follower has a sizing mode, set on the Accounts page. Most use <B>Auto ratio</B>.</p>
        <Table head={['Mode', 'Follower trades']} rows={[
          ['Auto ratio', <>Your size × (follower balance ÷ master balance). If an <B>allocated balance</B> is set on the follower, that is used instead of its real balance.</>],
          ['Multiplier', 'Your size × a fixed number (e.g. 0.5 = half your size).'],
          ['Fixed', 'The same number of lots on every trade, whatever you trade.'],
          ['Capital %', 'A set percentage of the follower’s available margin.'],
        ]} />
        <Example>
          Master balance 5,000 · Follower balance 5,500 → ratio 1.1<br />
          You buy 300 lots → follower buys 330 lots<br />
          You sell 3,000 lots → follower sells 3,300 lots
        </Example>
        <Points>
          <P>Sizes are <B>rounded up</B> to whole lots, and a follower always trades <B>at least 1 lot</B> when you open.</P>
          <P>If the follower has a <B>max position size</B>, it never trades more than that.</P>
          <P>The ratio uses <B>total balance</B>, so it barely moves while you trade. Small differences of a few lots are normal.</P>
        </Points>
      </Section>

      <Section id="market" n={next()}>
        <Points>
          <P>When your market order fills, each follower places its share straight away — usually filled in about 1 second.</P>
          <P>If your order fills in <B>several pieces</B>, every piece is copied, not just the first.</P>
          <P>On thin option strikes, the follower may fill a few points away from your price. A big gap sends a <B>High slippage</B> alert (over 2% and at least $5).</P>
        </Points>
      </Section>

      <Section id="limit" n={next()}>
        <Points>
          <P>When you place a limit order, each follower gets the same order <B>at the same price</B>, sized to its share. It rests on the book just like yours.</P>
          <P>If you <B>edit</B> the price or size, the followers’ orders are edited too. If you <B>cancel</B>, theirs are cancelled.</P>
          <P>When your limit <B>fills</B>, the follower’s copy gets <B>5 seconds</B> to fill at the same price. If it hasn’t, the rest is bought or sold at market so the follower isn’t left behind.</P>
          <P>If your limit only <B>partly fills</B>, the follower’s copy keeps waiting at your price — it is not forced yet.</P>
          <P>Several limit orders on one strike (a ladder) are each copied, sized so the follower’s total matches its share of your total.</P>
        </Points>
        <Note tone="warn">Because the follower’s copy reaches the book a moment after yours, it is behind you in the queue. At the same price, yours can fill and the follower’s not — that is what the 5-second rule is for.</Note>
      </Section>

      <Section id="exits" n={next()}>
        <Points>
          <P>When you close or reduce a position, each follower closes <B>its share of what you closed</B>, based on what you still hold.</P>
          <P>A small trim on a big position does not wipe out a small follower — it closes only the matching part.</P>
          <P>If you close everything, the followers close everything.</P>
          <P>A limit exit works like a limit order above: it rests at your price, and is forced at market 5 seconds after yours fills.</P>
        </Points>
        <Example>
          You hold 3,000 · follower holds 3,300 (ratio 1.1)<br />
          You close 1,000 → you have 2,000 left → follower closes down to 2,200
        </Example>
      </Section>

      <Section id="stops" n={next()}>
        <p>
          Your followers get your stop-loss and take-profit, but placed <B>50 points further away</B> than
          yours. So <B>you always exit first</B>, and the followers exit just after you.
        </p>
        <Table head={['You set', 'Follower gets', 'Why']} rows={[
          ['Take-profit 61,850 (above price)', <><B>61,900</B> — 50 higher</>, 'Triggers when price rises — the follower’s is reached after yours'],
          ['Stop-loss 60,000 (below price)', <><B>59,950</B> — 50 lower</>, 'Triggers when price falls — the follower’s is reached after yours'],
        ]} />
        <Points>
          <P>If you <B>move</B> your stop, the followers’ stops move with it and stay 50 behind.</P>
          <P><B>On a stop-loss</B>, the follower loses slightly more than you — its stop is 50 points further.</P>
          <P><B>On a take-profit</B>, price must go 50 points past your target for the follower to exit. If price touches your target and turns back, <B>you exit and the follower may not.</B></P>
        </Points>
        <h3 className="pt-1 text-[13px] font-bold text-text-primary">The two situations you’ll see</h3>
        <Table head={['', 'Your stop hits, follower still in', 'Follower’s stop hits, you still in']} rows={[
          ['Normal?', 'Yes — the usual case now', 'Yes — but should be rare now'],
          ['What happens', 'The follower is left alone. It closes on its own stop if price gets there', 'The follower is out, you are still in'],
          ['What to do', 'Nothing — or close the follower yourself if you want out together', 'Nothing — re-enter the follower yourself only if you want it back in'],
        ]} />
        <Note tone="warn"><B>Careful:</B> if the follower’s stop hit and you then add to or partly close that position, the follower can be bought back in. This is being fixed (section 14).</Note>
      </Section>

      <Section id="safety" n={next()}>
        <p>Every <B>10 seconds</B> the system compares each follower with you, leg by leg. It only acts if it sees the same problem <B>twice in a row</B> (about 20 seconds), so a trade still filling is never “fixed” by mistake.</p>
        <Table head={['It sees', 'It does']} rows={[
          ['Follower missing a position you hold', <>Opens it — <B>only if you entered in the last 3 minutes</B>. Older than that, it is not bought late at a new price; you get a <B>Left out of sync</B> message instead.</>],
          ['Follower bigger than its share', 'Trims it back to its share.'],
          ['Follower smaller than its share', 'Tops it up — unless you are reducing that position yourself.'],
          ['Follower holds something you closed', 'Closes it — unless your stop-loss/take-profit closed yours (then the follower’s own stop is left to do it).'],
          ['Follower missing its stop-loss/take-profit', 'Places it.'],
        ]} />
        <p>It leaves things alone when:</p>
        <Points>
          <P>The difference is tiny — under 5% of the position or under 1 lot.</P>
          <P>You have an order working on that strike that will settle it.</P>
          <P>It can’t read an account properly — it never acts on a bad reading (except to close a position you have exited).</P>
        </Points>
        <p>Every correction it makes is reported as <B>🔧 Reconciler corrected</B>.</p>
      </Section>

      <Section id="funds" n={next()}>
        <Points>
          <P>A follower needs enough margin for its share of your trade. If it doesn’t have it, the exchange <B>rejects the copy</B> and you get <B>⚠️ Mirror Failed</B> with <code className="font-mono text-[12px]">insufficient_margin</code>.</P>
          <P>The system can’t fix that — <B>add funds</B> to the follower, or lower its size (allocated balance or mode).</P>
          <P>Selling options uses a lot of margin. Check followers before big short positions.</P>
          <P>If the master’s balance can’t be read for a moment, its last good balance is used, so follower sizes don’t jump.</P>
        </Points>
      </Section>

      <Section id="pause" n={next()}>
        <Points>
          <P>A <B>paused</B> account is not copied to at all, and is hidden on the Positions page.</P>
          <P>When you <B>resume</B>, the system compares the follower with the master and adjusts it — including positions that were already there from before the pause. It may trim or close them.</P>
        </Points>
        <Note tone="warn"><B>Before resuming,</B> make sure the follower is either flat or already matches the master, so nothing is changed unexpectedly.</Note>
      </Section>

      <Section id="alerts" n={next()}>
        <p>Each group’s alerts go to its own Telegram chat. You get <B>one message per problem</B>, not one every 10 seconds.</p>
        <Table head={['Message', 'Meaning', 'Act?']} rows={[
          ['⚠️ Mismatch', 'A follower differs from you (size, missing position, missing stop, missing order).', 'Usually no — a fix normally follows within ~20s'],
          ['🔧 Reconciler corrected', 'The safety check fixed something, and says what.', 'No'],
          ['⏸ Holding on purpose', 'A difference is being left on purpose (e.g. you are reducing that position).', 'No'],
          ['ℹ️ Left out of sync', 'A missed entry older than 3 minutes — not bought late.', 'Decide: re-enter by hand or leave it'],
          ['⏳ Not corrected', 'Still wrong after 60 seconds — the fix didn’t work.', <B key="a">Yes — look at it</B>],
          ['⚠️ Mirror Failed', 'The exchange rejected a copy (often insufficient margin).', <B key="b">Yes</B>],
          ['🔥 Engine error', 'The system crashed while fixing that position. It will not fix it.', <B key="c">Yes — tell tech</B>],
          ['🛑 Stopped retrying', 'An order was placed 3 times and vanished each time.', <B key="d">Yes</B>],
          ['High slippage / No liquidity', 'A follower filled far from your price, or the book was empty.', 'Check the fill'],
        ]} />
        <Note tone="warn">
          <B>Known false alarm:</B> right after <B>your</B> stop hits, you may get <B>⚠️ Mirror Failed</B> saying the follower
          “still holds it… unmatched and unprotected”. If the follower still has its own stop, nothing is wrong —
          it’s the normal “your stop hits first” situation. This message will be removed (section 14).
        </Note>
      </Section>

      <Section id="dashboard" n={next()}>
        <Table rows={[
          ['Positions', <>Every group: master on top, followers below, with balance and P&amp;L. A yellow <B>“no SYMBOL”</B> chip means a follower doesn’t hold a position you hold. <B>Mismatch</B> on a group means at least one follower differs.</>],
          ['Accounts', 'Add accounts, set sizing mode, allocated balance and max position size, pause or resume, test the connection.'],
          ['Trades Log', 'Every master trade and what each follower did with it.'],
          ['Comparison', 'Your orders and fills side by side with each follower’s, for a chosen day.'],
          ['Alerts', 'Everything sent to Telegram, plus smaller notices. Clear resolved ones from here.'],
        ]} />
        <Note>Times on the dashboard are shown in <B>your device’s time zone</B>. The exchange shows IST. If you compare the two, convert first — or search by order ID.</Note>
      </Section>

      <Section id="rules" n={next()}>
        <div className="grid gap-3 sm:grid-cols-2">
          <div className="rounded-xl border border-emerald-500/20 bg-emerald-500/5 p-4">
            <div className="mb-2 text-[12.5px] font-bold text-emerald-400">Do</div>
            <Points>
              <P>Trade only on the master account.</P>
              <P>Keep followers funded for your biggest position.</P>
              <P>Set a stop-loss/take-profit on the master — followers get theirs automatically.</P>
              <P>Read ⏳ Not corrected, Mirror Failed and Engine error messages straight away.</P>
              <P>Make followers flat or matching before resuming them.</P>
            </Points>
          </div>
          <div className="rounded-xl border border-red-500/20 bg-red-500/5 p-4">
            <div className="mb-2 text-[12.5px] font-bold text-red-400">Don’t</div>
            <Points>
              <P>Don’t trade on a follower account by hand — the system keeps followers matching you, so it can change it back.</P>
              <P>Don’t expect a missed entry to be bought late after 3 minutes.</P>
              <P>Don’t rely on the follower exiting at exactly your price — it exits after you.</P>
              <P>Don’t compare dashboard times with exchange times without converting.</P>
            </Points>
          </div>
        </div>
      </Section>

      <Section id="faq" n={next()}>
        <div className="space-y-3">
          {[
            ['Why does the follower have a slightly different size from my ratio?', 'Sizes are rounded up to whole lots, and the ratio moves a little with balances. Differences under 5% or 1 lot are left alone on purpose.'],
            ['My stop hit, but the follower is still in. Is it broken?', 'No. The follower’s stop is 50 points further, so it exits after you — if price gets there. Close it yourself if you want out at the same time.'],
            ['The follower got stopped out and I didn’t. Is it broken?', 'No — it should be rare now. The follower stays out; re-enter it by hand if you want. Careful about adding to or partly closing that position until the fix is live.'],
            ['A trade wasn’t copied. Will it be copied later?', 'Only if you entered less than 3 minutes ago. After that you get “Left out of sync” and the follower is left out.'],
            ['I got “Mirror Failed – insufficient_margin”.', 'The follower didn’t have the margin. Add funds or reduce its size. The system can’t fix this by itself.'],
            ['My limit filled but the follower’s didn’t.', 'Normal for a moment — the follower is behind you in the queue. After 5 seconds the rest is done at market.'],
          ].map(([q, a]) => (
            <div key={q} className="rounded-xl border border-bg-border bg-bg-secondary/40 px-4 py-3">
              <div className="font-semibold text-text-primary">{q}</div>
              <p className="mt-1">{a}</p>
            </div>
          ))}
        </div>
      </Section>

      <Section id="soon" n={next()}>
        <Points>
          <P>If a follower’s own stop closes a position while you are still in, the follower will <B>stay out</B> until you close yours — it won’t be bought back when you add to or partly close it. (On 23 Nov it was: the follower was stopped out, the master partly closed, and the follower was bought back into one side of the spread.)</P>
          <P>No more false alarms for either stop situation, and the Positions page will show <B>“closed by own stop”</B> instead of a yellow “no SYMBOL”.</P>
          <P>Once that is in, the desk may change where follower stops sit. This guide will be updated when it does.</P>
        </Points>
      </Section>
    </div>
    <StickyIndex />
    </div>
  );
}
