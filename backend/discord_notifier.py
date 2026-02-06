"""Discord Webhook通知モジュール - パイプライン全ステージ対応"""
import os
import httpx
from datetime import datetime


# ステージ別カラー
COLOR_GROK = 0x1DA1F2      # Twitter blue
COLOR_OPUS = 0xD97706      # Opus orange
COLOR_BLOCKED = 0xEF4444   # Red
COLOR_ADJUSTED = 0xF59E0B  # Yellow
COLOR_EXECUTED = 0x22C55E  # Green
COLOR_SKIP = 0x6B7280      # Gray
COLOR_ALERT = 0xEF4444     # Red
COLOR_INFO = 0x3B82F6      # Blue


class DiscordNotifier:
    def __init__(self):
        self.webhook_trades = os.getenv("DISCORD_WEBHOOK_TRADES")
        self.webhook_alerts = os.getenv("DISCORD_WEBHOOK_ALERTS")

    async def _send_embed(self, webhook_url: str, embed: dict):
        """Webhook にembed送信"""
        if not webhook_url:
            print(f"[Discord] Webhook URL not set, skipping embed")
            return

        payload = {"embeds": [embed]}
        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(webhook_url, json=payload)
                response.raise_for_status()
            except Exception as e:
                print(f"[Discord] Error sending embed: {e}")

    async def _send(self, webhook_url: str, content: str):
        """Webhook にテキスト送信（後方互換）"""
        if not webhook_url:
            print(f"[Discord] Webhook URL not set, skipping: {content}")
            return

        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(webhook_url, json={"content": content})
                response.raise_for_status()
            except Exception as e:
                print(f"[Discord] Error sending message: {e}")

    async def send_pipeline_log(self, cycle_id: str, stage: str, data: dict):
        """パイプラインの各ステージをDiscordにログ"""
        embed = {
            "title": f"Cycle {cycle_id[:8]}",
            "timestamp": datetime.utcnow().isoformat(),
            "fields": [],
        }

        if stage == "grok":
            embed["color"] = COLOR_GROK
            sentiment = data.get("sentiment", {})
            embed["fields"].append({
                "name": "Grok Report",
                "value": (
                    f"Sentiment: {sentiment.get('overall', 0)}\n"
                    f"Change: {'Yes' if data.get('significant_change') else 'No'}\n"
                    f"News: {len(data.get('breaking_news', []))} items"
                ),
            })
            if data.get("_warning"):
                embed["fields"].append({
                    "name": "Warning",
                    "value": data["_warning"],
                })

        elif stage == "opus_decision":
            embed["color"] = COLOR_OPUS
            action_emoji = {"buy": "BUY", "sell": "SELL", "hold": "HOLD"}
            embed["fields"].append({
                "name": "Opus Decision",
                "value": (
                    f"{action_emoji.get(data.get('action', ''), '?')} "
                    f"{data.get('symbol', '')}\n"
                    f"Size: {data.get('position_size_pct', 0)}% | "
                    f"Confidence: {data.get('confidence', 0)}%\n"
                    f"Risk: {data.get('risk_assessment', 'unknown')}\n"
                    f"Reason: {data.get('reasoning', '')}"
                ),
            })
            if data.get("adjustments"):
                adj_text = "\n".join([
                    f"{a['field']}: {a['original']} -> {a['adjusted']} ({a['reason']})"
                    for a in data["adjustments"]
                ])
                embed["fields"].append({
                    "name": "Opus Self-Adjustments",
                    "value": adj_text,
                })

        elif stage == "risk_guard":
            if not data.get("passed"):
                embed["color"] = COLOR_BLOCKED
                embed["fields"].append({
                    "name": "Risk Guard BLOCKED",
                    "value": data.get("reason", "unknown"),
                })
            elif data.get("adjustments"):
                embed["color"] = COLOR_ADJUSTED
                adj_text = "\n".join([
                    f"{a['field']}: {a['original']} -> {a['adjusted']} ({a['reason']})"
                    for a in data["adjustments"]
                ])
                embed["fields"].append({
                    "name": "Risk Guard Adjustments",
                    "value": adj_text,
                })

        elif stage == "execution":
            embed["color"] = COLOR_EXECUTED
            embed["fields"].append({
                "name": "Executed",
                "value": (
                    f"Order: {data.get('alpaca_order_id', 'N/A')}\n"
                    f"Status: {data.get('status', 'unknown')}"
                ),
            })

        elif stage == "skip":
            embed["color"] = COLOR_SKIP
            embed["fields"].append({
                "name": "Opus Skipped",
                "value": "Grok: no significant change",
            })

        await self._send_embed(self.webhook_trades, embed)

    # --- 後方互換メソッド ---

    async def notify_trade(self, symbol: str, action: str, quantity: float, price: float):
        """取引通知（後方互換）"""
        emoji = "BUY" if action == "buy" else "SELL"
        content = f"**{emoji}** {symbol} x{quantity} @ ${price:.2f}"
        await self._send(self.webhook_trades, content)

    async def notify_alert(self, message: str, level: str = "warning"):
        """アラート通知"""
        embed = {
            "color": COLOR_ALERT if level == "error" else COLOR_INFO,
            "title": level.upper(),
            "description": message,
            "timestamp": datetime.utcnow().isoformat(),
        }
        await self._send_embed(self.webhook_alerts, embed)

    async def notify_system_stop(self, reason: str):
        """システム停止通知"""
        embed = {
            "color": COLOR_BLOCKED,
            "title": "SYSTEM STOPPED",
            "description": reason,
            "timestamp": datetime.utcnow().isoformat(),
        }
        await self._send_embed(self.webhook_alerts, embed)

    async def notify_daily_summary(self, pnl: float, trade_count: int, win_rate: float):
        """日次サマリー"""
        embed = {
            "color": COLOR_EXECUTED if pnl >= 0 else COLOR_BLOCKED,
            "title": "Daily Summary",
            "fields": [
                {"name": "P&L", "value": f"${pnl:+.2f}", "inline": True},
                {"name": "Trades", "value": str(trade_count), "inline": True},
                {"name": "Win Rate", "value": f"{win_rate:.1%}", "inline": True},
            ],
            "timestamp": datetime.utcnow().isoformat(),
        }
        await self._send_embed(self.webhook_trades, embed)
