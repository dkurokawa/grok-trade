"""Discord Notifier unit tests - pipeline embed + legacy notification tests"""
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from discord_notifier import DiscordNotifier


class TestDiscordNotifierInit:
    def test_init_with_env_vars(self):
        with patch.dict(os.environ, {
            "DISCORD_WEBHOOK_TRADES": "https://discord.com/trades",
            "DISCORD_WEBHOOK_ALERTS": "https://discord.com/alerts",
        }):
            notifier = DiscordNotifier()
            assert notifier.webhook_trades == "https://discord.com/trades"
            assert notifier.webhook_alerts == "https://discord.com/alerts"

    def test_init_without_env_vars(self):
        with patch.dict(os.environ, {}, clear=True):
            notifier = DiscordNotifier()
            assert notifier.webhook_trades is None
            assert notifier.webhook_alerts is None


class TestSendEmbed:
    @pytest.fixture
    def notifier(self):
        with patch.dict(os.environ, {
            "DISCORD_WEBHOOK_TRADES": "https://discord.com/trades",
            "DISCORD_WEBHOOK_ALERTS": "https://discord.com/alerts",
        }):
            return DiscordNotifier()

    @pytest.mark.asyncio
    async def test_send_embed_success(self, notifier):
        with patch("discord_notifier.httpx.AsyncClient") as mock_client:
            mock_response = MagicMock()
            mock_response.raise_for_status = MagicMock()
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(return_value=mock_response)

            await notifier._send_embed("https://discord.com/test", {"title": "Test"})
            mock_client.return_value.__aenter__.return_value.post.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_embed_no_url(self, notifier):
        with patch("discord_notifier.httpx.AsyncClient") as mock_client:
            await notifier._send_embed(None, {"title": "Test"})
            mock_client.assert_not_called()


class TestSendPipelineLog:
    @pytest.fixture
    def notifier(self):
        with patch.dict(os.environ, {
            "DISCORD_WEBHOOK_TRADES": "https://discord.com/trades",
            "DISCORD_WEBHOOK_ALERTS": "https://discord.com/alerts",
        }):
            return DiscordNotifier()

    @pytest.mark.asyncio
    async def test_grok_stage(self, notifier):
        with patch.object(notifier, "_send_embed", new_callable=AsyncMock) as mock:
            data = {
                "sentiment": {"overall": 45},
                "significant_change": True,
                "breaking_news": ["Fed decision"],
            }
            await notifier.send_pipeline_log("abc12345-uuid", "grok", data)
            mock.assert_called_once()
            embed = mock.call_args.args[1]
            assert embed["color"] == 0x1DA1F2
            assert any("Grok Report" in f["name"] for f in embed["fields"])

    @pytest.mark.asyncio
    async def test_grok_stage_with_warning(self, notifier):
        with patch.object(notifier, "_send_embed", new_callable=AsyncMock) as mock:
            data = {
                "sentiment": {"overall": 95},
                "significant_change": True,
                "breaking_news": [],
                "_warning": "extreme_sentiment_may_be_hallucination",
            }
            await notifier.send_pipeline_log("abc12345", "grok", data)
            embed = mock.call_args.args[1]
            assert any("Warning" in f["name"] for f in embed["fields"])

    @pytest.mark.asyncio
    async def test_opus_decision_stage(self, notifier):
        with patch.object(notifier, "_send_embed", new_callable=AsyncMock) as mock:
            data = {
                "action": "buy",
                "symbol": "NVDA",
                "position_size_pct": 30,
                "confidence": 75,
                "risk_assessment": "medium",
                "reasoning": "bullish momentum",
                "adjustments": [],
            }
            await notifier.send_pipeline_log("abc12345", "opus_decision", data)
            embed = mock.call_args.args[1]
            assert embed["color"] == 0xD97706
            assert any("Opus Decision" in f["name"] for f in embed["fields"])

    @pytest.mark.asyncio
    async def test_opus_decision_with_adjustments(self, notifier):
        with patch.object(notifier, "_send_embed", new_callable=AsyncMock) as mock:
            data = {
                "action": "buy",
                "symbol": "NVDA",
                "position_size_pct": 35,
                "confidence": 72,
                "risk_assessment": "medium",
                "reasoning": "test",
                "adjustments": [
                    {"field": "position_size_pct", "original": 50, "adjusted": 35, "reason": "VIX high"}
                ],
            }
            await notifier.send_pipeline_log("abc12345", "opus_decision", data)
            embed = mock.call_args.args[1]
            assert any("Self-Adjustments" in f["name"] for f in embed["fields"])

    @pytest.mark.asyncio
    async def test_risk_guard_blocked(self, notifier):
        with patch.object(notifier, "_send_embed", new_callable=AsyncMock) as mock:
            data = {"passed": False, "reason": "confidence_too_low: 35"}
            await notifier.send_pipeline_log("abc12345", "risk_guard", data)
            embed = mock.call_args.args[1]
            assert embed["color"] == 0xEF4444
            assert any("BLOCKED" in f["name"] for f in embed["fields"])

    @pytest.mark.asyncio
    async def test_risk_guard_adjusted(self, notifier):
        with patch.object(notifier, "_send_embed", new_callable=AsyncMock) as mock:
            data = {
                "passed": True,
                "adjustments": [
                    {"field": "position_size_pct", "original": 60, "adjusted": 50, "reason": "limit"}
                ],
            }
            await notifier.send_pipeline_log("abc12345", "risk_guard", data)
            embed = mock.call_args.args[1]
            assert embed["color"] == 0xF59E0B
            assert any("Adjustments" in f["name"] for f in embed["fields"])

    @pytest.mark.asyncio
    async def test_execution_stage(self, notifier):
        with patch.object(notifier, "_send_embed", new_callable=AsyncMock) as mock:
            data = {"alpaca_order_id": "order-123", "status": "filled"}
            await notifier.send_pipeline_log("abc12345", "execution", data)
            embed = mock.call_args.args[1]
            assert embed["color"] == 0x22C55E
            assert any("Executed" in f["name"] for f in embed["fields"])

    @pytest.mark.asyncio
    async def test_skip_stage(self, notifier):
        with patch.object(notifier, "_send_embed", new_callable=AsyncMock) as mock:
            await notifier.send_pipeline_log("abc12345", "skip", {})
            embed = mock.call_args.args[1]
            assert embed["color"] == 0x6B7280
            assert any("Skipped" in f["name"] for f in embed["fields"])


