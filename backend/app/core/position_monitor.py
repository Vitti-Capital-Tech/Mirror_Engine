import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, List, Optional
from app.database import db
from app.websocket.socket_manager import socket_manager
from app.core.risk_engine import RiskEngine
from app.core import order_ledger as ledger
from app.core.account_cache import account_cache
from app.services import telegram_client as tg
from app.core import quote_cache
import asyncio
import time

logger = logging.getLogger(__name__)

# Don't re-raise the same (account, symbol) position-mismatch alert more than once
# within this window — even if it resolves and re-triggers as the master trades.
MISMATCH_COOLDOWN_MIN = 30

class PositionMonitor:
    def __init__(self, db_client, socket_mgr) -> None:
        self.db = db_client
        self.socket_manager = socket_mgr
        self.risk_engine = RiskEngine()
        # (account_id, symbol) -> when we first saw a follower position with no
        # master row behind it. See the "no master row" branch in
        # check_sync_and_alert for why this is timed rather than immediate.
        self._orphan_since: dict = {}
        # Set at startup. Needed to read the master's high-water size per symbol —
        # without it this monitor alerts on gaps the copy engine is deliberately
        # maintaining. See check_sync_and_alert.
        self.redis = None
        # Cache of latest positions to avoid redundant DB queries on every message
        # Structure: account_id -> symbol -> position_dict
        self._positions_cache: Dict[str, Dict[str, dict]] = {}

    def make_position_callback(self, account_id: str, account_name: str):
        """Bind account context to the websocket callback."""
        async def callback(position_data: dict):
            await self.on_position_update(account_id, account_name, position_data)
        return callback

    async def start_monitoring(self, accounts_data: List[dict]) -> None:
        """Initialize the positions cache from existing database records."""
        logger.info("Initializing PositionMonitor cache...")
        try:
            res = self.db.table("positions").select("*").execute()
            positions = res.data or []
            for pos in positions:
                acc_id = pos["account_id"]
                symbol = pos["symbol"]
                if acc_id not in self._positions_cache:
                    self._positions_cache[acc_id] = {}
                self._positions_cache[acc_id][symbol] = pos
            logger.info(f"PositionMonitor cache initialized with {len(positions)} positions.")
        except Exception as e:
            logger.error(f"Failed to initialize PositionMonitor cache: {e}")

    async def on_position_update(self, account_id: str, account_name: str, position_data: dict) -> None:
        """
        Callback handler for WebSocket position messages.
        1. Save/update position in Supabase
        2. Compare with master for mismatch check
        3. Emit update via Socket.IO
        """
        try:
            symbol = position_data.get("product_symbol") or position_data.get("symbol")
            if not symbol:
                return

            # Only treat as closed if 'size' is explicitly present in the payload AND is 0.
            # If 'size' is absent (partial WS update), don't infer closure — avoids false deletions.
            size_present = "size" in position_data or "quantity" in position_data
            raw_size = float(position_data.get("size") or position_data.get("quantity") or 0.0)

            # Map side: Delta REST API uses a signed 'size' field (positive=long, negative=short).
            # The WebSocket 'positions' channel may include an explicit 'side' field ('long'/'short').
            # Priority: explicit side field > sign of size value.
            explicit_side = position_data.get("side")
            if explicit_side and str(explicit_side).lower() in ("long", "short", "buy", "sell"):
                side_str = str(explicit_side).lower()
                side = "long" if side_str in ("long", "buy") else "short"
            else:
                side = "long" if raw_size >= 0 else "short"

            # Use absolute quantity for storage
            raw_qty = abs(raw_size)

            entry_price = float(position_data.get("entry_price") or 0.0)
            mark_price = float(position_data.get("mark_price") or position_data.get("current_price") or entry_price)

            # PnL is valued at the price this position would actually be CLOSED
            # at — a long sells into the best bid, a short lifts the best ask —
            # not at mark_price. On options the two are nowhere near each other:
            # C-BTC-72000-141126 on 2026-11-13 marked at 1.97 against a book of
            # 2.8 / 3, so the dashboard could not agree with Delta's own number.
            # (Jigar, 2026-11-11: "mark price ki jagah sirf bid ask use karna
            # hai".) Falls back to mark_price when the book cannot be read.
            current_price = await quote_cache.exit_price_for(
                symbol, side, fallback=mark_price,
                environment=(position_data.get("environment") or "production"),
            )
            
            # Options contracts have a multiplier (e.g. 0.001 BTC per contract).
            # Look for 'contract_value' in the API payload. If missing, check symbol format.
            multiplier = float(position_data.get("contract_value") or (0.001 if ("-C-" in symbol or "-P-" in symbol or symbol.startswith("C-") or symbol.startswith("P-")) else 1.0))
            
            # Calculate Unrealized PnL manually to ensure direction and options calculations are correct
            # PnL = (Price Diff) * Quantity * Multiplier
            if side == "long":
                unrealized_pnl = (current_price - entry_price) * raw_qty * multiplier
            else:
                unrealized_pnl = (entry_price - current_price) * raw_qty * multiplier
            
            logger.info(f"Unrealized PnL manual calculation for {symbol}: qty={raw_qty}, multiplier={multiplier}, side={side}, pnl={unrealized_pnl:.4f}")

            realized_pnl = float(position_data.get("realized_pnl") or 0.0)
            
            sl_price = float(position_data.get("stop_loss_price")) if position_data.get("stop_loss_price") else None
            tp_price = float(position_data.get("take_profit_price")) if position_data.get("take_profit_price") else None

            logger.info(f"Position update for {account_name} on {symbol}: qty={raw_qty}, price={current_price}")

            # If qty is 0 AND size was explicitly in the payload, the position is closed.
            if raw_qty == 0 and size_present:
                # Delete position from DB
                self.db.table("positions").delete().eq("account_id", account_id).eq("symbol", symbol).execute()
                if account_id in self._positions_cache and symbol in self._positions_cache[account_id]:
                    self._positions_cache[account_id].pop(symbol)
                
                # Emit position update with qty 0
                await self.socket_manager.emit_position_update({
                    "account_id": account_id,
                    "account_name": account_name,
                    "symbol": symbol,
                    "side": side,
                    "quantity": 0,
                    "entry_price": entry_price,
                    "current_price": current_price,
                    "unrealized_pnl": 0,
                    "sync_status": "synced"
                })
                
                # Check if we need to resolve mismatch alerts
                await self._resolve_mismatch_alert(account_id, symbol)
                return

            # Determine if this is a new position (not previously seen in cache)
            now_iso = datetime.utcnow().isoformat() + "Z"
            is_new = account_id not in self._positions_cache or symbol not in self._positions_cache[account_id]

            # Upsert position in Supabase
            db_pos = {
                "account_id": account_id,
                "symbol": symbol,
                "side": side,
                "quantity": raw_qty,
                "entry_price": entry_price,
                "current_price": current_price,
                "unrealized_pnl": unrealized_pnl,
                "realized_pnl": realized_pnl,
                "sl_price": sl_price,
                "tp_price": tp_price,
                "last_synced_at": now_iso
            }

            # Set created_at only on first insert (new positions) — preserve on updates
            if is_new:
                db_pos["created_at"] = now_iso

            upsert_res = self.db.table("positions").upsert(db_pos, on_conflict="account_id,symbol").execute()
            if upsert_res.data:
                position_record = upsert_res.data[0]
            else:
                position_record = db_pos
                position_record["id"] = f"{account_id}-{symbol}"

            # Update cache
            if account_id not in self._positions_cache:
                self._positions_cache[account_id] = {}
            self._positions_cache[account_id][symbol] = position_record

            # Mismatch check
            sync_status = await self.check_sync_and_alert(account_id, account_name, symbol, raw_qty, entry_price, side)
            position_record["sync_status"] = sync_status

            # Emit update
            await self.socket_manager.emit_position_update({
                "id": position_record.get("id"),
                "account_id": account_id,
                "account_name": account_name,
                "symbol": symbol,
                "side": side,
                "quantity": raw_qty,
                "entry_price": entry_price,
                "current_price": current_price,
                "unrealized_pnl": unrealized_pnl,
                "sl_price": sl_price,
                "tp_price": tp_price,
                "sync_status": sync_status,
                "last_synced_at": position_record.get("last_synced_at")
            })

        except Exception as e:
            logger.error(f"Error handling position update: {e}", exc_info=True)

    async def check_sync_and_alert(
        self,
        account_id: str,
        account_name: str,
        symbol: str,
        follower_qty: float,
        follower_price: float,
        side: str
    ) -> str:
        """
        Compare follower's position against the master's position.
        Returns 'synced' or 'out_of_sync'.
        """
        try:
            # 1. The account being checked, first — because WHICH master it
            #    should be measured against depends on who owns it. (Cached; this
            #    path runs ~1.1x/second against a synchronous client.)
            follower_account = account_cache.get(self.db, account_id)
            if not follower_account:
                return "unknown"

            # A master is copied from nothing, so it can never be out of sync.
            # This used to read `account_id == account_cache.master(db)["id"]`,
            # which quietly stopped protecting Jigar the moment a second tenant
            # added a second master: master() returned whichever row Postgres
            # gave first (Algo), so Jigar failed the test, was treated as a
            # follower of Algo, and was alerted on. Ask the row itself instead.
            if follower_account.get("is_master"):
                return "synced"

            owner_id = follower_account.get("owner_id")

            # 2. The master OF THIS OWNER. Comparing against another tenant's
            #    master is what produced "expected size=35" for a follower that
            #    was holding exactly the 85 it should have been.
            master_account = account_cache.master_for(self.db, owner_id)
            if not master_account:
                return "unknown"
            master_id = master_account["id"]

            # 3. Fetch that master's position for this symbol.
            # If we have NO record of the master's position for this symbol, we
            # cannot compute an expected size (e.g. options positions can't be
            # fetched over REST — Delta's options endpoint 400s — and the WS
            # snapshot may be empty). Return 'unknown' instead of raising a false
            # "expected size = 0" mismatch for a follower that is actually synced.
            master_pos_res = self.db.table("positions").select("*").eq("account_id", master_id).eq("symbol", symbol).execute()
            if not master_pos_res.data:
                # THE BLIND SPOT. A closed symbol has its position row DELETED,
                # not zeroed — verified on the live table, which holds no rows at
                # quantity 0. So "no master row" is indistinguishable from "the
                # master is flat", and returning unknown means the check goes
                # quiet in exactly the situation where the follower is most
                # exposed: still holding a position the master has fully exited.
                #
                # On 2026-11-11 that is why NOTHING alerted between 03:05 and
                # 03:25 while the follower held C-BTC-74000 and C-BTC-75000 with
                # its TP/SL already cancelled.
                #
                # Still returns "unknown" — the sizing logic genuinely cannot
                # compute a target here, and flipping this to a mismatch would
                # raise false alarms on options, whose positions REST cannot read
                # (Delta's endpoint 400s) and whose WS snapshot can be briefly
                # empty. Instead it is TIMED: a snapshot gap clears in seconds, a
                # real orphan does not. Only a position that outlives
                # _ORPHAN_ALERT_SEC with no master row behind it is called out.
                await self._maybe_alert_orphan(account_id, account_name, symbol, follower_qty)
                return "unknown"
            self._orphan_since.pop((account_id, symbol), None)
            master_qty = float(master_pos_res.data[0]["quantity"])

            # Copied because master_balance is injected below for the sizing call,
            # and the cached row is shared with every other caller this tick.
            follower_account = dict(follower_account)

            # Master balance for the expected-quantity calculation. This is the
            # SAME row fetched in step 1 — it used to be a second round trip a few
            # lines later, selecting the full row the first query had thrown away.
            master_balance = float(
                master_account.get("allocated_balance")
                or master_account.get("balance")
                or master_account.get("available_margin")
                or 0.0
            )

            follower_account["master_balance"] = master_balance

            # 4. Calculate expected qty (ceil, to match how orders are actually
            #    sized/placed — otherwise flooring here would flag false mismatches)
            expected_qty = self.risk_engine.calculate_follower_quantity(master_qty, follower_price, follower_account, round_up=True)
            if master_qty == 0:
                expected_qty = 0.0

            # 5. Check mismatch threshold (5% difference or minimum 1 lot mismatch)
            diff = abs(follower_qty - expected_qty)
            mismatch_limit = max(1.0, expected_qty * 0.05)

            # An UNDER-exposed follower on a leg the master is unwinding is not a
            # fault — it is the copy engine's deliberate choice. The reconciler
            # refuses to top up while the master sits below its high-water size
            # ("never add while the master is unwinding this symbol"), so alerting
            # on the resulting gap means alerting on our own correct decision.
            #
            # Observed 2026-08-26 on P-BTC-76800-260826: peak 700, master holding
            # 350, follower 3 against an expected 4-5. The reconciler logged
            # "under-exposed (3 vs 4) but master is BELOW its peak (350 of 700) —
            # net-reduced on this leg, not adding" while this check raised an ERROR
            # alert to Telegram for the same state, repeatedly.
            #
            # OVER-exposure is still alerted: holding more than intended is a real
            # risk whatever the master is doing.
            if follower_qty < expected_qty and diff > mismatch_limit:
                peak = await ledger.get_peak(self.redis, follower_account.get("owner_id"), symbol)
                if peak and abs(master_qty) < peak:
                    logger.info(
                        f"{account_name} under-exposed on {symbol} ({follower_qty} vs "
                        f"{expected_qty}) but master is BELOW its peak ({master_qty:.0f} of "
                        f"{peak:.0f}) — expected while it unwinds, not alerting"
                    )
                    await self._resolve_mismatch_alert(account_id, symbol, owner_id)
                    self.db.table("positions").update({"sync_status": "synced"}).eq(
                        "account_id", account_id).eq("symbol", symbol).execute()
                    return "synced"

            if diff > mismatch_limit:
                status = "out_of_sync"
                msg = f"Position mismatch for {account_name} on {symbol}: actual size={follower_qty}, expected size={expected_qty} (diff={diff})"

                # Notify BOTH the follower and the master account, but only ONCE
                # per mismatch episode: if an UNRESOLVED mismatch alert already
                # exists for this (account, symbol), stay silent — no repeated
                # pinging every 30 min while it persists. It re-arms automatically
                # when the mismatch resolves (_resolve_mismatch_alert flips
                # is_resolved), so a fresh mismatch later alerts once again.
                targets = [account_id, master_id]
                open_alerts = (
                    self.db.table("alerts")
                    .select("account_id, metadata")
                    .eq("type", "position_mismatch")
                    .eq("is_resolved", False)
                    .in_("account_id", targets)
                    .execute()
                )
                have = {
                    (a.get("account_id"), (a.get("metadata") or {}).get("symbol"))
                    for a in (open_alerts.data or [])
                }
                meta = {
                    "symbol": symbol,
                    "follower_qty": follower_qty,
                    "expected_qty": expected_qty,
                    "master_qty": master_qty,
                }
                to_insert = [
                    {"level": "error", "type": "position_mismatch", "account_id": aid,
                     "message": msg, "metadata": meta, "owner_id": owner_id}
                    for aid in targets if (aid, symbol) not in have
                ]
                if to_insert:
                    logger.warning(msg)
                    alert_res = self.db.table("alerts").insert(to_insert).execute()
                    for row in (alert_res.data or []):
                        await self.socket_manager.emit_alert(row)
                
                # Update position sync_status in DB
                self.db.table("positions").update({"sync_status": "out_of_sync"}).eq("account_id", account_id).eq("symbol", symbol).execute()
                return "out_of_sync"
            else:
                await self._resolve_mismatch_alert(account_id, symbol, owner_id)
                self.db.table("positions").update({"sync_status": "synced"}).eq("account_id", account_id).eq("symbol", symbol).execute()
                return "synced"

        except Exception as e:
            logger.error(f"Error checking position sync: {e}")
            return "unknown"

    # How long a follower may hold a position with no master row behind it
    # before it is called out. Long enough that a WS snapshot gap or a REST blip
    # clears on its own; short enough to beat the reconciler's own cleanup, so
    # the alert tells you something is wrong rather than confirming it is fixed.
    _ORPHAN_ALERT_SEC = 45.0
    # Longest silence that still counts as the SAME episode. The sync check runs
    # on position updates, roughly every few seconds per symbol, so anything
    # beyond this means the symbol stopped being held and came back.
    _ORPHAN_GAP_SEC = 30.0

    async def _maybe_alert_orphan(self, account_id, account_name, symbol, follower_qty):
        """Warn when a follower is holding a position the master no longer has.

        This is the one the 2026-11-11 incident needed. The follower held
        C-BTC-74000 and C-BTC-75000 after the master had closed both, with its
        TP/SL already cancelled — cancels match by order id, so that half of the
        copy worked while the close did not — and for 4.5 minutes nothing said a
        word. The only Telegram messages the whole incident produced were the
        reconciler's, after the fact.

        Says the two things that matter in one line: the position is unmatched,
        and it has no protection behind it.
        """
        key = (account_id, symbol)
        now = time.time()
        if not follower_qty:
            self._orphan_since.pop(key, None)
            return

        entry = self._orphan_since.get(key)
        # A GAP IN OBSERVATION IS A NEW EPISODE, not a continuing one.
        #
        # This is only reached while the symbol is being checked, and it is only
        # checked while the follower has a position in it. So when a position
        # closes the symbol simply stops being seen — and the clear-on-zero above
        # never runs, because there is no update carrying a zero. The entry then
        # sits there, and the next time that symbol is opened the very first
        # check reads an age of "however long ago that was" and alerts instantly.
        #
        # That is not theoretical: on 2026-11-13 04:19:26 this fired on
        # C-BTC-71600-131126 seven seconds after the follower opened 2515, while
        # the master was taking 2800 in the same breath. Both were flat at
        # 04:19:19, the fill landed at 04:19:24, and the "orphan" was a leftover
        # timer from an earlier position in the same symbol. A false alarm on a
        # new alert costs more than the alert is worth — it teaches the desk to
        # ignore it.
        #
        # So an episode must be CONTINUOUSLY observed to age. A gap longer than
        # _ORPHAN_GAP_SEC means whatever we were timing ended; start again.
        if entry is not None:
            first, last = entry
            if now - last > self._ORPHAN_GAP_SEC:
                entry = None
        if entry is None:
            self._orphan_since[key] = (now, now)
            return
        first, _ = entry
        self._orphan_since[key] = (first, now)
        if now - first < self._ORPHAN_ALERT_SEC:
            return
        try:
            await tg.notify_fail(
                account_name, symbol, "hold", abs(follower_qty),
                "master has exited this symbol but the follower still holds it — "
                "its TP/SL are gone too, so this position is unmatched AND unprotected",
                key=f"orphan:{account_id}:{symbol}",
            )
        except Exception as e:
            logger.warning("orphan alert failed for %s %s: %s", account_name, symbol, e)

    async def _resolve_mismatch_alert(self, account_id: str, symbol: str,
                                      owner_id: Optional[str] = None) -> None:
        """Resolve open position mismatch alerts for this symbol on BOTH the
        follower and the master (mismatch alerts are raised on both).

        The master here must be THIS owner's master. It used to be the global
        master_id(), which with two tenants resolved the wrong account's alerts
        and left the right ones standing: on 2026-10-14 every Algo/Algo2 alert
        read resolved while Jigar's and Mini Prathav's sat open indefinitely,
        which is the notification that kept being shown for a mismatch that had
        already cleared.
        """
        try:
            account_ids = [account_id]
            try:
                if owner_id is None:
                    acc = account_cache.get(self.db, account_id) or {}
                    owner_id = acc.get("owner_id")
                mid = account_cache.master_id_for(self.db, owner_id)
                if mid:
                    account_ids.append(mid)
            except Exception:
                pass

            alerts_res = (
                self.db.table("alerts").select("*")
                .eq("type", "position_mismatch").eq("is_resolved", False)
                .in_("account_id", account_ids).execute()
            )
            for alert in (alerts_res.data or []):
                meta = alert.get("metadata") or {}
                if meta.get("symbol") == symbol:
                    self.db.table("alerts").update({"is_resolved": True}).eq("id", alert["id"]).execute()
                    alert["is_resolved"] = True
                    await self.socket_manager.emit_alert(alert)
                    logger.info(f"Resolved position mismatch alert {alert['id']} for {alert.get('account_id')} on {symbol}")
        except Exception as e:
            logger.error(f"Error resolving mismatch alerts: {e}")


position_monitor = PositionMonitor(db, socket_manager)

