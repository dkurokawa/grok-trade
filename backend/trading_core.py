"""Core trading pipeline (one run per invocation).

Port of the Grok -> Opus 4.6 -> Risk Guard -> Alpaca pipeline from the old
APScheduler-driven main.py. EventBridge Scheduler now provides the cadence:
  - trading_cycle   : every 30 min during market hours (mon-fri 9:00-15:30 ET)
  - emergency_check : every 5 min during market hours

"Paused" state is persisted in DynamoDB (STATE/scheduler_running) instead of an
in-process scheduler flag, so it survives across stateless Lambda invocations.

Stage 2 is selectable with DECISION_ENGINE:
  - grok : Grok decides directly from its own market report (default)
  - opus : Claude Opus decides from Grok's report (the original design)
Both return the same TradeDecision shape, so Risk Guard and execution are shared.
"""
import uuid
from datetime import datetime

from config import decision_engine, missing_required
from db import get_scheduler_state, log_pipeline, log_trade, set_scheduler_state
from discord_notifier import DiscordNotifier
from grok_client import GrokClient
from grok_validator import validate_grok_report
from opus_client import OpusClient
from risk_guard import RiskGuard
from skip_logic import should_skip_opus
from trader import Trader

# Clients are built on first use rather than at import: Alpaca and the model
# SDKs raise when their keys are absent, and failing at import turns a
# missing-secret problem into an opaque "module could not be loaded" error.
grok = None
opus = None
guard = None
trader = None
notifier = None


def _init_clients():
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


async def _alert_startup_failure(job: str, error: Exception):
    """_ready() raised, so `notifier` may not have been constructed yet."""
    print(f"[Error] {job} startup failed: {error}")
    try:
        await (notifier or DiscordNotifier()).notify_alert(
            f"{job} startup failed: {error}", "error"
        )
    except Exception as e:  # noqa: BLE001 - alerting must not mask the original error
        print(f"[Discord] Startup failure alert failed: {e}")


