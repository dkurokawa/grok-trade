"""Discord Notifier unit tests - comprehensive webhook and notification tests"""
import pytest
import os
from unittest.mock import patch, MagicMock, AsyncMock
import httpx

from discord_notifier import DiscordNotifier


class TestDiscordNotifierInit:
    """Initialization tests"""

    def test_init_with_env_vars(self):
        """Test initialization with environment variables"""
        with patch.dict(os.environ, {
            "DISCORD_WEBHOOK_TRADES": "https://discord.com/trades",
            "DISCORD_WEBHOOK_ALERTS": "https://discord.com/alerts"
        }):
            notifier = DiscordNotifier()
            assert notifier.webhook_trades == "https://discord.com/trades"
            assert notifier.webhook_alerts == "https://discord.com/alerts"

    def test_init_without_env_vars(self):
        """Test initialization without environment variables"""
        with patch.dict(os.environ, {}, clear=True):
            notifier = DiscordNotifier()
            assert notifier.webhook_trades is None
            assert notifier.webhook_alerts is None

    def test_init_partial_env_vars(self):
        """Test initialization with only one webhook set"""
        with patch.dict(os.environ, {
            "DISCORD_WEBHOOK_TRADES": "https://discord.com/trades"
        }, clear=True):
            notifier = DiscordNotifier()
            assert notifier.webhook_trades == "https://discord.com/trades"
            assert notifier.webhook_alerts is None


class TestSendMethod:
    """_send method tests"""

    @pytest.fixture
    def notifier(self):
        with patch.dict(os.environ, {
            "DISCORD_WEBHOOK_TRADES": "https://discord.com/trades",
            "DISCORD_WEBHOOK_ALERTS": "https://discord.com/alerts"
        }):
            return DiscordNotifier()

    @pytest.mark.asyncio
    async def test_send_success(self, notifier):
        """Test successful message send"""
        with patch("discord_notifier.httpx.AsyncClient") as mock_client:
            mock_response = MagicMock()
            mock_response.raise_for_status = MagicMock()
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(return_value=mock_response)

            await notifier._send("https://discord.com/test", "Test message")

            mock_client.return_value.__aenter__.return_value.post.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_with_embed(self, notifier):
        """Test sending message with embed"""
        with patch("discord_notifier.httpx.AsyncClient") as mock_client:
            mock_response = MagicMock()
            mock_response.raise_for_status = MagicMock()
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(return_value=mock_response)

            embed = {"title": "Test", "description": "Test embed"}
            await notifier._send("https://discord.com/test", "Test message", embed)

            call_args = mock_client.return_value.__aenter__.return_value.post.call_args
            payload = call_args.kwargs["json"]
            assert "embeds" in payload
            assert payload["embeds"] == [embed]

    @pytest.mark.asyncio
    async def test_send_no_webhook_url(self, notifier):
        """Test sending with no webhook URL (should skip)"""
        with patch("discord_notifier.httpx.AsyncClient") as mock_client:
            await notifier._send(None, "Test message")
            mock_client.assert_not_called()

    @pytest.mark.asyncio
    async def test_send_empty_webhook_url(self, notifier):
        """Test sending with empty webhook URL"""
        with patch("discord_notifier.httpx.AsyncClient") as mock_client:
            await notifier._send("", "Test message")
            mock_client.assert_not_called()

    @pytest.mark.asyncio
    async def test_send_http_error(self, notifier):
        """Test handling HTTP error"""
        with patch("discord_notifier.httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(
                side_effect=httpx.HTTPStatusError("Error", request=MagicMock(), response=MagicMock())
            )

            # Should not raise, just print error
            await notifier._send("https://discord.com/test", "Test message")

    @pytest.mark.asyncio
    async def test_send_connection_error(self, notifier):
        """Test handling connection error"""
        with patch("discord_notifier.httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(
                side_effect=httpx.ConnectError("Connection failed")
            )

            # Should not raise, just print error
            await notifier._send("https://discord.com/test", "Test message")

    @pytest.mark.asyncio
    async def test_send_timeout_error(self, notifier):
        """Test handling timeout error"""
        with patch("discord_notifier.httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(
                side_effect=httpx.TimeoutException("Timeout")
            )

            # Should not raise, just print error
            await notifier._send("https://discord.com/test", "Test message")


