"""Grok Trade Bot - Grok → Opus 4.6 → Alpaca パイプライン"""
import os
import sentry_sdk
from sentry_sdk.integrations.fastapi import FastApiIntegration

# Sentry初期化（他のimportより前に実行）
sentry_sdk.init(
    dsn=os.environ.get("SENTRY_DSN"),
    environment=os.environ.get("ENVIRONMENT", "development"),
    traces_sample_rate=0.1,
    integrations=[FastApiIntegration()],
)

import asyncio
import json
import uuid
import time
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager

from grok_client import GrokClient
from opus_client import OpusClient
from grok_validator import validate_grok_report
from skip_logic import should_skip_opus
from risk_guard import RiskGuard
from trader import Trader
from discord_notifier import DiscordNotifier
from db import Trade, Decision, PipelineLog, SystemState, init_db, get_session


# グローバルインスタンス
grok = GrokClient()
opus = OpusClient()
guard = RiskGuard()
trader = Trader()
notifier = DiscordNotifier()
scheduler = AsyncIOScheduler(timezone="America/New_York")

# 監視対象銘柄
WATCHLIST = ["MSTR", "TSLA", "QQQ", "SPY"]


def log_pipeline(
    cycle_id: str,
    grok_input: dict = None,
    grok_output: dict = None,
    grok_latency_ms: int = None,
    opus_skipped: bool = False,
    opus_input: dict = None,
    opus_output: dict = None,
    opus_latency_ms: int = None,
    opus_adjustments: list = None,
    rg_passed: bool = None,
    rg_reason: str = None,
    rg_adjustments: list = None,
    order_submitted: bool = False,
    alpaca_order_id: str = None,
    execution_result: dict = None,
):
    """パイプライン全体をDBに記録"""
    session = get_session()
    if not session:
        return
    try:
        log = PipelineLog(
            cycle_id=cycle_id,
            grok_input=grok_input,
            grok_output=grok_output,
            grok_latency_ms=grok_latency_ms,
            opus_skipped=opus_skipped,
            opus_input=opus_input,
            opus_output=opus_output,
            opus_latency_ms=opus_latency_ms,
            opus_adjustments=opus_adjustments or [],
            risk_guard_passed=rg_passed,
            risk_guard_reason=rg_reason,
            risk_guard_adjustments=rg_adjustments or [],
            order_submitted=order_submitted,
            alpaca_order_id=alpaca_order_id,
            execution_result=execution_result,
        )
        session.add(log)
        session.commit()
        print(f"[DB] Pipeline log saved: {cycle_id[:8]}")
    except Exception as e:
        print(f"[DB] Error logging pipeline: {e}")
        session.rollback()
    finally:
        session.close()


def log_trade(
    cycle_id: str, symbol: str, action: str, quantity: float,
    price: float, order_type: str, status: str, alpaca_order_id: str,
    stop_loss: float = None, take_profit: float = None,
):
    """取引をDBに記録"""
    session = get_session()
    if not session:
        return
    try:
        trade = Trade(
            cycle_id=cycle_id,
            symbol=symbol,
            action=action,
            quantity=quantity,
            price=price,
            order_type=order_type,
            status=status,
            alpaca_order_id=alpaca_order_id,
            stop_loss=stop_loss,
            take_profit=take_profit,
        )
        session.add(trade)
        session.commit()
        print("[DB] Trade logged")
    except Exception as e:
        print(f"[DB] Error logging trade: {e}")
        session.rollback()
    finally:
        session.close()


def get_scheduler_state() -> bool:
    """DB からスケジューラー状態を取得"""
    session = get_session()
    if not session:
        return True
    try:
        state = session.query(SystemState).filter_by(key="scheduler_running").first()
        if state:
            return state.value.get("running", True)
        return True
    except Exception as e:
        print(f"[DB] Error getting scheduler state: {e}")
        return True
    finally:
        session.close()


def set_scheduler_state(running: bool):
    """DB にスケジューラー状態を保存"""
    session = get_session()
    if not session:
        return
    try:
        state = session.query(SystemState).filter_by(key="scheduler_running").first()
        if state:
            state.value = {"running": running}
            state.updated_at = datetime.utcnow()
        else:
            state = SystemState(key="scheduler_running", value={"running": running})
            session.add(state)
        session.commit()
    except Exception as e:
        print(f"[DB] Error setting scheduler state: {e}")
        session.rollback()
    finally:
        session.close()