async def trading_cycle():
    """メイン取引サイクル（30分ごと・4ステージパイプライン）"""
    # _ready() constructs the API clients, which raise on missing/invalid keys.
    # Outside this try, such a failure would end the cycle with no alert at all.
    try:
        if not _ready("trading cycle"):
            return
    except Exception as e:  # noqa: BLE001
        await _alert_startup_failure("Trading cycle", e)
        return

    cycle_id = str(uuid.uuid4())
    engine = decision_engine()
    print(f"\n{'='*50}")
    print(f"[{datetime.now()}] Cycle {cycle_id[:8]} started (decision engine: {engine})")
    print("=" * 50)

    try:
        # === アカウント・市場データ取得 ===
        account = trader.get_account()
        positions = trader.get_positions()
        daily_pnl = account["daily_pnl"]

        print(f"Cash: ${account['cash']:,.2f} | Daily P&L: ${daily_pnl:+,.2f} | Positions: {len(positions)}")

        # システム健全性チェック
        health = guard.check_system_health(daily_pnl)
        if not health.allowed:
            print(f"[RiskGuard] System stopped: {health.reason}")
            await notifier.notify_system_stop(health.reason)
            set_scheduler_state(False)  # 以降のサイクルを停止
            return

        market_data = trader.get_market_data(WATCHLIST)
        if not market_data:
            print("[Trader] No market data available")
            return

        # === Stage 1: Grok 情報収集 ===
        grok_report, grok_latency = grok.collect_market_report(
            market_data=market_data,
            positions=positions,
        )

        if not grok_report:
            print("[Grok] No report returned")
            return

        grok_report = validate_grok_report(grok_report)
        try:
            await notifier.send_pipeline_log(cycle_id, "grok", grok_report)
        except Exception as e:
            print(f"[Discord] Grok log failed: {e}")

        print(f"[Grok] Sentiment: {grok_report['sentiment']['overall']} | "
              f"Change: {grok_report['significant_change']} | "
              f"Latency: {grok_latency}ms")

        # ログ共通項目
        base = {
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
                await notifier.send_pipeline_log(cycle_id, "skip", {})
            except Exception as e:
                print(f"[Discord] Skip log failed: {e}")
            log_pipeline(**base, opus_skipped=True)
            return

        # === Stage 2: 売買判断（Grok または Opus） ===
        decide = opus.analyze if engine == "opus" else grok.decide
        decision, decision_latency = decide(
            balance=account["cash"],
            positions=positions,
            daily_pnl=daily_pnl,
            max_daily_loss=guard.max_daily_loss,
            price_data=market_data,
            grok_report=grok_report,
        )

        if not decision:
            print(f"[{engine.capitalize()}] No decision returned")
            log_pipeline(**base, opus_latency_ms=decision_latency)
            return

        decision["decision_engine"] = engine
        try:
            await notifier.send_pipeline_log(cycle_id, "opus_decision", decision)
        except Exception as e:
            print(f"[Discord] Decision log failed: {e}")

        print(f"[{engine.capitalize()}] {decision['action'].upper()} {decision.get('symbol', '')} | "
              f"Confidence: {decision['confidence']}% | "
              f"Size: {decision.get('position_size_pct', 0)}% | "
              f"Latency: {decision_latency}ms")

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
        rg_result = guard.check(
            decision,
            {"daily_pnl": daily_pnl, "positions": positions},
            price=price,
            equity=account["equity"],
        )

        if rg_result.adjustments:
            try:
                await notifier.send_pipeline_log(cycle_id, "risk_guard", {
                    "passed": True, "adjustments": rg_result.adjustments,
                })
            except Exception as e:
                print(f"[Discord] RiskGuard adjustment log failed: {e}")
            print(f"[RiskGuard] Adjustments: {len(rg_result.adjustments)}")

        if not rg_result.allowed:
            try:
                await notifier.send_pipeline_log(cycle_id, "risk_guard", {
                    "passed": False, "reason": rg_result.reason,
                })
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
                rg_reason="hold",
                rg_adjustments=rg_result.adjustments,
            )
            return

        # symbol/price were already resolved before Stage 3 for RiskGuard.check().

        # 発注前の前提チェック（買いのみ）。どちらも満たさない注文は Alpaca に
        # 必ず拒否され、30分ごとに失敗通知が飛び続けるだけになる。
        if decision["action"] == "buy":
            skip_reason = None
            buying_power = account.get("buying_power", account["cash"])
            if symbol in trader.get_open_buy_order_symbols():
                skip_reason = "open_buy_order_pending"
            elif price and decision["quantity"] * price > buying_power:
                skip_reason = (
                    f"insufficient_buying_power: need ${decision['quantity'] * price:,.2f}, "
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

        order = trader.execute_order(
            symbol=symbol,
            action=decision["action"],
            quantity=decision["quantity"],
            order_type=decision.get("order_type", "market"),
            limit_price=decision.get("limit_price"),
            stop_loss=decision.get("stop_loss"),
            take_profit=decision.get("take_profit"),
        )

        if order:
            exec_result = {
                "alpaca_order_id": order["order_id"],
                "status": order["status"],
            }
            try:
                await notifier.send_pipeline_log(cycle_id, "execution", exec_result)
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
        else:
            print("[Order] Failed to execute")
            await notifier.notify_alert(f"Order failed: {symbol}", "error")
            log_pipeline(
                **base,
                rg_passed=True,
                rg_adjustments=rg_result.adjustments,
                order_submitted=False,
            )

    except Exception as e:
        print(f"[Error] Trading cycle failed: {e}")
        await notifier.notify_alert(f"Trading error: {e}", "error")


async def emergency_check():
    """5分間隔でドローダウン監視（AI不要）"""
    try:
        if not _ready("emergency check"):
            return
    except Exception as e:  # noqa: BLE001
        await _alert_startup_failure("Emergency check", e)
        return

    try:
        account = trader.get_account()
        equity = account["equity"]
        last_equity = account["last_equity"]

        if last_equity > 0:
            drawdown_pct = ((last_equity - equity) / last_equity) * 100
            if drawdown_pct > 5:
                print(f"[EMERGENCY] Drawdown {drawdown_pct:.1f}% > 5% → liquidating")
                trader.execute_emergency_liquidation()
                await notifier.send_pipeline_log("EMERGENCY", "risk_guard", {
                    "passed": False,
                    "reason": f"EMERGENCY STOP: drawdown {drawdown_pct:.1f}%",
                })
                set_scheduler_state(False)
    except Exception as e:
        print(f"[Emergency] Check failed: {e}")
