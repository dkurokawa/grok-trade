"""Discord Webhook通知モジュール"""
import os
import httpx
from datetime import datetime


class DiscordNotifier:
    def __init__(self):
        self.webhook_trades = os.getenv("DISCORD_WEBHOOK_TRADES")
        self.webhook_alerts = os.getenv("DISCORD_WEBHOOK_ALERTS")

    async def _send(self, webhook_url: str, content: str, embed: dict = None):
        """Webhookにメッセージ送信"""
        if not webhook_url:
            print(f"[Discord] Webhook URL not set, skipping: {content}")
            return

        payload = {"content": content}
        if embed:
            payload["embeds"] = [embed]

        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(webhook_url, json=payload)
                response.raise_for_status()
            except Exception as e:
                print(f"[Discord] Error sending message: {e}")

    async def notify_trade(self, symbol: str, action: str, quantity: float, price: float):
        """取引通知"""
        emoji = "🟢" if action == "buy" else "🔴"
        content = f"{emoji} **{action.upper()}** {symbol} x{quantity} @ ${price:.2f}"
        await self._send(self.webhook_trades, content)

    async def notify_alert(self, message: str, level: str = "warning"):
        """アラート通知"""
        emoji = {"warning": "⚠️", "error": "❌", "info": "ℹ️"}.get(level, "⚠️")
        content = f"{emoji} {message}"
        await self._send(self.webhook_alerts, content)

    async def notify_daily_summary(self, pnl: float, trade_count: int, win_rate: float):
        """日次サマリー"""
        emoji = "📈" if pnl >= 0 else "📉"
        content = (
            f"{emoji} **Daily Summary**\n"
            f"P&L: ${pnl:+.2f}\n"
            f"Trades: {trade_count}\n"
            f"Win Rate: {win_rate:.1%}"
        )
        await self._send(self.webhook_trades, content)

    async def notify_system_stop(self, reason: str):
        """システム停止通知"""
        content = f"🛑 **SYSTEM STOPPED**\nReason: {reason}"
        await self._send(self.webhook_alerts, content)


# テスト用
async def test_discord():
    from dotenv import load_dotenv
    load_dotenv()

    notifier = DiscordNotifier()
    await notifier.notify_alert("🚀 Grok Trade Bot started!", level="info")
    print("Test message sent!")


if __name__ == "__main__":
    import asyncio
    asyncio.run(test_discord())