class TestNotifyTrade:
    @pytest.fixture
    def notifier(self):
        with patch.dict(os.environ, {
            "DISCORD_WEBHOOK_TRADES": "https://discord.com/trades",
            "DISCORD_WEBHOOK_ALERTS": "https://discord.com/alerts",
        }):
            return DiscordNotifier()

    @pytest.mark.asyncio
    async def test_notify_trade_buy(self, notifier):
        with patch.object(notifier, "_send", new_callable=AsyncMock) as mock_send:
            await notifier.notify_trade("MSTR", "buy", 10, 350.0)
            mock_send.assert_called_once()
            content = mock_send.call_args.args[1]
            assert "BUY" in content
            assert "MSTR" in content
            assert "350.00" in content

    @pytest.mark.asyncio
    async def test_notify_trade_sell(self, notifier):
        with patch.object(notifier, "_send", new_callable=AsyncMock) as mock_send:
            await notifier.notify_trade("TSLA", "sell", 5, 250.0)
            content = mock_send.call_args.args[1]
            assert "SELL" in content
            assert "TSLA" in content


class TestNotifyAlert:
    @pytest.fixture
    def notifier(self):
        with patch.dict(os.environ, {
            "DISCORD_WEBHOOK_TRADES": "https://discord.com/trades",
            "DISCORD_WEBHOOK_ALERTS": "https://discord.com/alerts",
        }):
            return DiscordNotifier()

    @pytest.mark.asyncio
    async def test_notify_alert(self, notifier):
        with patch.object(notifier, "_send_embed", new_callable=AsyncMock) as mock:
            await notifier.notify_alert("Risk limit reached", "warning")
            mock.assert_called_once()
            embed = mock.call_args.args[1]
            assert "Risk limit reached" in embed["description"]

    @pytest.mark.asyncio
    async def test_notify_alert_uses_alerts_webhook(self, notifier):
        with patch.object(notifier, "_send_embed", new_callable=AsyncMock) as mock:
            await notifier.notify_alert("Test", "error")
            webhook = mock.call_args.args[0]
            assert webhook == "https://discord.com/alerts"


class TestNotifySystemStop:
    @pytest.fixture
    def notifier(self):
        with patch.dict(os.environ, {
            "DISCORD_WEBHOOK_TRADES": "https://discord.com/trades",
            "DISCORD_WEBHOOK_ALERTS": "https://discord.com/alerts",
        }):
            return DiscordNotifier()

    @pytest.mark.asyncio
    async def test_notify_system_stop(self, notifier):
        with patch.object(notifier, "_send_embed", new_callable=AsyncMock) as mock:
            await notifier.notify_system_stop("Daily loss limit reached")
            embed = mock.call_args.args[1]
            assert "SYSTEM STOPPED" in embed["title"]
            assert "Daily loss limit reached" in embed["description"]


class TestNotifyDailySummary:
    @pytest.fixture
    def notifier(self):
        with patch.dict(os.environ, {
            "DISCORD_WEBHOOK_TRADES": "https://discord.com/trades",
        }):
            return DiscordNotifier()

    @pytest.mark.asyncio
    async def test_positive_pnl(self, notifier):
        with patch.object(notifier, "_send_embed", new_callable=AsyncMock) as mock:
            await notifier.notify_daily_summary(pnl=500.0, trade_count=10, win_rate=0.7)
            embed = mock.call_args.args[1]
            assert embed["color"] == 0x22C55E
            assert any("+500.00" in f["value"] for f in embed["fields"])

    @pytest.mark.asyncio
    async def test_negative_pnl(self, notifier):
        with patch.object(notifier, "_send_embed", new_callable=AsyncMock) as mock:
            await notifier.notify_daily_summary(pnl=-300.0, trade_count=5, win_rate=0.4)
            embed = mock.call_args.args[1]
            assert embed["color"] == 0xEF4444