class TestNotifyTrade:
    """notify_trade method tests"""

    @pytest.fixture
    def notifier(self):
        with patch.dict(os.environ, {
            "DISCORD_WEBHOOK_TRADES": "https://discord.com/trades",
            "DISCORD_WEBHOOK_ALERTS": "https://discord.com/alerts"
        }):
            return DiscordNotifier()

    @pytest.mark.asyncio
    async def test_notify_trade_buy(self, notifier):
        """Test buy trade notification"""
        with patch.object(notifier, "_send", new_callable=AsyncMock) as mock_send:
            await notifier.notify_trade("MSTR", "buy", 10, 350.0)

            mock_send.assert_called_once()
            call_args = mock_send.call_args
            content = call_args.args[1]
            assert "🟢" in content
            assert "BUY" in content
            assert "MSTR" in content
            assert "10" in content
            assert "350.00" in content

    @pytest.mark.asyncio
    async def test_notify_trade_sell(self, notifier):
        """Test sell trade notification"""
        with patch.object(notifier, "_send", new_callable=AsyncMock) as mock_send:
            await notifier.notify_trade("TSLA", "sell", 5, 250.0)

            mock_send.assert_called_once()
            call_args = mock_send.call_args
            content = call_args.args[1]
            assert "🔴" in content
            assert "SELL" in content
            assert "TSLA" in content

    @pytest.mark.asyncio
    async def test_notify_trade_uses_trades_webhook(self, notifier):
        """Test trade notification uses trades webhook"""
        with patch.object(notifier, "_send", new_callable=AsyncMock) as mock_send:
            await notifier.notify_trade("MSTR", "buy", 10, 350.0)

            call_args = mock_send.call_args
            webhook_url = call_args.args[0]
            assert webhook_url == "https://discord.com/trades"

    @pytest.mark.asyncio
    async def test_notify_trade_fractional_quantity(self, notifier):
        """Test trade with fractional quantity"""
        with patch.object(notifier, "_send", new_callable=AsyncMock) as mock_send:
            await notifier.notify_trade("MSTR", "buy", 0.5, 350.0)

            mock_send.assert_called_once()
            content = mock_send.call_args.args[1]
            assert "0.5" in content

    @pytest.mark.asyncio
    async def test_notify_trade_large_price(self, notifier):
        """Test trade with large price"""
        with patch.object(notifier, "_send", new_callable=AsyncMock) as mock_send:
            await notifier.notify_trade("BRK.A", "buy", 1, 500000.0)

            mock_send.assert_called_once()
            content = mock_send.call_args.args[1]
            assert "500000.00" in content


class TestNotifyAlert:
    """notify_alert method tests"""

    @pytest.fixture
    def notifier(self):
        with patch.dict(os.environ, {
            "DISCORD_WEBHOOK_TRADES": "https://discord.com/trades",
            "DISCORD_WEBHOOK_ALERTS": "https://discord.com/alerts"
        }):
            return DiscordNotifier()

    @pytest.mark.asyncio
    async def test_notify_alert_warning(self, notifier):
        """Test warning alert"""
        with patch.object(notifier, "_send", new_callable=AsyncMock) as mock_send:
            await notifier.notify_alert("Risk limit reached", "warning")

            mock_send.assert_called_once()
            content = mock_send.call_args.args[1]
            assert "⚠️" in content
            assert "Risk limit reached" in content

    @pytest.mark.asyncio
    async def test_notify_alert_error(self, notifier):
        """Test error alert"""
        with patch.object(notifier, "_send", new_callable=AsyncMock) as mock_send:
            await notifier.notify_alert("API connection failed", "error")

            mock_send.assert_called_once()
            content = mock_send.call_args.args[1]
            assert "❌" in content

    @pytest.mark.asyncio
    async def test_notify_alert_info(self, notifier):
        """Test info alert"""
        with patch.object(notifier, "_send", new_callable=AsyncMock) as mock_send:
            await notifier.notify_alert("Bot started", "info")

            mock_send.assert_called_once()
            content = mock_send.call_args.args[1]
            assert "ℹ️" in content

    @pytest.mark.asyncio
    async def test_notify_alert_default_level(self, notifier):
        """Test alert with default level"""
        with patch.object(notifier, "_send", new_callable=AsyncMock) as mock_send:
            await notifier.notify_alert("Unknown event")

            mock_send.assert_called_once()
            content = mock_send.call_args.args[1]
            assert "⚠️" in content  # default is warning

    @pytest.mark.asyncio
    async def test_notify_alert_unknown_level(self, notifier):
        """Test alert with unknown level falls back to warning"""
        with patch.object(notifier, "_send", new_callable=AsyncMock) as mock_send:
            await notifier.notify_alert("Event", "unknown_level")

            content = mock_send.call_args.args[1]
            assert "⚠️" in content

    @pytest.mark.asyncio
    async def test_notify_alert_uses_alerts_webhook(self, notifier):
        """Test alert notification uses alerts webhook"""
        with patch.object(notifier, "_send", new_callable=AsyncMock) as mock_send:
            await notifier.notify_alert("Test", "warning")

            webhook_url = mock_send.call_args.args[0]
            assert webhook_url == "https://discord.com/alerts"


