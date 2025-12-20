"""Grok Trade Bot - メインエントリーポイント"""
import os
import asyncio
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from fastapi import FastAPI
from contextlib import asynccontextmanager

from grok_client import GrokClient
from risk_guard import RiskGuard
from trader import Trader
from discord_notifier import DiscordNotifier


# グローバルインスタンス
grok = GrokClient()
guard = RiskGuard()
trader = Trader()
notifier = DiscordNotifier()
scheduler = AsyncIOScheduler(timezone="America/New_York")

# 監視対象銘柄
WATCHLIST = ["MSTR", "TSLA", "QQQ", "SPY"]


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
        else:
            print("[Order] Failed to execute")
            await notifier.notify_alert(f"Order failed: {symbol}", "error")

    except Exception as e:
        print(f"[Error] Trading cycle failed: {e}")
        await notifier.notify_alert(f"Trading error: {e}", "error")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """アプリケーションライフサイクル"""
    # 起動時
    interval = int(os.getenv("TRADING_INTERVAL", "900"))  # デフォルト15分

    scheduler.add_job(
        trading_cycle,
        IntervalTrigger(seconds=interval),
        id="trading_cycle",
        name="Main trading cycle"
    )
    scheduler.start()

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
    await notifier.notify_system_stop("Manual stop via API")
    return {"status": "stopped"}


@app.post("/start")
async def start():
    """再開"""
    scheduler.resume()
    await notifier.notify_alert("🔄 Bot resumed", "info")
    return {"status": "running"}


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run(app, host="0.0.0.0", port=port)
