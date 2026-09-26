"""Core trading pipeline (one run per invocation).

Port of the Grok -> Opus 4.6 -> Risk Guard -> Alpaca pipeline from the old
APScheduler-driven main.py. EventBridge Scheduler now provides the cadence:
  - trading_cycle   : every 30 min, mon-fri 9:30-15:30 ET (the actual market
                       session; the market doesn't open until 9:30)
  - emergency_check : every 5 min, mon-fri 9:30-15:55 ET

"Paused" state is persisted in DynamoDB (STATE/scheduler_running) instead of an
in-process scheduler flag, so it survives across stateless Lambda invocations.

Stage 2 is selectable with DECISION_ENGINE:
  - grok : Grok decides directly from its own market report (default)
  - opus : Claude Opus decides from Grok's report (the original design)
Both return the same TradeDecision shape, so Risk Guard and execution are shared.
"""

import os
import uuid
from datetime import datetime
from typing import Any

import pytz

from config import decision_engine, missing_required
from db import (
    acquire_lock,
    emergency_liquidation_recorded_for,
    get_pipeline_logs,
    get_scheduler_state,
    log_pipeline,
    log_trade,
    record_emergency_liquidation,
    set_scheduler_state,
)
from decision_schema import validate_decision
from discord_notifier import DiscordNotifier
from grok_client import NO_PREVIOUS_SENTIMENT, GrokClient
from grok_validator import validate_grok_report
from opus_client import OpusClient
from risk_guard import RiskGuard, effective_buy_price
from skip_logic import should_skip_opus
from trader import DuplicateOrderError, Trader

NY_TZ = pytz.timezone("America/New_York")


def _resolve_now(scheduled_time: str | None) -> datetime:
    """The time to key the slot off: EventBridge Scheduler's own
    <aws.scheduler.scheduled-time> (the intended fire time, ISO 8601 UTC)
    when the event carries one, otherwise the actual current time.

    Using the scheduled time rather than "whenever this Lambda happened to
    start" means a slot boundary being crossed by cold-start/queueing delay
    can't split one scheduled firing into two different slots (and two
    different client_order_ids). A manually-invoked event (no scheduled_time)
    has no such intended time, so it falls back to now.
    """
    if scheduled_time:
        try:
            return datetime.fromisoformat(scheduled_time)
        except ValueError as e:
            print(f"[Scheduler] Could not parse scheduled_time={scheduled_time!r}: {e}")
    return datetime.now(NY_TZ)