async def trading_cycle():
    """メイン取引サイクル（30分ごと・4ステージパイプライン）"""
    cycle_id = str(uuid.uuid4())
    print(f"\n{'='*50}")
    print(f"[{datetime.now()}] Cycle {cycle_id[:8]} started")
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
            scheduler.pause()
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

        # === Opusスキップ判定 ===
        if should_skip_opus(grok_report):
            print("[Skip] No significant change → Opus skipped")
            try:
                await notifier.send_pipeline_log(cycle_id, "skip", {})
            except Exception as e:
                print(f"[Discord] Skip log failed: {e}")
            log_pipeline(
                cycle_id=cycle_id,
                grok_input={"market_data": market_data},
                grok_output=grok_report,
                grok_latency_ms=grok_latency,
                opus_skipped=True,
            )
            return

        # === Stage 2: Opus 4.6 判断 ===
        max_daily_loss = guard.max_daily_loss
        decision, opus_latency = opus.analyze(
            balance=account["cash"],
            positions=positions,
            daily_pnl=daily_pnl,
            max_daily_loss=max_daily_loss,
            price_data=market_data,
            grok_report=grok_report,
        )

        if not decision:
            print("[Opus] No decision returned")
            log_pipeline(
                cycle_id=cycle_id,
                grok_input={"market_data": market_data},
                grok_output=grok_report,
                grok_latency_ms=grok_latency,
                opus_latency_ms=opus_latency,
            )
            return

        try:
            await notifier.send_pipeline_log(cycle_id, "opus_decision", decision)
        except Exception as e:
            print(f"[Discord] Opus log failed: {e}")

        print(f"[Opus] {decision['action'].upper()} {decision.get('symbol', '')} | "
              f"Confidence: {decision['confidence']}% | "
              f"Size: {decision.get('position_size_pct', 0)}% | "
              f"Latency: {opus_latency}ms")

        # === Stage 3: Risk Guard ===
        portfolio = {"daily_pnl": daily_pnl}
        rg_result = guard.check(decision, portfolio)

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
                cycle_id=cycle_id,
                grok_input={"market_data": market_data},
                grok_output=grok_report,
                grok_latency_ms=grok_latency,
                opus_output=decision,
                opus_latency_ms=opus_latency,
                opus_adjustments=decision.get("adjustments", []),
                rg_passed=False,
                rg_reason=rg_result.reason,
                rg_adjustments=rg_result.adjustments,
            )
            return

        # === Stage 4: Execute ===
        if decision["action"] == "hold":
            print("[Action] Hold - no trade")
            log_pipeline(
                cycle_id=cycle_id,
                grok_input={"market_data": market_data},
                grok_output=grok_report,
                grok_latency_ms=grok_latency,
                opus_output=decision,
                opus_latency_ms=opus_latency,
                opus_adjustments=decision.get("adjustments", []),
                rg_passed=True,
                rg_reason="hold",
                rg_adjustments=rg_result.adjustments,
            )
            return

        symbol = decision["symbol"]
        price = market_data.get(symbol, {}).get("price", 0)

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
                cycle_id=cycle_id,
                grok_input={"market_data": market_data},
                grok_output=grok_report,
                grok_latency_ms=grok_latency,
                opus_output=decision,
                opus_latency_ms=opus_latency,
                opus_adjustments=decision.get("adjustments", []),
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
                cycle_id=cycle_id,
                grok_input={"market_data": market_data},
                grok_output=grok_report,
                grok_latency_ms=grok_latency,
                opus_output=decision,
                opus_latency_ms=opus_latency,
                opus_adjustments=decision.get("adjustments", []),
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
        account = trader.get_account()
        equity = account["equity"]
        last_equity = account["last_equity"]

        if last_equity > 0:
            drawdown_pct = ((last_equity - equity) / last_equity) * 100
            if drawdown_pct > 5:
                print(f"[EMERGENCY] Drawdown {drawdown_pct:.1f}% > 5% → liquidating")
                results = trader.execute_emergency_liquidation()
                await notifier.send_pipeline_log("EMERGENCY", "risk_guard", {
                    "passed": False,
                    "reason": f"EMERGENCY STOP: drawdown {drawdown_pct:.1f}%",
                })
                scheduler.pause()
                set_scheduler_state(False)
    except Exception as e:
        print(f"[Emergency] Check failed: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """アプリケーションライフサイクル"""
    init_db()

    # 30分間隔（市場時間のみ：月-金 9:00-15:30 EST）
    scheduler.add_job(
        trading_cycle,
        CronTrigger(
            day_of_week="mon-fri",
            hour="9-15",
            minute="0,30",
            timezone="America/New_York",
        ),
        id="trading_cycle",
        name="Main trading cycle",
    )

    # 最後の判断（15:30）
    scheduler.add_job(
        trading_cycle,
        CronTrigger(
            day_of_week="mon-fri",
            hour="15",
            minute="30",
            timezone="America/New_York",
        ),
        id="trading_cycle_close",
        name="Market close trading cycle",
    )

    # 緊急チェック（5分間隔、市場時間のみ）
    scheduler.add_job(
        emergency_check,
        CronTrigger(
            day_of_week="mon-fri",
            hour="9-15",
            minute="*/5",
            timezone="America/New_York",
        ),
        id="emergency_check",
        name="Emergency drawdown check",
    )

    scheduler.start()

    # 前回の状態を復元
    if not get_scheduler_state():
        print("[DB] Restoring paused state from previous session")
        scheduler.pause()
    else:
        print("Grok → Opus 4.6 → Alpaca pipeline started (30min cron)")
        await notifier.notify_alert("Grok → Opus pipeline started!", "info")

    yield

    scheduler.shutdown()
    print("Bot stopped")


# FastAPIアプリ
app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 本番では Vercel URL に限定推奨
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "scheduler_running": scheduler.running,
        "timestamp": datetime.now().isoformat(),
    }


@app.get("/status")
async def status():
    account = trader.get_account()
    positions = trader.get_positions()
    return {
        "account": account,
        "positions": positions,
        "scheduler_running": scheduler.running,
    }


@app.post("/stop")
async def stop():
    """緊急停止"""
    scheduler.pause()
    set_scheduler_state(False)
    await notifier.notify_system_stop("Manual stop via API")
    return {"status": "stopped"}


@app.post("/start")
async def start():
    """再開"""
    scheduler.resume()
    set_scheduler_state(True)
    await notifier.notify_alert("Bot resumed", "info")
    return {"status": "running"}


@app.get("/trades")
async def get_trades(limit: int = 50):
    """取引履歴取得"""
    session = get_session()
    if not session:
        return {"trades": [], "error": "Database not available"}
    try:
        trades = session.query(Trade).order_by(Trade.timestamp.desc()).limit(limit).all()
        return {
            "trades": [
                {
                    "id": t.id,
                    "timestamp": t.timestamp.isoformat() if t.timestamp else None,
                    "cycle_id": t.cycle_id,
                    "symbol": t.symbol,
                    "action": t.action,
                    "quantity": t.quantity,
                    "price": t.price,
                    "order_type": t.order_type,
                    "status": t.status,
                    "alpaca_order_id": t.alpaca_order_id,
                    "stop_loss": t.stop_loss,
                    "take_profit": t.take_profit,
                }
                for t in trades
            ]
        }
    except Exception as e:
        return {"trades": [], "error": str(e)}
    finally:
        session.close()


@app.get("/decisions")
async def get_decisions(limit: int = 50):
    """パイプラインログ取得（pipeline_log 参照）"""
    session = get_session()
    if not session:
        return {"decisions": [], "error": "Database not available"}
    try:
        logs = session.query(PipelineLog).order_by(PipelineLog.timestamp.desc()).limit(limit).all()
        return {
            "decisions": [
                {
                    "id": d.id,
                    "cycle_id": d.cycle_id,
                    "timestamp": d.timestamp.isoformat() if d.timestamp else None,
                    "opus_skipped": d.opus_skipped,
                    "opus_output": d.opus_output,
                    "risk_guard_passed": d.risk_guard_passed,
                    "risk_guard_reason": d.risk_guard_reason,
                    "order_submitted": d.order_submitted,
                }
                for d in logs
            ]
        }
    except Exception as e:
        return {"decisions": [], "error": str(e)}
    finally:
        session.close()


@app.get("/pipeline")
async def get_pipeline_logs(limit: int = 50):
    """パイプライン全体ログ取得"""
    session = get_session()
    if not session:
        return {"logs": [], "error": "Database not available"}
    try:
        logs = session.query(PipelineLog).order_by(PipelineLog.timestamp.desc()).limit(limit).all()
        return {
            "logs": [
                {
                    "id": d.id,
                    "cycle_id": d.cycle_id,
                    "timestamp": d.timestamp.isoformat() if d.timestamp else None,
                    "grok_output": d.grok_output,
                    "grok_latency_ms": d.grok_latency_ms,
                    "opus_skipped": d.opus_skipped,
                    "opus_output": d.opus_output,
                    "opus_latency_ms": d.opus_latency_ms,
                    "opus_adjustments": d.opus_adjustments,
                    "risk_guard_passed": d.risk_guard_passed,
                    "risk_guard_reason": d.risk_guard_reason,
                    "risk_guard_adjustments": d.risk_guard_adjustments,
                    "order_submitted": d.order_submitted,
                    "alpaca_order_id": d.alpaca_order_id,
                    "execution_result": d.execution_result,
                }
                for d in logs
            ]
        }
    except Exception as e:
        return {"logs": [], "error": str(e)}
    finally:
        session.close()


@app.get("/debug/db")
async def debug_db():
    """Database connection debug info"""
    from db.models import DATABASE_URL, engine, SessionLocal
    db_url = os.getenv("DATABASE_URL", "")
    return {
        "database_url_set": bool(db_url),
        "database_url_length": len(db_url) if db_url else 0,
        "database_url_prefix": db_url[:25] + "..." if len(db_url) > 25 else db_url if db_url else "NOT SET",
        "engine_exists": engine is not None,
        "session_local_exists": SessionLocal is not None,
    }


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run(app, host="0.0.0.0", port=port)
