"""PaperClient — a stand-in for DeltaClient that trades nothing.

WHAT IT IS FOR. A paper FOLLOWER mirrors a real master with no money at risk,
so the copy path can be exercised end to end against live master flow. It is a
testing instrument, not a simulator of the exchange's microstructure.

WHY IT LOOKS LIKE THIS. The engine, the reconciler, the position monitor, the
daily report and the alerting all talk to an account through exactly one object:
a DeltaClient. Two places construct one (ConnectionManager.connect_account and
CopyEngine._get_master_client). So a class with the same method surface, chosen
at those two points, makes a paper account indistinguishable from a real one to
everything downstream — no branches anywhere else, and nothing in the live path
to get wrong.

THE RULES IT HOLDS ITSELF TO, in order of importance:

1. NEVER BLOCK. A paper follower sits in the same follower loop as the real
   ones and under the SAME leg lock, so a slow answer here delays a real
   follower's copy on that leg. Every call is local except the mark price, and
   that has a hard timeout and three fallbacks. It is fake; it answers at once
   or it answers approximately, but it never makes anyone wait.

2. NEVER BE A MASTER. A fake master driving real followers would move real
   money off invented trades. Refused here as well as in the API, because one
   of those is a guard and two is a rule.

3. BALANCE IS WHATEVER THE ROW SAYS. Fixed on purpose (desk call): a moving
   paper balance makes the ratio drift exactly as a real account's does, and
   then a copy bug and ratio drift look the same. Edit the number to test a
   different ratio.

WHAT IT SIMULATES, honestly stated:
  * market orders fill in full, at once, at the mark.
  * limit orders rest and fill when the mark reaches them — checked on a 2s
    tick, so a fill is up to 2s late. No partial fills, no queue position, no
    liquidity: a resting limit at a reachable price always fills in full.
  * stop / take-profit orders rest as `pending` and become market orders when
    the mark crosses the trigger.
  * reduce_only is clamped to the position held and can never flip it.
  * fees, funding and liquidation do not exist.

So it answers "did the engine ask for the right thing, at the right moment, for
the right size" — which is the whole question — and not "what would this have
cost". Read a paper account's P&L as fiction.

STATE lives in Supabase (`paper_orders`, `paper_positions`) rather than in
memory, because the backend reloads on every deploy and a paper book that
vanished on reload would be useless for watching a day's copying. The daily
report reads order history over a time range, which is what `paper_orders` is
shaped for.
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Awaitable, Callable, Optional

import httpx

logger = logging.getLogger(__name__)

OnFillCallback = Callable[[dict], Awaitable[None]]
OnPositionCallback = Callable[[dict], Awaitable[None]]

# How often a resting limit / stop is re-checked against the mark.
TICK_SEC = 2.0

# Hard ceiling on the one remote call this class makes. Chosen well under
# leg_locks.LIVE_ACQUIRE_TIMEOUT (30s): a paper follower must never be the
# reason a real follower's copy is late.
MARK_TIMEOUT_SEC = 2.0

# Public, unauthenticated — a paper account has no keys, and does not need any
# to read a price.
_TICKER_URL = "https://api.india.delta.exchange/v2/tickers/{symbol}"

# One shared mark cache across every paper client: the price of a contract does
# not depend on who is asking, and two paper followers on one master would
# otherwise each pay their own round trip inside the same leg lock.
_quotes: dict = {}                   # symbol -> (quote dict, fetched_at)
_QUOTE_TTL = 2.0

# Which ticker field a stop's trigger method is measured against. Must stay in
# step with CopyEngine._TRIGGER_FIELD: the engine picks the follower's trigger
# off one of these, so paper has to evaluate it against the same one.
_TRIGGER_FIELD = {"spot_price": "spot_price", "mark_price": "mark_price",
                  "last_traded_price": "close"}

# Public, unauthenticated, and ONE product at a time (~0.5s) rather than the
# whole catalogue: /v2/products with a large page_size does not return inside
# 25s, which would be a guaranteed timeout in the follower loop.
_PRODUCT_URL = "https://api.india.delta.exchange/v2/products/{product_id}"

# product_id -> symbol, cached for the life of the process and never expired.
# A product id at Delta is immutable: 167088 is C-XAUT-3480-281126 today and
# forever. So the ONLY cost is the first bracket on a contract nobody has
# touched yet, and every later one is a dict lookup with no network at all.
_symbols: dict = {}


PAPER_ENV = "paper"


def is_paper(account: dict | None) -> bool:
    """Is this account a paper account? ONE definition, deliberately.

    The account's `environment` is the single source of truth -- the same field
    that already says demo or live, and the same dropdown the person picks it
    from. A separate boolean would have been a second source of truth for one
    fact, which is how the two disagree later.

    EXACT MATCH, never truthiness, because the two ways of getting this wrong
    are not remotely symmetrical:

      * a PAPER account handed a real DeltaClient -> it has no keys, so every
        call fails loudly and nobody is hurt.
      * a REAL account handed a PaperClient -> its orders are never placed, the
        engine believes they were, and NOTHING SAYS SO. A desk would sit flat
        while its dashboard showed it in the market.

    So only the exact word qualifies. A missing column reads None, "live" and
    "demo" read as themselves, and every one of them leaves a real account on
    the real path. Case and surrounding spaces are forgiven because they come
    from a form, not from a decision.
    """
    env = (account or {}).get("environment")
    return isinstance(env, str) and env.strip().lower() == PAPER_ENV


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _now_us() -> int:
    return int(time.time() * 1_000_000)


async def _quote(symbol: str) -> dict:
    """The contract's public ticker: its own price AND the underlying's.

    TWO DIFFERENT PRICES, AND CONFUSING THEM IS A DISASTER ON OPTIONS. For
    C-BTC-62400-271126 the ticker reports mark_price 38.2 -- the premium, what
    one contract costs -- and spot_price 60501.6, which is BTC. A stop on that
    option triggers off the UNDERLYING, so its trigger is a number like 61284.

    This used to collapse the two into a single float ("mark_price or close or
    spot_price"), which made a stop impossible to evaluate: comparing a premium
    of 38.2 against a trigger of 61284 says "already through it" for every sell
    that ever rests. See _trigger_px and _sweep.

    An empty dict is a real answer; callers must not treat a missing price as a
    price. Cached briefly and shared across paper clients.
    """
    hit = _quotes.get(symbol)
    if hit and (time.time() - hit[1]) < _QUOTE_TTL:
        return hit[0]
    try:
        async with httpx.AsyncClient(timeout=MARK_TIMEOUT_SEC) as c:
            r = await c.get(_TICKER_URL.format(symbol=symbol))
        if r.status_code != 200:
            return hit[0] if hit else {}
        res = (r.json() or {}).get("result") or {}
        q = {}
        for k in ("mark_price", "spot_price", "close"):
            v = res.get(k)
            try:
                q[k] = float(v) if v not in (None, "") else None
            except (TypeError, ValueError):
                q[k] = None
        if q.get("mark_price") or q.get("close"):
            _quotes[symbol] = (q, time.time())
            return q
    except Exception as e:
        logger.debug("paper: ticker read failed for %s: %s", symbol, e)
    # Stale is better than nothing, and nothing is better than a guess.
    return hit[0] if hit else {}


async def _mark(symbol: str) -> Optional[float]:
    """What one contract is worth now -- the price a FILL happens at.

    Deliberately never falls back to spot_price. On an option that is the
    underlying, so a missing premium would have filled a paper order at ~60,000
    instead of ~38 and poisoned every number built on it. None is the honest
    answer, and place_order already refuses to fill without a price.
    """
    q = await _quote(symbol)
    return q.get("mark_price") or q.get("close")


async def _trigger_px(symbol: str, method: Optional[str]) -> Optional[float]:
    """The price a stop with this trigger method is compared against.

    Mirrors CopyEngine._TRIGGER_FIELD, because the follower's trigger was
    computed against that reference and has to be evaluated against the same
    one. None when the reference cannot be read -- and a stop whose reference
    is unreadable must WAIT, never fire: firing on an unknown price is how a
    whole paper book got closed in one sweep.
    """
    q = await _quote(symbol)
    return q.get(_TRIGGER_FIELD.get(method or "mark_price", "mark_price"))


async def _symbol_for(product_id) -> Optional[str]:
    """The contract symbol for a Delta product id, or None.

    WHY THIS EXISTS. The engine attaches protection by product_id -- that is
    what Delta's bracket endpoint takes -- while a paper book is keyed by
    symbol, because that is what a position means here. Something has to
    translate, and it has to be THIS side: the alternative was to make the
    engine pass a symbol it does not otherwise need, which edits the live
    copy path to accommodate a fake account. A real follower's order must
    never route through code that exists for paper's benefit.

    None is a real answer. The caller treats it as a failed placement and says
    so, rather than guessing at a symbol and attaching a stop to the wrong leg.
    """
    key = str(product_id)
    if key in _symbols:
        return _symbols[key]
    try:
        async with httpx.AsyncClient(timeout=MARK_TIMEOUT_SEC) as c:
            r = await c.get(_PRODUCT_URL.format(product_id=key))
        if r.status_code != 200:
            logger.warning("paper: product %s lookup returned %s", key, r.status_code)
            return None
        sym = ((r.json() or {}).get("result") or {}).get("symbol")
        if sym:
            _symbols[key] = sym
            return sym
        logger.warning("paper: product %s has no symbol in the response", key)
    except Exception as e:
        logger.warning("paper: product %s lookup failed: %s", key, e)
    return None


class PaperClient:
    """A DeltaClient-shaped object backed by two Supabase tables."""

    def __init__(self, account: dict, db) -> None:
        if account.get("is_master"):
            # Rule 2. Belt and braces with the API's own refusal.
            raise ValueError(
                f"{account.get('name')} is a paper account and cannot be a master — "
                f"a fake master would drive real followers off invented trades"
            )
        self.account = dict(account)
        self.account_id = str(account["id"])
        self.name = account.get("name") or "paper"
        self.db = db
        self.environment = PAPER_ENV
        self._on_fill: Optional[OnFillCallback] = None
        self._on_position: Optional[OnPositionCallback] = None
        self._tick_task: Optional[asyncio.Task] = None
        self._running = False
        # Paper order ids must not collide with Delta's global order-id sequence,
        # or the ledger and the report would pair a paper order with a real one.
        # Delta's are ~1.7e9 and climbing; these start far above and are only ever
        # compared with each other.
        self._next_id = 9_000_000_000 + (uuid.uuid4().int % 1_000_000) * 1000

    # ------------------------------------------------------------------ util

    def _oid(self) -> int:
        self._next_id += 1
        return self._next_id

    def _orders(self):
        return self.db.table("paper_orders")

    def _positions(self):
        return self.db.table("paper_positions")

    def _balance(self) -> float:
        """Rule 3 — whatever the account row says, re-read so an edit takes
        effect without a restart."""
        try:
            # `balance` only, deliberately. For a real account that is the
            # WALLET and allocated_balance is a separate allocation override the
            # sizing code applies on top; returning the override from get_wallet
            # would make paper mean something different by the same name.
            row = (self.db.table("accounts").select("balance")
                   .eq("id", self.account_id).execute().data or [{}])[0]
            return float(row.get("balance") or 0.0)
        except Exception:
            return float(self.account.get("balance") or 0.0)

    async def _emit_order(self, order: dict) -> None:
        if self._on_fill:
            try:
                await self._on_fill(dict(order))
            except Exception as e:
                logger.warning("paper: on_fill raised for %s: %s", self.name, e)

    async def _emit_position(self, symbol: str, size: float, entry: float) -> None:
        if self._on_position:
            try:
                await self._on_position({
                    "type": "positions",
                    "action": "delete" if size == 0 else "update",
                    "symbol": symbol,
                    "product_symbol": symbol,
                    "size": size,
                    "entry_price": str(entry),
                    "realized_pnl": "0",
                    "margin": "0",
                    "timestamp": _now_us(),
                    "reason": None,
                })
            except Exception as e:
                logger.warning("paper: on_position raised for %s: %s", self.name, e)

    # ------------------------------------------------------------- positions

    def _held(self, symbol: str) -> tuple:
        """(signed size, entry price) for one symbol."""
        try:
            rows = (self._positions().select("*")
                    .eq("account_id", self.account_id)
                    .eq("symbol", symbol).execute().data) or []
        except Exception as e:
            logger.warning("paper: position read failed for %s %s: %s",
                           self.name, symbol, e)
            return 0.0, 0.0
        if not rows:
            return 0.0, 0.0
        r = rows[0]
        return float(r.get("size") or 0.0), float(r.get("entry_price") or 0.0)

    def _apply_fill(self, symbol: str, signed_qty: float, price: float) -> tuple:
        """Move the position by signed_qty and return the new (size, entry).

        Weighted-average entry while adding; entry untouched while reducing;
        a flip takes the new price. Same arithmetic a venue uses, and the only
        arithmetic the sizing code reads back."""
        held, entry = self._held(symbol)
        new = held + signed_qty
        opening = held == 0
        opposite = (held > 0) != (signed_qty > 0)
        if opening or (opposite and abs(signed_qty) > abs(held)):
            new_entry = price                       # opened, or flipped through zero
        elif not opposite:
            total = abs(held) + abs(signed_qty)
            new_entry = ((abs(held) * entry + abs(signed_qty) * price) / total) if total else price
        else:
            new_entry = entry                       # reducing: entry does not move
        if abs(new) < 1e-9:
            new, new_entry = 0.0, 0.0
        try:
            self._positions().upsert({
                "account_id": self.account_id,
                "symbol": symbol,
                "size": new,
                "entry_price": new_entry,
                "updated_at": _now_iso(),
            }, on_conflict="account_id,symbol").execute()
        except Exception as e:
            logger.error("paper: could not write position for %s %s: %s",
                         self.name, symbol, e)
        return new, new_entry

    async def get_positions(self, strict: bool = False) -> list:
        try:
            rows = (self._positions().select("*")
                    .eq("account_id", self.account_id).execute().data) or []
        except Exception as e:
            logger.warning("paper: positions read failed for %s: %s", self.name, e)
            if strict:
                raise
            return []
        return [{
            "product_symbol": r.get("symbol"),
            "symbol": r.get("symbol"),
            "size": float(r.get("size") or 0.0),
            "entry_price": str(r.get("entry_price") or 0),
            "realized_pnl": "0",
            "margin": "0",
        } for r in rows if float(r.get("size") or 0.0) != 0.0]

    # ---------------------------------------------------------------- orders

    def _row(self, o: dict) -> dict:
        """A stored paper order in the shape Delta returns one."""
        return {
            "id": int(o["order_id"]),
            "product_symbol": o.get("symbol"),
            "product_id": 0,
            "side": o.get("side"),
            "size": float(o.get("size") or 0),
            "unfilled_size": float(o.get("unfilled_size") or 0),
            "state": o.get("state"),
            "reason": o.get("reason"),
            "order_type": o.get("order_type"),
            "limit_price": o.get("limit_price"),
            "stop_price": o.get("stop_price"),
            "stop_order_type": o.get("stop_order_type"),
            "stop_trigger_method": o.get("stop_trigger_method"),
            "reduce_only": bool(o.get("reduce_only")),
            "average_fill_price": o.get("average_fill_price"),
            "client_order_id": o.get("client_order_id"),
            "cancellation_reason": o.get("cancellation_reason"),
            "created_at": o.get("created_at"),
            "updated_at": o.get("updated_at"),
        }

    def _fetch(self, order_id) -> Optional[dict]:
        try:
            rows = (self._orders().select("*")
                    .eq("account_id", self.account_id)
                    .eq("order_id", str(order_id)).execute().data) or []
            return rows[0] if rows else None
        except Exception as e:
            logger.warning("paper: order read failed for %s: %s", order_id, e)
            return None

    def _save(self, o: dict) -> None:
        try:
            self._orders().upsert(o, on_conflict="account_id,order_id").execute()
        except Exception as e:
            logger.error("paper: could not write order %s for %s: %s",
                         o.get("order_id"), self.name, e)

    async def _fill(self, o: dict, price: float) -> dict:
        """Fill the whole of a resting or market order at `price`."""
        qty = float(o.get("unfilled_size") or o.get("size") or 0)
        if qty <= 0:
            return o
        signed = qty if o.get("side") == "buy" else -qty
        o = dict(o, unfilled_size=0.0, state="closed", reason="fill",
                 average_fill_price=str(price), updated_at=_now_iso())
        self._save(o)
        size, entry = self._apply_fill(o["symbol"], signed, price)
        logger.info("paper: %s %s %s %g on %s filled at %g (now %+g)",
                    self.name, o.get("order_type"), o.get("side"), qty,
                    o.get("symbol"), price, size)
        await self._emit_order(self._row(o))
        await self._emit_position(o["symbol"], size, entry)
        return o

    async def place_order(
        self,
        symbol: str,
        side: str,
        size: int,
        order_type: str = "market_order",
        limit_price: Optional[float] = None,
        reduce_only: bool = False,
        stop_price: Optional[float] = None,
        stop_order_type: Optional[str] = None,
        stop_trigger_method: Optional[str] = None,
        time_in_force: Optional[str] = None,
        client_order_id: Optional[str] = None,
    ) -> dict:
        qty = abs(float(size or 0))
        held, _ = self._held(symbol)
        if reduce_only:
            # Clamp, never flip — the same contract Delta's reduce_only gives.
            room = abs(held) if ((held > 0) == (side == "sell")) else 0.0
            qty = min(qty, room)
        oid = self._oid()
        o = {
            "account_id": self.account_id,
            "order_id": str(oid),
            "symbol": symbol,
            "side": side,
            "size": qty,
            "unfilled_size": qty,
            "order_type": order_type,
            "limit_price": str(limit_price) if limit_price is not None else None,
            "stop_price": str(stop_price) if stop_price is not None else None,
            "stop_order_type": stop_order_type,
            "stop_trigger_method": stop_trigger_method,
            "reduce_only": bool(reduce_only),
            "client_order_id": (str(client_order_id)[:32] if client_order_id else None),
            "state": "open",
            "reason": None,
            "average_fill_price": None,
            "cancellation_reason": None,
            "created_at": _now_iso(),
            "updated_at": _now_iso(),
        }
        if qty <= 0:
            # A reduce_only with nothing to reduce. Recorded and reported as a
            # no-op rather than raised: the caller asked for something harmless
            # and an exception here would read as a copy failure.
            o.update(state="cancelled", reason="noop",
                     cancellation_reason="reduce_only: nothing to reduce")
            self._save(o)
            return {"result": self._row(o), **self._row(o)}

        if stop_price is not None:
            o["state"] = "pending"                  # waits for its trigger
            self._save(o)
            return {"result": self._row(o), **self._row(o)}

        px = await _mark(symbol)
        if order_type == "market_order":
            if px is None:
                px = float(limit_price) if limit_price else None
            if px is None:
                # No price anywhere. Refuse rather than invent one — a fill at a
                # made-up price would corrupt every number downstream.
                o.update(state="cancelled", reason="no_price",
                         cancellation_reason="paper: no mark price available")
                self._save(o)
                logger.warning("paper: %s could not price %s — order not filled",
                               self.name, symbol)
                return {"result": self._row(o), **self._row(o)}
            self._save(o)
            o = await self._fill(o, px)
            return {"result": self._row(o), **self._row(o)}

        # A limit order rests, and the tick fills it when the mark reaches it.
        self._save(o)
        if px is not None and limit_price is not None and self._crossed(side, px, float(limit_price)):
            o = await self._fill(o, float(limit_price))
        return {"result": self._row(o), **self._row(o)}

    @staticmethod
    def _crossed(side: str, mark: float, level: float) -> bool:
        """Has the mark reached a buy-below / sell-above level?"""
        return mark <= level if side == "buy" else mark >= level

    async def cancel_order(self, order_id: str, product_id: Optional[int] = None) -> dict:
        o = self._fetch(order_id)
        if not o:
            raise RuntimeError(f"paper: no such order {order_id}")
        if o.get("state") in ("closed", "cancelled"):
            return {"result": self._row(o), **self._row(o)}
        o = dict(o, state="cancelled", reason="cancel",
                 cancellation_reason="cancelled_by_user", updated_at=_now_iso())
        self._save(o)
        await self._emit_order(self._row(o))
        return {"result": self._row(o), **self._row(o)}

    async def edit_order(self, order_id: str, product_id: Optional[int] = None,
                         stop_price: Optional[float] = None,
                         limit_price: Optional[float] = None,
                         stop_trigger_method: Optional[str] = None,
                         size: Optional[int] = None) -> dict:
        """Edit a resting order's price, trigger and/or size. Delta's signature.

        `stop_price` AND `stop_trigger_method` USED TO FALL INTO **_kw and be
        thrown away. That is how the engine moves a follower's stop when the
        master moves his, so a master who dragged his SL had the edit accepted,
        logged as "Updated bracket ... @ <new price>", and applied to nothing.
        The paper book kept the old trigger while the log said otherwise --
        worse than the outright crash next door in place_bracket, because
        nothing anywhere said it had not worked.

        No **kwargs here any more, deliberately. An argument this client cannot
        honour should fail at the call, where it is obvious, instead of being
        swallowed into a silent no-op.
        """
        o = self._fetch(order_id)
        if not o:
            raise RuntimeError(f"paper: no such order {order_id}")
        if o.get("state") not in ("open", "pending"):
            raise RuntimeError(f"paper: order {order_id} is {o.get('state')}, not editable")
        upd = dict(o, updated_at=_now_iso())
        if limit_price is not None:
            upd["limit_price"] = str(limit_price)
        if stop_price is not None:
            upd["stop_price"] = str(stop_price)
        if stop_trigger_method is not None:
            upd["stop_trigger_method"] = stop_trigger_method
        if size is not None:
            # Match the venue: resizing resets what is left to fill.
            upd["size"] = abs(float(size))
            upd["unfilled_size"] = abs(float(size))
        self._save(upd)
        await self._emit_order(self._row(upd))
        return {"result": self._row(upd), **self._row(upd)}

    async def place_bracket(
        self,
        product_id: int,
        stop_loss: Optional[dict] = None,
        take_profit: Optional[dict] = None,
        trigger_method: str = "mark_price",
    ) -> dict:
        """Attach SL/TP to the held position, as pending reduce-only stops.

        THIS SIGNATURE IS NOT A CHOICE. It is DeltaClient.place_bracket's, down
        to the parameter names, because the engine calls both through the same
        line and names every argument. The first version of this method invented
        its own shape -- `symbol`, `stop_loss_price`, `take_profit_price` -- and
        the engine's call did not match it, so every paper bracket died on

            place_bracket() missing 1 required positional argument: 'symbol'

        for a whole session. Mini 1 and Mini 2 carried open positions with no
        protection at all while the log said a stop had been requested. The
        failure was caught by the per-follower guard, which is why the real
        followers beside them were untouched -- but a paper account that silently
        has no stops is worthless as a test instrument, because the one thing it
        is there to prove is that the engine asked for the right protection.

        A leg is Delta's dict: {"order_type", "stop_price", optional
        "limit_price"}. The return is Delta's shape too -- a dict KEYED BY LEG
        TYPE, not a list -- because the caller reads
        result["stop_loss_order"]["id"] to map the follower's order back to the
        master's. A list there would have left the id unrecorded even once the
        signature was right, and a later cancel or edit from the master would
        not have found this order.
        """
        symbol = await _symbol_for(product_id)
        if not symbol:
            # Loud, not silent. The engine logs this as a failed placement and
            # alerts; a quiet success would claim protection that is not there.
            raise RuntimeError(
                f"paper: cannot resolve product {product_id} to a symbol — bracket not placed")
        held, _ = self._held(symbol)
        qty = abs(float(held))
        if qty <= 0:
            return {"result": {}, "success": True}
        # Protection closes the position, so it sits on the opposite side.
        side = "sell" if held > 0 else "buy"
        out: dict = {}
        for leg, kind in ((stop_loss, "stop_loss_order"),
                          (take_profit, "take_profit_order")):
            if not leg:
                continue
            px = leg.get("stop_price")
            if px is None:
                continue
            lim = leg.get("limit_price")
            r = await self.place_order(
                symbol=symbol, side=side, size=int(qty),
                order_type=leg.get("order_type") or "market_order",
                limit_price=float(lim) if lim is not None else None,
                reduce_only=True, stop_price=float(px), stop_order_type=kind,
                stop_trigger_method=trigger_method)
            out[kind] = r.get("result") or r
        return {"result": out, "success": True}

    async def edit_bracket_order(self, order_id: str,
                                 sl_price: Optional[float] = None,
                                 tp_price: Optional[float] = None) -> dict:
        """Move an existing bracket leg's trigger.

        Delta's signature. Nothing calls this on either client today, but a
        stub that took `*a, **k` and returned success was a lie waiting for the
        first caller: it would have reported a moved stop that never moved.
        """
        return await self.edit_order(
            order_id, stop_price=(sl_price if sl_price is not None else tp_price))

    async def get_order(self, order_id: str) -> dict:
        o = self._fetch(order_id)
        if not o:
            raise RuntimeError(f"paper: no such order {order_id}")
        return {"result": self._row(o), **self._row(o)}

    async def get_open_orders(self, state: str = "open") -> list:
        try:
            rows = (self._orders().select("*")
                    .eq("account_id", self.account_id)
                    .eq("state", state).execute().data) or []
        except Exception as e:
            logger.warning("paper: open-order read failed for %s: %s", self.name, e)
            return []
        return [self._row(r) for r in rows]

    def _history(self, limit: int = 50) -> list:
        try:
            rows = (self._orders().select("*")
                    .eq("account_id", self.account_id)
                    .order("created_at", desc=True)
                    .limit(limit).execute().data) or []
        except Exception as e:
            logger.warning("paper: history read failed for %s: %s", self.name, e)
            return []
        return [self._row(r) for r in rows]

    async def get_order_history(self, page_size: int = 50) -> list:
        return self._history(page_size)

    async def get_order_history_range(self, start_time_us: int, end_time_us: int,
                                      page_size: int = 200,
                                      max_pages: int = 25) -> list:
        """Every order in a window. Delta's signature, including `max_pages`.

        `max_pages` USED TO FALL INTO **_kw, and the limit here was `page_size`
        alone -- one page, 200 orders, where the caller had asked for up to 25
        pages of them. The daily report reads exactly this range, so a busy
        paper day would have been silently truncated and every order past the
        200th read as never placed. There is no paging to do against a table,
        so the ceiling the caller asked for is simply applied in one go.
        """
        lo = datetime.fromtimestamp(start_time_us / 1_000_000, timezone.utc).isoformat()
        hi = datetime.fromtimestamp(end_time_us / 1_000_000, timezone.utc).isoformat()
        cap = max(1, int(page_size)) * max(1, int(max_pages))
        try:
            rows = (self._orders().select("*")
                    .eq("account_id", self.account_id)
                    .gte("created_at", lo).lte("created_at", hi)
                    .order("created_at").limit(cap).execute().data) or []
        except Exception as e:
            logger.warning("paper: history range read failed for %s: %s", self.name, e)
            return []
        return [self._row(r) for r in rows]

    async def get_fills(self, page_size: int = 50) -> list:
        return [o for o in self._history(page_size) if o.get("reason") == "fill"]

    async def get_fills_range(self, start_time_us: int, end_time_us: int,
                              page_size: int = 200, max_pages: int = 25) -> list:
        rows = await self.get_order_history_range(
            start_time_us, end_time_us, page_size, max_pages)
        return [o for o in rows if o.get("reason") == "fill"]

    # ----------------------------------------------------------------- money

    async def get_wallet(self) -> dict:
        bal = self._balance()
        return {"result": [{
            "asset_symbol": "USD",
            "balance": str(bal),
            "available_balance": str(bal),
        }]}

    async def get_wallet_transactions(self, start_time_us: Optional[int] = None,
                                      page_size: int = 200) -> list:
        return []

    async def get_ticker(self, symbol: str) -> dict:
        """The public ticker, UNWRAPPED — DeltaClient returns
        `data.get("result", data)`, and the engine reads the fields straight
        off what it gets back.

        This used to return {"result": {...}}, so CopyEngine._trigger_reference
        asked the dict for "spot_price", got None, and placed the follower's
        stop AT the master's own price instead of a ladder rung beyond it. The
        log said so every time -- "could not read the current price ... placed
        AT the master's" -- and the deployed stops showed it: Mini 1 @ 61284.0
        against master 61284.0, no offset at all.

        It also reported the option's premium as its own `spot_price`, which is
        the underlying. Both fields are now whatever the exchange says they are.
        """
        q = await _quote(symbol)
        return {"symbol": symbol,
                "mark_price": q.get("mark_price"),
                "spot_price": q.get("spot_price"),
                "close": q.get("close")}

    # -------------------------------------------------------- "websocket"

    async def connect_websocket(self, on_fill_callback: OnFillCallback,
                                on_position_callback: OnPositionCallback) -> None:
        """No socket. Register the callbacks and start the fill tick.

        The engine is event driven — it waits for a fill callback — so a paper
        account that only answered REST reads would leave every copy hanging.
        This is the piece that makes it behave like an account rather than a
        record."""
        self._on_fill = on_fill_callback
        self._on_position = on_position_callback
        if self._tick_task is None or self._tick_task.done():
            self._running = True
            self._tick_task = asyncio.create_task(self._tick_loop())
        logger.info("paper: %s is live (no socket; %.0fs fill tick)", self.name, TICK_SEC)

    async def _tick_loop(self) -> None:
        while self._running:
            try:
                await asyncio.sleep(TICK_SEC)
                await self._sweep()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                # A paper tick must never take the process down.
                logger.warning("paper: tick failed for %s: %s", self.name, e)

    async def _sweep(self) -> None:
        """Fill any resting limit the mark has reached, and trigger any stop."""
        resting = []
        for st in ("open", "pending"):
            resting.extend(await self.get_open_orders(st))
        for o in resting:
            row = self._fetch(o["id"])
            if not row or row.get("state") not in ("open", "pending"):
                continue
            px = await _mark(row["symbol"])
            if px is None:
                continue
            if row.get("stop_price") is not None:
                # A stop triggers when price moves THROUGH it, which is the
                # opposite sense to a limit: a sell stop fires below, a buy stop
                # above. Then it becomes a market order.
                #
                # IT IS COMPARED AGAINST THE TRIGGER REFERENCE, NOT AGAINST THE
                # CONTRACT'S OWN PRICE. On an option those are different numbers
                # by three orders of magnitude -- a premium of 38 against a
                # trigger of 61284 -- and comparing them fired every resting
                # sell stop the instant it was placed. On 2026-11-26 that closed
                # Mini 1's and Mini 2's entire books within a second of their
                # protection finally being placed.
                #
                # It FILLS at the contract's own price, which is the premium.
                # Two prices, two jobs.
                ref = await _trigger_px(row["symbol"], row.get("stop_trigger_method"))
                if ref is None:
                    # Unreadable reference: wait. Never fire on a price we do
                    # not have -- that is the bug above, with extra steps.
                    continue
                level = float(row["stop_price"])
                fired = ref <= level if row["side"] == "sell" else ref >= level
                if fired:
                    await self._fill(row, px)
                continue
            if row.get("limit_price") is not None and self._crossed(
                    row["side"], px, float(row["limit_price"])):
                await self._fill(row, float(row["limit_price"]))

    async def disconnect_websocket(self) -> None:
        self._running = False
        if self._tick_task is not None:
            self._tick_task.cancel()
            try:
                await self._tick_task
            except (asyncio.CancelledError, Exception):
                pass
            self._tick_task = None

    async def close(self) -> None:
        await self.disconnect_websocket()

    @property
    def ws_connected(self) -> bool:
        return bool(self._running)