class TestNotifyDailySummary:
    """notify_daily_summary method tests"""

    @pytest.fixture
    def notifier(self):
        with patch.dict(os.environ, {
            "DISCORD_WEBHOOK_TRADES": "https://discord.com/trades",
            "DISCORD_WEBHOOK_ALERTS": "https://discord.com/alerts"
        }):
            return DiscordNotifier()

    @pytest.mark.asyncio
    async def test_notify_daily_summary_positive(self, notifier):
        """Test positive daily summary"""
        with patch.object(notifier, "_send", new_callable=AsyncMock) as mock_send:
            await notifier.notify_daily_summary(pnl=500.0, trade_count=10, win_rate=0.7)

            mock_send.assert_called_once()
            content = mock_send.call_args.args[1]
            assert "📈" in content
            assert "+500.00" in content
            assert "10" in content
            assert "70.0%" in content

    @pytest.mark.asyncio
    async def test_notify_daily_summary_negative(self, notifier):
        """Test negative daily summary"""
        with patch.object(notifier, "_send", new_callable=AsyncMock) as mock_send:
            await notifier.notify_daily_summary(pnl=-300.0, trade_count=5, win_rate=0.4)

            mock_send.assert_called_once()
            content = mock_send.call_args.args[1]
            assert "📉" in content
            assert "-300.00" in content

    @pytest.mark.asyncio
    async def test_notify_daily_summary_zero(self, notifier):
        """Test zero P&L daily summary"""
        with patch.object(notifier, "_send", new_callable=AsyncMock) as mock_send:
            await notifier.notify_daily_summary(pnl=0.0, trade_count=0, win_rate=0.0)

            content = mock_send.call_args.args[1]
            assert "📈" in content  # zero is considered non-negative

    @pytest.mark.asyncio
    async def test_notify_daily_summary_uses_trades_webhook(self, notifier):
        """Test daily summary uses trades webhook"""
        with patch.object(notifier, "_send", new_callable=AsyncMock) as mock_send:
            await notifier.notify_daily_summary(pnl=100.0, trade_count=5, win_rate=0.6)

            webhook_url = mock_send.call_args.args[0]
            assert webhook_url == "https://discord.com/trades"


class TestNotifySystemStop:
    """notify_system_stop method tests"""

    @pytest.fixture
    def notifier(self):
        with patch.dict(os.environ, {
            "DISCORD_WEBHOOK_TRADES": "https://discord.com/trades",
            "DISCORD_WEBHOOK_ALERTS": "https://discord.com/alerts"
        }):
            return DiscordNotifier()

    @pytest.mark.asyncio
    async def test_notify_system_stop(self, notifier):
        """Test system stop notification"""
        with patch.object(notifier, "_send", new_callable=AsyncMock) as mock_send:
            await notifier.notify_system_stop("Daily loss limit reached")

            mock_send.assert_called_once()
            content = mock_send.call_args.args[1]
            assert "🛑" in content
            assert "SYSTEM STOPPED" in content
            assert "Daily loss limit reached" in content

    @pytest.mark.asyncio
    async def test_notify_system_stop_uses_alerts_webhook(self, notifier):
        """Test system stop uses alerts webhook"""
        with patch.object(notifier, "_send", new_callable=AsyncMock) as mock_send:
            await notifier.notify_system_stop("Test reason")

            webhook_url = mock_send.call_args.args[0]
            assert webhook_url == "https://discord.com/alerts"

    @pytest.mark.asyncio
    async def test_notify_system_stop_long_reason(self, notifier):
        """Test system stop with long reason"""
        with patch.object(notifier, "_send", new_callable=AsyncMock) as mock_send:
            long_reason = "A" * 1000
            await notifier.notify_system_stop(long_reason)

            content = mock_send.call_args.args[1]
            assert long_reason in content

    @pytest.mark.asyncio
    async def test_notify_system_stop_special_characters(self, notifier):
        """Test system stop with special characters in reason"""
        with patch.object(notifier, "_send", new_callable=AsyncMock) as mock_send:
            await notifier.notify_system_stop("Loss: $-500.00 (>limit)")

            content = mock_send.call_args.args[1]
            assert "$-500.00" in content


class TestEdgeCases:
    """Edge case tests"""

    @pytest.mark.asyncio
    async def test_multiple_notifications_sequential(self):
        """Test multiple sequential notifications"""
        with patch.dict(os.environ, {
            "DISCORD_WEBHOOK_TRADES": "https://discord.com/trades",
            "DISCORD_WEBHOOK_ALERTS": "https://discord.com/alerts"
        }):
            notifier = DiscordNotifier()

            with patch.object(notifier, "_send", new_callable=AsyncMock) as mock_send:
                await notifier.notify_trade("MSTR", "buy", 10, 350.0)
                await notifier.notify_alert("Test alert", "info")
                await notifier.notify_system_stop("Test stop")

                assert mock_send.call_count == 3

    @pytest.mark.asyncio
    async def test_unicode_in_messages(self):
        """Test unicode characters in messages"""
        with patch.dict(os.environ, {
            "DISCORD_WEBHOOK_ALERTS": "https://discord.com/alerts"
        }):
            notifier = DiscordNotifier()

            with patch.object(notifier, "_send", new_callable=AsyncMock) as mock_send:
                await notifier.notify_alert("日本語テスト", "info")

                content = mock_send.call_args.args[1]
                assert "日本語テスト" in content

    @pytest.mark.asyncio
    async def test_empty_message(self):
        """Test empty message handling"""
        with patch.dict(os.environ, {
            "DISCORD_WEBHOOK_ALERTS": "https://discord.com/alerts"
        }):
            notifier = DiscordNotifier()

            with patch.object(notifier, "_send", new_callable=AsyncMock) as mock_send:
                await notifier.notify_alert("", "info")

                mock_send.assert_called_once()
