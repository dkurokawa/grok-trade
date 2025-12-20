"""Grok Trade Bot - メインエントリーポイント"""
import os
import asyncio
import json
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager

from grok_client import GrokClient
from risk_guard import RiskGuard
from trader import Trader
from discord_notifier import DiscordNotifier
from db import Trade, Decision, SystemState, init_db, get_session


# グローバルインスタンス
grok = GrokClient()
guard = RiskGuard()
trader = Trader()
notifier = DiscordNotifier()
scheduler = AsyncIOScheduler(timezone="America/New_York")

# 監視対象銘柄
WATCHLIST = ["MSTR", "TSLA", "QQQ", "SPY"]


def log_decision(market_context: dict, grok_response: str, parsed_action: dict, executed: bool, blocked_reason: str = None):
    """Grokの判断をDBに記録"""
    session = get_session()
    if not session:
        return
    try:
        decision = Decision(
            market_context=market_context,
            grok_response=grok_response,
            parsed_action=parsed_action,
            executed=executed,
            blocked_reason=blocked_reason
        )
        session.add(decision)
        session.commit()
        print("[DB] Decision logged")
    except Exception as e:
        print(f"[DB] Error logging decision: {e}")
        session.rollback()
    finally:
        session.close()


def log_trade(symbol: str, action: str, quantity: float, price: float, order_type: str, status: str, alpaca_order_id: str):
    """取引をDBに記録"""
    session = get_session()
    if not session:
        return
    try:
        trade = Trade(
            symbol=symbol,
            action=action,
            quantity=quantity,
            price=price,
            order_type=order_type,
            status=status,
            alpaca_order_id=alpaca_order_id
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
        return True  # デフォルトは起動
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
        print(f"[DB] Scheduler state saved: {running}")
    except Exception as e:
        print(f"[DB] Error setting scheduler state: {e}")
        session.rollback()
    finally:
        session.close()


async def trading_cycle():
    """メイン取引サイクル（15分ごと）"""
    print(f"\n{'='*50}")
    print(f"[{datetime.now()}] Trading cycle started")
    print('='*50)

    try:
        # 1. アカウント情報取得
        account = trader.get_account()
        positions = trader.get_positions()
        daily_pnl = account["daily_pnl"]

        print(f"Cash: ${account['cash']:,.2f}")
        print(f"Daily P&L: ${daily_pnl:+,.2f}")
        print(f"Positions: {len(positions)}")

        # 2. システム健全性チェック
        health = guard.check_system_health(daily_pnl)
        if not health.allowed:
            print(f"[RiskGuard] System stopped: {health.reason}")
            await notifier.notify_system_stop(health.reason)
            scheduler.pause()
            return

        # 3. 市場データ取得
        market_data = trader.get_market_data(WATCHLIST)
        if not market_data:
            print("[Trader] No market data available")
            return

        print(f"Market data: {list(market_data.keys())}")

        # 4. Grok分析
        decision = grok.analyze_market(
            balance=account["cash"],
            positions=positions,
            market_data=market_data
        )

        if not decision:
            print("[Grok] No decision returned")
            return

        print(f"[Grok] Decision: {decision['action']} {decision['symbol']} x{decision['quantity']}")
        print(f"[Grok] Confidence: {decision['confidence']}%")
        print(f"[Grok] Reasoning: {decision['reasoning']}")

        # 5. Hold なら終了
        if decision["action"] == "hold":
            print("[Action] Hold - no trade")
            # DBに決定を記録
            log_decision(
                market_context={"positions": positions, "market_data": market_data},
                grok_response=json.dumps(decision),
                parsed_action=decision,
                executed=False,
                blocked_reason="hold"
            )
            return

        # 6. 価格取得（リスクチェック用）
        symbol = decision["symbol"]
        price = market_data.get(symbol, {}).get("price", 0)
        if price == 0:
            print(f"[Error] No price for {symbol}")
            return

        # 7. リスクチェック
        risk_check = guard.check_order(
            action=decision["action"],
            symbol=symbol,
            quantity=decision["quantity"],
            price=price,
            account_balance=account["cash"],
            current_positions=positions,
            daily_pnl=daily_pnl
        )

        if not risk_check.allowed:
            print(f"[RiskGuard] Order blocked: {risk_check.reason}")
            await notifier.notify_alert(f"Order blocked: {risk_check.reason}", "warning")
            # DBに決定を記録
            log_decision(
                market_context={"positions": positions, "market_data": market_data},
                grok_response=json.dumps(decision),
                parsed_action=decision,
                executed=False,
                blocked_reason=risk_check.reason
            )
            return

        # 8. 注文実行
        print(f"[Execute] {decision['action'].upper()} {symbol} x{decision['quantity']}")

        order = trader.execute_order(
            symbol=symbol,
            action=decision["action"],
            quantity=decision["quantity"],
            order_type=decision.get("order_type", "market")
        )

        if order:
            print(f"[Order] ID: {order['order_id']}, Status: {order['status']}")
            await notifier.notify_trade(
                symbol=symbol,
                action=decision["action"],
                quantity=decision["quantity"],
                price=price
            )
            # DBに取引と決定を記録
            log_trade(
                symbol=symbol,
                action=decision["action"],
                quantity=decision["quantity"],
                price=price,
                order_type=decision.get("order_type", "market"),
                status=order["status"],
                alpaca_order_id=order["order_id"]
            )
            log_decision(
                market_context={"positions": positions, "market_data": market_data},
                grok_response=json.dumps(decision),
                parsed_action=decision,
                executed=True,
                blocked_reason=None
            )
        else:
            print("[Order] Failed to execute")
            await notifier.notify_alert(f"Order failed: {symbol}", "error")
            # DBに失敗した決定を記録
            log_decision(
                market_context={"positions": positions, "market_data": market_data},
                grok_response=json.dumps(decision),
                parsed_action=decision,
                executed=False,
                blocked_reason="order_execution_failed"
            )

    except Exception as e:
        print(f"[Error] Trading cycle failed: {e}")
        await notifier.notify_alert(f"Trading error: {e}", "error")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """アプリケーションライフサイクル"""
    # DB初期化
    init_db()

    # 起動時
    interval = int(os.getenv("TRADING_INTERVAL", "900"))  # デフォルト15分

    scheduler.add_job(
        trading_cycle,
        IntervalTrigger(seconds=interval),
        id="trading_cycle",
        name="Main trading cycle"
    )
    scheduler.start()

    # 前回の状態を復元
    if not get_scheduler_state():
        print("[DB] Restoring paused state from previous session")
        scheduler.pause()
    else:
        print(f"🚀 Grok Trade Bot started (interval: {interval}s)")
        await notifier.notify_alert("🚀 Grok Trade Bot started!", "info")
        # 初回実行
        await trading_cycle()

    yield

    # 終了時
    scheduler.shutdown()
    print("Bot stopped")


# FastAPIアプリ（ヘルスチェック用）
app = FastAPI(lifespan=lifespan)

# CORS設定（Dashboard からのアクセスを許可）
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
        "timestamp": datetime.now().isoformat()
    }


@app.get("/status")
async def status():
    account = trader.get_account()
    positions = trader.get_positions()
    return {
        "account": account,
        "positions": positions,
        "scheduler_running": scheduler.running
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
    await notifier.notify_alert("🔄 Bot resumed", "info")
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
                    "symbol": t.symbol,
                    "action": t.action,
                    "quantity": t.quantity,
                    "price": t.price,
                    "order_type": t.order_type,
                    "status": t.status,
                    "alpaca_order_id": t.alpaca_order_id
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
    """Grok判断履歴取得"""
    session = get_session()
    if not session:
        return {"decisions": [], "error": "Database not available"}
    try:
        decisions = session.query(Decision).order_by(Decision.timestamp.desc()).limit(limit).all()
        return {
            "decisions": [
                {
                    "id": d.id,
                    "timestamp": d.timestamp.isoformat() if d.timestamp else None,
                    "parsed_action": d.parsed_action,
                    "executed": d.executed,
                    "blocked_reason": d.blocked_reason
                }
                for d in decisions
            ]
        }
    except Exception as e:
        return {"decisions": [], "error": str(e)}
    finally:
        session.close()


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run(app, host="0.0.0.0", port=port)