def _slot_id(interval_minutes: int, now: datetime) -> str:
    """Floor `now` (tz-aware) to an `interval_minutes` boundary, New York time.

    Used as the DynamoDB lock key (and the Alpaca client_order_id) so that a
    duplicate/retried invocation for the same scheduled slot is recognisable
    as "the same slot" regardless of which Lambda instance runs it.
    """
    now_ny = now.astimezone(NY_TZ)
    floored_minute = (now_ny.minute // interval_minutes) * interval_minutes
    slot_time = now_ny.replace(minute=floored_minute, second=0, microsecond=0)
    return slot_time.strftime("%Y%m%dT%H%M")


# Clients are built on first use rather than at import: Alpaca and the model
# SDKs raise when their keys are absent, and failing at import turns a
# missing-secret problem into an opaque "module could not be loaded" error.
grok = None
opus = None
guard = None
trader = None
notifier = None


def _init_clients() -> None:
    """Construct the API clients once per warm container."""
    global grok, opus, guard, trader, notifier
    if grok is None:
        grok = GrokClient()
    if opus is None and decision_engine() == "opus":
        opus = OpusClient()
    if guard is None:
        guard = RiskGuard()
    if trader is None:
        trader = Trader()
    if notifier is None:
        notifier = DiscordNotifier()


# 監視対象銘柄
WATCHLIST = ["MSTR", "TSLA", "QQQ", "SPY"]

# Alpaca が「注文は受理したが約定させない」ことを示すステータス。submit_order()
# 自体は例外を投げず Order オブジェクトを返すので、trader.execute_order() の
# 戻り値が truthy でも中身がこれらなら実質は失敗として扱う。
FAILED_ORDER_STATUSES = {"rejected", "canceled", "expired"}


def _previous_sentiment_summary() -> str:
    """直前サイクルの Stage 1 センチメント要約を DynamoDB から引く。

    Grok への API 呼び出しは毎回独立していて前回の会話を覚えていないため、
    プロンプト内の「前回から大きく変動」判定はこちらから明示的にデータを
    渡さない限り機能しない（渡さないまま "前回と比較して" と書いていたのが
    元のバグ）。直前が無ければ、無いと明記した文字列を返す。
    """
    try:
        logs = get_pipeline_logs(1)
    except Exception as e:  # noqa: BLE001 - この要約が取れなくても取引は続ける
        print(f"[DB] Could not fetch previous cycle for comparison: {e}")
        return f"{NO_PREVIOUS_SENTIMENT}（取得エラー: {e}）"

    if not logs or not logs[0].get("grok_output"):
        return NO_PREVIOUS_SENTIMENT

    prev = logs[0]["grok_output"]
    sentiment = prev.get("sentiment", {})
    return (
        f"時刻: {prev.get('timestamp', '不明')} / "
        f"センチメント: {sentiment.get('overall', '不明')} / "
        f"重要な変化と判定されたか: {prev.get('significant_change', '不明')}"
    )


def _ready(job: str) -> bool:
    """停止フラグとシークレットを確認し、問題なければクライアントを用意する"""
    if not get_scheduler_state():
        print(f"[Scheduler] Paused - skipping {job}")
        return False

    missing = missing_required()
    if missing:
        print(f"[Config] Missing secrets in SSM: {', '.join(missing)} - skipping {job}")
        return False

    _init_clients()
    return True


# Alpaca だけが emergency_check の前提。Grok/Anthropic のキーは無関係。
_ALPACA_KEYS = ("ALPACA_API_KEY", "ALPACA_SECRET_KEY")


def _emergency_ready() -> bool:
    """emergency_check 専用の準備確認（_ready() は使わない）。

    ドローダウン監視は AI が一切関与しない安全弁 (README の「AI 不要」) なので、
    停止フラグ（一時停止中でも動かなければ意味がない）も AI キーの有無
    （Grok/Opus は呼ばない）も見ない。Alpaca のキーだけを確認し、
    trader/notifier だけを用意する（grok/opus/guard は構築しない）。
    """
    missing = [k for k in _ALPACA_KEYS if not os.getenv(k)]
    if missing:
        print(f"[Config] Missing secrets in SSM: {', '.join(missing)} - skipping emergency check")
        return False

    global trader, notifier
    if trader is None:
        trader = Trader()
    if notifier is None:
        notifier = DiscordNotifier()
    return True


async def _alert_startup_failure(job: str, error: Exception) -> None:
    """_ready() raised, so `notifier` may not have been constructed yet."""
    print(f"[Error] {job} startup failed: {error}")
    try:
        await (notifier or DiscordNotifier()).notify_alert(f"{job} startup failed: {error}", "error")
    except Exception as e:  # noqa: BLE001 - alerting must not mask the original error
        print(f"[Discord] Startup failure alert failed: {e}")


async def trading_cycle(scheduled_time: str | None = None) -> None:
    """メイン取引サイクル（30分ごと・4ステージパイプライン）

    scheduled_time: EventBridge Scheduler の <aws.scheduler.scheduled-time>
    （lambda_trading.handler がイベントから渡す）。手動実行では None。
    """
    # _ready() constructs the API clients, which raise on missing/invalid keys.
    # Outside this try, such a failure would end the cycle with no alert at all.
    try:
        if not _ready("trading cycle"):
            return
    except Exception as e:  # noqa: BLE001
        await _alert_startup_failure("Trading cycle", e)
        return

    # F8: 市場カレンダーで閉場中なら取引しない（ログのみ）。EventBridge の
    # cron は祝日・臨時休場を知らないので、営業日どうかは Alpaca の clock で
    # 確認する必要がある。取得失敗は fail closed（取引しない） - 開場中か
    # 分からない状態で取引を進めるほうが危険。
    assert trader is not None  # _ready() succeeded, so this is constructed
    try:
        if not trader.is_market_open():
            print("[Trading cycle] Market is closed - skipping")
            return
    except Exception as e:  # noqa: BLE001
        print(f"[Trading cycle] Could not confirm market is open: {e} - skipping (fail closed)")
        return

    # Claim this 30-min slot before doing anything else. A retried/duplicate
    # EventBridge invocation (or a manual re-run) for the same slot is
    # rejected here instead of running the pipeline - and placing orders -
    # twice. A cycle that throws after claiming the lock is not retried by
    # design: the scheduler's own retry would be blocked by this same lock.
    slot = _slot_id(30, _resolve_now(scheduled_time))
    if not acquire_lock("trading", slot):
        print(f"[Lock] Trading cycle for slot {slot} already handled - skipping")
        return

    # _ready() succeeded, so these are all constructed; bind to locals (typed,
    # narrowed) rather than re-checking the module globals at every call site.
    assert grok is not None
    assert guard is not None
    assert trader is not None
    assert notifier is not None
    _grok, _guard, _trader, _notifier = grok, guard, trader, notifier

    cycle_id = f"{slot}-{uuid.uuid4().hex[:8]}"
    engine = decision_engine()
    print(f"\n{'=' * 50}")
    print(f"[{datetime.now()}] Cycle {cycle_id} started (decision engine: {engine})")
    print("=" * 50)

    try:
        # === アカウント・市場データ取得 ===
        account = _trader.get_account()
        positions = _trader.get_positions()
        daily_pnl = account["daily_pnl"]

        print(f"Cash: ${account['cash']:,.2f} | Daily P&L: ${daily_pnl:+,.2f} | Positions: {len(positions)}")

        # システム健全性チェック
        health = _guard.check_system_health(daily_pnl)
        if not health.allowed:
            print(f"[RiskGuard] System stopped: {health.reason}")
            # F3: 停止フラグの保存を先に、Discord 通知は後にする（通知が
            # 詰まる/失敗しても保存が行われるように）。通知はそれぞれ個別の
            # try で行い、どちらが失敗してももう片方の結果に影響しない。
            try:
                set_scheduler_state(False)  # 以降のサイクルを停止
            except Exception as e:
                # The flag write failed, so the next cycle might not see the
                # stop - but this cycle still must not trade (return below).
                print(f"[DB] Failed to persist stop flag: {e}")
                try:
                    await _notifier.notify_alert(f"Failed to persist stop flag: {e}", "error")
                except Exception as notify_err:
                    print(f"[Discord] Failure alert failed: {notify_err}")
            try:
                await _notifier.notify_system_stop(health.reason or "unknown")
            except Exception as e:
                print(f"[Discord] System stop notification failed: {e}")
            return

        market_data = _trader.get_market_data(WATCHLIST)
        if not market_data:
            print("[Trader] No market data available")
            return

        # === Stage 1: Grok 情報収集 ===
        # Grok は前回の呼び出しを覚えていないので、比較材料を明示的に渡す。
        previous_sentiment = _previous_sentiment_summary()
        grok_report, grok_latency = _grok.collect_market_report(
            market_data=market_data,
            positions=positions,
            previous_sentiment=previous_sentiment,
        )

        if not grok_report:
            print("[Grok] No report returned")
            return

        grok_report = validate_grok_report(grok_report)
        try:
            await _notifier.send_pipeline_log(cycle_id, "grok", grok_report)
        except Exception as e:
            print(f"[Discord] Grok log failed: {e}")

        print(
            f"[Grok] Sentiment: {grok_report['sentiment']['overall']} | "
            f"Change: {grok_report['significant_change']} | "
            f"Latency: {grok_latency}ms"
        )

        # ログ共通項目
        base: dict[str, Any] = {
            "cycle_id": cycle_id,
            "decision_engine": engine,
            "grok_input": {"market_data": market_data},
            "grok_output": grok_report,
            "grok_latency_ms": grok_latency,
        }

        # === 判断スキップ判定 ===
        if should_skip_opus(grok_report):
            print(f"[Skip] No significant change → {engine} decision skipped")
            try:
                await _notifier.send_pipeline_log(cycle_id, "skip", {})
            except Exception as e:
                print(f"[Discord] Skip log failed: {e}")
            log_pipeline(**base, opus_skipped=True)
            return

        # === Stage 2: 売買判断（Grok または Opus） ===
        if engine == "opus":
            assert opus is not None  # _init_clients() constructs it when engine == "opus"
            decide = opus.analyze
        else:
            decide = _grok.decide
        decision, decision_latency = decide(
            balance=account["cash"],
            positions=positions,
            daily_pnl=daily_pnl,
            max_daily_loss=_guard.max_daily_loss,
            price_data=market_data,
            grok_report=grok_report,
        )

        if not decision:
            print(f"[{engine.capitalize()}] No decision returned")
            log_pipeline(**base, opus_latency_ms=decision_latency)
            return

        # === Stage 2.5: AI 出力の検証 ===
        # RiskGuard.check() はここまでの前提（存在するティッカー、実際に保有して
        # いる銘柄、現在値を踏まえた損切り価格）が正しいことを仮定して動く。
        # AI がそれを満たさない判断を返したら、RiskGuard に渡す前に hold へ倒す。
        validation_symbol = decision.get("symbol")
        validation_price = market_data.get(validation_symbol, {}).get("price") if validation_symbol else None
        decision, invalid_reason = validate_decision(decision, WATCHLIST, positions, validation_price)
        if invalid_reason:
            print(f"[Validate] Decision failed validation, forcing hold: {invalid_reason}")
            try:
                await _notifier.notify_alert(f"Decision validation failed: {invalid_reason}", "warning")
            except Exception as e:
                print(f"[Discord] Validation warning failed: {e}")

        decision["decision_engine"] = engine
        try:
            await _notifier.send_pipeline_log(cycle_id, "opus_decision", decision)
        except Exception as e:
            print(f"[Discord] Decision log failed: {e}")

        print(
            f"[{engine.capitalize()}] {decision['action'].upper()} {decision.get('symbol', '')} | "
            f"Confidence: {decision['confidence']}% | "
            f"Size: {decision.get('position_size_pct', 0)}% | "
            f"Latency: {decision_latency}ms"
        )

        base.update(
            opus_output=decision,
            opus_latency_ms=decision_latency,
            opus_adjustments=decision.get("adjustments", []),
        )

        # === Stage 3: Risk Guard ===
        # price/equity are resolved here (not just before execution) because
        # RiskGuard.check() needs them to size a buy in actual shares rather
        # than trust the AI's requested quantity at face value.
        symbol = decision.get("symbol")
        price = market_data.get(symbol, {}).get("price", 0) if symbol else 0

        # 未約定の買い注文一覧を1回だけ取得する（total position ratio の
        # 想定額 = M2、同一銘柄チェック = Stage 4 の両方で使う）。
        # buy 判断のときだけ取得する。取得失敗は E1: fail closed - 既存の
        # exposure が分からない以上、新規の買いは見送る（売りはこの一覧を
        # 使わないので影響しない）。
        open_buy_order_value = 0.0
        open_buy_order_symbols: set = set()
        if decision["action"] == "buy":
            try:
                open_buy_orders = _trader.get_open_buy_orders()
            except Exception as e:
                reason = f"open_buy_orders_unavailable: {e}"
                print(f"[Execute] Skipping buy - could not check open buy orders: {e}")
                try:
                    await _notifier.notify_alert(f"Skipped buy: could not check open orders ({e})", "warning")
                except Exception as notify_err:
                    print(f"[Discord] Warning failed: {notify_err}")
                log_pipeline(**base, rg_passed=False, rg_reason=reason)
                return

            open_buy_order_symbols = {o["symbol"] for o in open_buy_orders}
            for o in open_buy_orders:
                order_price = o["limit_price"] or market_data.get(o["symbol"], {}).get("price", 0)
                open_buy_order_value += o["qty"] * order_price

        rg_result = _guard.check(
            decision,
            {
                "daily_pnl": daily_pnl,
                "positions": positions,
                "open_buy_order_value": open_buy_order_value,
            },
            price=price,
            equity=account["equity"],
        )

        if rg_result.adjustments:
            try:
                await _notifier.send_pipeline_log(
                    cycle_id,
                    "risk_guard",
                    {
                        "passed": True,
                        "adjustments": rg_result.adjustments,
                    },
                )
            except Exception as e:
                print(f"[Discord] RiskGuard adjustment log failed: {e}")
            print(f"[RiskGuard] Adjustments: {len(rg_result.adjustments)}")

        if not rg_result.allowed:
            try:
                await _notifier.send_pipeline_log(
                    cycle_id,
                    "risk_guard",
                    {
                        "passed": False,
                        "reason": rg_result.reason,
                    },
                )
            except Exception as e:
                print(f"[Discord] RiskGuard block log failed: {e}")
            print(f"[RiskGuard] BLOCKED: {rg_result.reason}")
            log_pipeline(
                **base,
                rg_passed=False,
                rg_reason=rg_result.reason,
                rg_adjustments=rg_result.adjustments,
            )
            return

        # === Stage 4: Execute ===
        if decision["action"] == "hold":
            print("[Action] Hold - no trade")
            log_pipeline(
                **base,
                rg_passed=True,
                rg_reason=f"invalid_decision: {invalid_reason}" if invalid_reason else "hold",
                rg_adjustments=rg_result.adjustments,
            )
            return

        # symbol/price were already resolved before Stage 3 for RiskGuard.check().
        # decision_schema guarantees a symbol for buy/sell (hold, handled
        # above, is the only action allowed to omit it).
        assert symbol is not None

        # 発注前の前提チェック（買いのみ）。どちらも満たさない注文は Alpaca に
        # 必ず拒否され、30分ごとに失敗通知が飛び続けるだけになる。
        if decision["action"] == "buy":
            skip_reason = None
            buying_power = account.get("buying_power", account["cash"])
            # 指値なら現在値と指値の高い方を使う（risk_guard.check() と同じ
            # 実効価格 - 低く見積もって買付余力チェックをすり抜けさせない）。
            effective_price = effective_buy_price(
                price, decision.get("order_type", "market"), decision.get("limit_price")
            )
            # open_buy_order_symbols was already fetched above (Stage 3) - a
            # failure there already returned before reaching here, so this is
            # always the real, current set for a buy decision.
            if symbol in open_buy_order_symbols:
                skip_reason = "open_buy_order_pending"
            elif effective_price and decision["quantity"] * effective_price > buying_power:
                skip_reason = (
                    f"insufficient_buying_power: need ${decision['quantity'] * effective_price:,.2f}, "
                    f"have ${buying_power:,.2f}"
                )
            if skip_reason:
                print(f"[Execute] Skipped {symbol}: {skip_reason}")
                log_pipeline(
                    **base,
                    rg_passed=True,
                    rg_adjustments=rg_result.adjustments,
                    order_submitted=False,
                    execution_result={"skipped": skip_reason},
                )
                return

        # The cycle has been waiting on the AI stages for a while. An emergency
        # check (or /stop) may have halted trading and cancelled every order in
        # the meantime, so re-read the flag right before sending anything.
        # get_scheduler_state() fails closed: a read error also counts as stopped.
        if not get_scheduler_state():
            print(f"[Execute] Skipped {symbol}: trading was stopped during this cycle")
            log_pipeline(
                **base,
                rg_passed=True,
                rg_adjustments=rg_result.adjustments,
                order_submitted=False,
                execution_result={"skipped": "stopped_during_cycle"},
            )
            return

        try:
            order = _trader.execute_order(
                symbol=symbol,
                action=decision["action"],
                quantity=decision["quantity"],
                order_type=decision.get("order_type", "market"),
                limit_price=decision.get("limit_price"),
                stop_loss=decision.get("stop_loss"),
                take_profit=decision.get("take_profit"),
                client_order_id=f"gt-{slot}-{symbol}-{decision['action']}",
            )
        except DuplicateOrderError as e:
            # Alpaca's own client_order_id dedup caught what the DynamoDB
            # lock didn't (e.g. the lock's writer crashed after acquiring it
            # but before this point, then a manual retry reused the slot).
            # Not a failure - no alert.
            print(f"[Execute] {e}")
            log_pipeline(
                **base,
                rg_passed=True,
                rg_adjustments=rg_result.adjustments,
                order_submitted=False,
                execution_result={"skipped": "duplicate_order"},
            )
            return

        # Narrow the check-then-send window: if a stop landed while the order
        # was in flight, take the order back. Buys only - a sell only reduces
        # exposure, which is what a stop wants anyway.
        if (
            order
            and decision["action"] == "buy"
            and order["status"] not in FAILED_ORDER_STATUSES
            and not get_scheduler_state()
        ):
            print(f"[Execute] Trading stopped while {order['order_id']} was being sent - cancelling it")
            try:
                _trader.cancel_order(order["order_id"])
            except Exception as e:  # noqa: BLE001
                print(f"[Trader] Cancel after stop failed: {e}")
                try:
                    await _notifier.notify_alert(
                        f"Order {order['order_id']} ({symbol}) was sent after trading stopped "
                        f"and could not be cancelled: {e}",
                        "error",
                    )
                except Exception as alert_error:  # noqa: BLE001
                    print(f"[Discord] Alert failed: {alert_error}")
                raise
            log_pipeline(
                **base,
                rg_passed=True,
                rg_adjustments=rg_result.adjustments,
                order_submitted=False,
                alpaca_order_id=order["order_id"],
                execution_result={"skipped": "stopped_during_send", "cancelled": order["order_id"]},
            )
            return

        if order and order["status"] not in FAILED_ORDER_STATUSES:
            exec_result = {
                "alpaca_order_id": order["order_id"],
                "status": order["status"],
            }
            try:
                await _notifier.send_pipeline_log(cycle_id, "execution", exec_result)
            except Exception as e:
                print(f"[Discord] Execution log failed: {e}")
            print(f"[Order] {order['order_id']} | Status: {order['status']}")

            log_trade(
                cycle_id=cycle_id,
                symbol=symbol,
                action=decision["action"],
                quantity=decision["quantity"],
                price=price,
                order_type=decision.get("order_type", "market"),
                status=order["status"],
                alpaca_order_id=order["order_id"],
                stop_loss=decision.get("stop_loss"),
                take_profit=decision.get("take_profit"),
            )
            log_pipeline(
                **base,
                rg_passed=True,
                rg_adjustments=rg_result.adjustments,
                order_submitted=True,
                alpaca_order_id=order["order_id"],
                execution_result=exec_result,
            )
        elif order:
            # Alpaca accepted the request but rejected/canceled/expired the
            # order itself - not the same as a submit failure (order is None
            # below), but still not a trade: don't record it in `trades`.
            print(f"[Order] {symbol} {order['status']}: {order['order_id']}")
            await _notifier.notify_alert(f"Order {order['status']}: {symbol}", "error")
            log_pipeline(
                **base,
                rg_passed=True,
                rg_adjustments=rg_result.adjustments,
                order_submitted=False,
                alpaca_order_id=order["order_id"],
                execution_result={"status": order["status"]},
            )
        else:
            print("[Order] Failed to execute")
            await _notifier.notify_alert(f"Order failed: {symbol}", "error")
            log_pipeline(
                **base,
                rg_passed=True,
                rg_adjustments=rg_result.adjustments,
                order_submitted=False,
            )

    except Exception as e:
        print(f"[Error] Trading cycle failed: {e}")
        await _notifier.notify_alert(f"Trading error: {e}", "error")
        # Re-raise (Issue #9) so the Lambda invocation itself is reported as
        # failed (CloudWatch Errors metric) instead of a swallowed exception
        # silently looking like a normal, uneventful run. The slot lock
        # already claimed above means a retry of this same slot cannot
        # double-execute.
        raise


class EmergencyLiquidationFailed(Exception):
    """execute_emergency_liquidation() raised, or reported a per-symbol
    failure. emergency_check() always re-raises this (unlike other
    unexpected errors in that function, which are still swallowed) so it
    reaches CloudWatch as a Lambda error - a failed emergency liquidation
    must never look like a normal, uneventful run.
    """


async def emergency_check(scheduled_time: str | None = None) -> None:
    """5分間隔でドローダウン監視（AI不要・一時停止中でも動く）

    scheduled_time: trading_cycle() と同じ（EventBridge の scheduled-time）。
    """
    try:
        if not _emergency_ready():
            return
    except Exception as e:  # noqa: BLE001
        await _alert_startup_failure("Emergency check", e)
        return

    now = _resolve_now(scheduled_time)
    slot = _slot_id(5, now)
    try:
        lock_acquired = acquire_lock("emergency", slot)
    except Exception as e:  # noqa: BLE001
        # E3: a lock-check failure must not block the drawdown check itself -
        # a redundant emergency check (worst case: two liquidation attempts
        # a few seconds apart, the second a no-op) is far cheaper than
        # silently skipping the safety net because DynamoDB hiccuped.
        print(f"[Lock] Could not check emergency lock for slot {slot}: {e} - continuing anyway (fail open)")
        lock_acquired = True
    if not lock_acquired:
        print(f"[Lock] Emergency check for slot {slot} already handled - skipping")
        return

    # _emergency_ready() succeeded, so these are constructed.
    assert trader is not None
    assert notifier is not None
    _trader, _notifier = trader, notifier

    try:
        account = _trader.get_account()
        equity = account["equity"]
        last_equity = account["last_equity"]

        if last_equity > 0:
            drawdown_pct = ((last_equity - equity) / last_equity) * 100
            if drawdown_pct > 5:
                today = now.astimezone(NY_TZ).strftime("%Y-%m-%d")
                # Stop new buys first, before anything below can fail (for
                # example get_positions()). The branches below write the flag
                # again and report a write failure; this is the early attempt.
                try:
                    set_scheduler_state(False)
                except Exception as e:  # noqa: BLE001
                    print(f"[DB] Early stop-flag write failed: {e} - retrying below")
                positions = _trader.get_positions()

                if not positions:
                    # F4: 保有はゼロでも、未約定の買い注文が残っていればそれが
                    # 約定して新規にポジションを持ってしまう。清算する保有は
                    # 無いが、停止と未約定注文の取り消しは行う。
                    print(
                        f"[EMERGENCY] Drawdown {drawdown_pct:.1f}% > 5%, but nothing is held - "
                        f"stopping and cancelling any pending orders"
                    )
                    no_position_failures: list[str] = []
                    try:
                        _trader.cancel_all_orders()
                    except Exception as e:  # noqa: BLE001
                        print(f"[Trader] Failed to cancel pending orders: {e}")
                        no_position_failures.append(f"cancel pending orders failed: {e}")
                    try:
                        set_scheduler_state(False)
                    except Exception as e:
                        print(f"[DB] Failed to persist stop flag: {e}")
                        no_position_failures.append(f"stop flag write failed: {e}")
                    if no_position_failures:
                        detail = "; ".join(no_position_failures)
                        print(f"[EMERGENCY] Emergency stop (no positions held) had failures: {detail}")
                        try:
                            await _notifier.notify_alert(
                                f"Emergency stop (no positions held) had failures: {detail}", "error"
                            )
                        except Exception as e:
                            print(f"[Discord] Failure alert failed: {e}")
                            detail += f" | also: alert failed: {e}"
                        raise EmergencyLiquidationFailed(detail)
                    return

                # F1: 「本日処理済み」の記録は、通知と注文取り消しを繰り返さない
                # ためだけに使う - 清算そのものを止める理由にはしない。記録が
                # あっても保有が残っている限り清算を試みる（読み取り失敗は
                # E3 と同じ fail open で「未処理」扱い）。
                try:
                    already_handled_today = emergency_liquidation_recorded_for(today)
                except Exception as e:  # noqa: BLE001
                    print(
                        f"[DB] Could not check emergency liquidation record for {today}: "
                        f"{e} - continuing anyway (fail open)"
                    )
                    already_handled_today = False
                if already_handled_today:
                    print(
                        f"[EMERGENCY] Drawdown {drawdown_pct:.1f}% > 5%, and today's liquidation "
                        f"was already recorded, but positions still remain - retrying"
                    )

                print(f"[EMERGENCY] Drawdown {drawdown_pct:.1f}% > 5% → liquidating")

                liquidation_error: Exception | None = None
                results: list[dict] = []
                try:
                    results = _trader.execute_emergency_liquidation()
                except Exception as e:  # noqa: BLE001
                    liquidation_error = e

                failed = [r for r in results if not r.get("ok", True)]
                liquidation_ok = not liquidation_error and not failed
                extra_failures: list[str] = []

                # F1: 清算注文の作成がすべて成功したときだけ記録する。失敗時に
                # 記録すると、保有が残っているのに次回以降の再試行が止まって
                # しまう。
                if liquidation_ok:
                    try:
                        record_emergency_liquidation(today)
                    except Exception as e:
                        print(f"[DB] Failed to record emergency liquidation for {today}: {e}")
                        extra_failures.append(f"liquidation record write failed: {e}")

                reason = f"EMERGENCY STOP: drawdown {drawdown_pct:.1f}%"
                if not liquidation_ok:
                    reason += " - LIQUIDATION FAILED, see error alert"

                try:
                    await _notifier.send_pipeline_log(
                        "EMERGENCY",
                        "risk_guard",
                        {
                            "passed": False,
                            "reason": reason,
                        },
                    )
                except Exception as e:
                    print(f"[Discord] Emergency log failed: {e}")
                    extra_failures.append(f"pipeline log failed: {e}")

                # ドローダウンを検知した以上、清算の成否に関わらず以降の
                # 自動取引は止める（部分的にしか清算できていない状態で
                # 取引を再開するのが最悪のシナリオ）。書き込み自体が失敗しても
                # 先へ進み、最後にまとめて例外を送出する - どれかの失敗で
                # 止まって清算失敗の報告自体が消えるのを避ける。
                try:
                    set_scheduler_state(False)
                except Exception as e:
                    print(f"[DB] Failed to persist stop flag: {e}")
                    extra_failures.append(f"stop flag write failed: {e}")

                # F2: 清算そのものは成功しても、記録・通知・停止フラグ保存の
                # いずれかが失敗したら素通りにせず、通知して例外を送出する
                # （以前は liquidation_ok のとき extra_failures があっても
                # ここを通らず、静かに正常終了していた）。
                if not liquidation_ok or extra_failures:
                    if not liquidation_ok:
                        if liquidation_error:
                            detail = str(liquidation_error)
                        else:
                            detail = "; ".join(f"{r['symbol']}: {r.get('error', r['status'])}" for r in failed)
                    else:
                        detail = "liquidation orders submitted successfully"
                    if extra_failures:
                        detail += " | also: " + "; ".join(extra_failures)

                    print(f"[EMERGENCY] Liquidation failed or incomplete: {detail}")
                    try:
                        await _notifier.notify_alert(f"Emergency liquidation failed or incomplete: {detail}", "error")
                    except Exception as e:
                        print(f"[Discord] Failure alert failed: {e}")
                        detail += f" | also: alert failed: {e}"
                    # 清算失敗・記録/通知/停止フラグ書き込み失敗のどれが
                    # 起きても、最後に必ずこれを送出する（E2/F2）。
                    raise EmergencyLiquidationFailed(detail)
    except EmergencyLiquidationFailed:
        raise
    except Exception as e:
        # get_account() などここまでの処理自体が失敗したケース。ログ・通知の
        # うえで再送出し、Lambda の Errors メトリクスに載せる（E2）。
        print(f"[Emergency] Check failed: {e}")
        try:
            await _notifier.notify_alert(f"Emergency check failed: {e}", "error")
        except Exception as notify_err:
            print(f"[Discord] Failure alert failed: {notify_err}")
        raise
