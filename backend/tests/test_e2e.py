"""End-to-End tests - Full trading cycle simulation"""
import pytest
import os
from unittest.mock import patch, MagicMock, AsyncMock
from datetime import datetime

# Set environment before imports
os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["ALPACA_API_KEY"] = "test_key"
os.environ["ALPACA_SECRET_KEY"] = "test_secret"
os.environ["ALPACA_PAPER"] = "true"
os.environ["GROK_API_KEY"] = "test_grok_key"
os.environ["DISCORD_WEBHOOK_URL"] = ""
os.environ["MAX_DAILY_LOSS"] = "500"
os.environ["MAX_POSITION_RATIO"] = "0.5"


class TestFullTradingCycle:
    """Complete trading cycle E2E tests"""

    @pytest.fixture
    def mock_trader(self):
        """Create mock trader with realistic responses"""
        trader = MagicMock()
        trader.get_account.return_value = {
            "cash": 100000.0,
            "portfolio_value": 100000.0,
            "buying_power": 200000.0,
            "equity": 100000.0,
            "daily_pnl": 0.0
        }
        trader.get_positions.return_value = []
        trader.get_market_data.return_value = {
            "MSTR": {"price": 350.0, "change_pct": 2.5, "volume": 1000000},
            "TSLA": {"price": 250.0, "change_pct": -1.2, "volume": 2000000}
        }
        trader.execute_order.return_value = {
            "order_id": "test-order-123",
            "symbol": "MSTR",
            "side": "buy",
            "qty": 10.0,
            "type": "market",
            "status": "filled",
            "submitted_at": datetime.now().isoformat()
        }
        return trader

    @pytest.fixture
    def mock_grok(self):
        """Create mock Grok client"""
        grok = MagicMock()
        grok.analyze_market.return_value = {
            "action": "buy",
            "symbol": "MSTR",
            "quantity": 10,
            "reasoning": "Bullish momentum detected",
            "confidence": 80,
            "order_type": "market"
        }
        return grok

    @pytest.fixture
    def mock_notifier(self):
        """Create mock Discord notifier"""
        notifier = MagicMock()
        notifier.notify_trade = AsyncMock()
        notifier.notify_alert = AsyncMock()
        notifier.notify_system_stop = AsyncMock()
        return notifier

    @pytest.mark.asyncio
    async def test_successful_buy_cycle(self, mock_trader, mock_grok, mock_notifier):
        """Test complete successful buy cycle"""
        with patch("main.trader", mock_trader):
            with patch("main.grok", mock_grok):
                with patch("main.notifier", mock_notifier):
                    with patch("main.guard") as mock_guard:
                        mock_guard.check_system_health.return_value = MagicMock(allowed=True)
                        mock_guard.check_order.return_value = MagicMock(allowed=True)

                        with patch("main.log_decision"):
                            with patch("main.log_trade"):
                                from main import trading_cycle
                                await trading_cycle()

        # Verify the full cycle
        mock_trader.get_account.assert_called()
        mock_trader.get_positions.assert_called()
        mock_trader.get_market_data.assert_called()
        mock_grok.analyze_market.assert_called()
        mock_trader.execute_order.assert_called_once()

    @pytest.mark.asyncio
    async def test_hold_decision_no_trade(self, mock_trader, mock_grok, mock_notifier):
        """Test hold decision doesn't execute trade"""
        mock_grok.analyze_market.return_value = {
            "action": "hold",
            "symbol": "MSTR",
            "quantity": 0,
            "reasoning": "Waiting for better entry",
            "confidence": 60,
            "order_type": "market"
        }

        with patch("main.trader", mock_trader):
            with patch("main.grok", mock_grok):
                with patch("main.notifier", mock_notifier):
                    with patch("main.guard") as mock_guard:
                        mock_guard.check_system_health.return_value = MagicMock(allowed=True)

                        with patch("main.log_decision"):
                            from main import trading_cycle
                            await trading_cycle()

        # No order should be executed for hold
        mock_trader.execute_order.assert_not_called()

    @pytest.mark.asyncio
    async def test_risk_blocked_no_trade(self, mock_trader, mock_grok, mock_notifier):
        """Test risk guard blocking trade"""
        with patch("main.trader", mock_trader):
            with patch("main.grok", mock_grok):
                with patch("main.notifier", mock_notifier):
                    with patch("main.guard") as mock_guard:
                        mock_guard.check_system_health.return_value = MagicMock(allowed=True)
                        mock_guard.check_order.return_value = MagicMock(
                            allowed=False,
                            reason="Position ratio would exceed limit"
                        )

                        with patch("main.log_decision"):
                            from main import trading_cycle
                            await trading_cycle()

        # Order should not be executed when blocked
        mock_trader.execute_order.assert_not_called()

    @pytest.mark.asyncio
    async def test_system_health_stop(self, mock_trader, mock_grok, mock_notifier):
        """Test system stops when health check fails"""
        mock_trader.get_account.return_value["daily_pnl"] = -600.0  # Beyond loss limit

        with patch("main.trader", mock_trader):
            with patch("main.grok", mock_grok):
                with patch("main.notifier", mock_notifier):
                    with patch("main.guard") as mock_guard:
                        mock_guard.check_system_health.return_value = MagicMock(
                            allowed=False,
                            reason="Daily loss limit exceeded"
                        )

                        with patch("main.scheduler") as mock_scheduler:
                            from main import trading_cycle
                            await trading_cycle()

                            # Scheduler should pause
                            mock_scheduler.pause.assert_called_once()

        # Notification should be sent
        mock_notifier.notify_system_stop.assert_called()


class TestSellCycle:
    """Sell order E2E tests"""

    @pytest.fixture
    def mock_trader_with_positions(self):
        """Trader with existing positions"""
        trader = MagicMock()
        trader.get_account.return_value = {
            "cash": 50000.0,
            "portfolio_value": 100000.0,
            "buying_power": 100000.0,
            "equity": 100000.0,
            "daily_pnl": 500.0
        }
        trader.get_positions.return_value = [
            {
                "symbol": "MSTR",
                "qty": 20.0,
                "avg_entry_price": 340.0,
                "market_value": 7000.0,
                "unrealized_pl": 200.0
            }
        ]
        trader.get_market_data.return_value = {
            "MSTR": {"price": 350.0, "change_pct": 2.5, "volume": 1000000}
        }
        trader.execute_order.return_value = {
            "order_id": "sell-order-456",
            "symbol": "MSTR",
            "side": "sell",
            "qty": 10.0,
            "type": "market",
            "status": "filled",
            "submitted_at": datetime.now().isoformat()
        }
        return trader

    @pytest.mark.asyncio
    async def test_successful_sell_cycle(self, mock_trader_with_positions):
        """Test complete sell cycle with existing position"""
        mock_grok = MagicMock()
        mock_grok.analyze_market.return_value = {
            "action": "sell",
            "symbol": "MSTR",
            "quantity": 10,
            "reasoning": "Taking profit at resistance",
            "confidence": 75,
            "order_type": "market"
        }

        mock_notifier = MagicMock()
        mock_notifier.notify_trade = AsyncMock()
        mock_notifier.notify_alert = AsyncMock()

        with patch("main.trader", mock_trader_with_positions):
            with patch("main.grok", mock_grok):
                with patch("main.notifier", mock_notifier):
                    with patch("main.guard") as mock_guard:
                        mock_guard.check_system_health.return_value = MagicMock(allowed=True)
                        mock_guard.check_order.return_value = MagicMock(allowed=True)

                        with patch("main.log_decision"):
                            with patch("main.log_trade"):
                                from main import trading_cycle
                                await trading_cycle()

        # Verify sell order
        mock_trader_with_positions.execute_order.assert_called_once()
        call_args = mock_trader_with_positions.execute_order.call_args
        assert call_args[1]["action"] == "sell"
        assert call_args[1]["symbol"] == "MSTR"


class TestErrorRecovery:
    """Error handling and recovery E2E tests"""

    @pytest.mark.asyncio
    async def test_grok_api_failure_recovery(self):
        """Test graceful handling of Grok API failure"""
        mock_trader = MagicMock()
        mock_trader.get_account.return_value = {
            "cash": 100000.0,
            "portfolio_value": 100000.0,
            "buying_power": 200000.0,
            "equity": 100000.0,
            "daily_pnl": 0.0
        }
        mock_trader.get_positions.return_value = []
        mock_trader.get_market_data.return_value = {"MSTR": {"price": 350.0}}

        mock_grok = MagicMock()
        mock_grok.analyze_market.return_value = None  # API failure

        mock_notifier = MagicMock()
        mock_notifier.notify_alert = AsyncMock()

        with patch("main.trader", mock_trader):
            with patch("main.grok", mock_grok):
                with patch("main.notifier", mock_notifier):
                    with patch("main.guard") as mock_guard:
                        mock_guard.check_system_health.return_value = MagicMock(allowed=True)

                        from main import trading_cycle
                        # Should not raise, just skip trading
                        await trading_cycle()

        # No order should be executed
        mock_trader.execute_order.assert_not_called()

    @pytest.mark.asyncio
    async def test_order_execution_failure(self):
        """Test handling of order execution failure"""
        mock_trader = MagicMock()
        mock_trader.get_account.return_value = {
            "cash": 100000.0,
            "portfolio_value": 100000.0,
            "buying_power": 200000.0,
            "equity": 100000.0,
            "daily_pnl": 0.0
        }
        mock_trader.get_positions.return_value = []
        mock_trader.get_market_data.return_value = {"MSTR": {"price": 350.0}}
        mock_trader.execute_order.return_value = None  # Order failed

        mock_grok = MagicMock()
        mock_grok.analyze_market.return_value = {
            "action": "buy",
            "symbol": "MSTR",
            "quantity": 10,
            "reasoning": "Buy signal",
            "confidence": 80,
            "order_type": "market"
        }

        mock_notifier = MagicMock()
        mock_notifier.notify_alert = AsyncMock()
        mock_notifier.notify_trade = AsyncMock()

        with patch("main.trader", mock_trader):
            with patch("main.grok", mock_grok):
                with patch("main.notifier", mock_notifier):
                    with patch("main.guard") as mock_guard:
                        mock_guard.check_system_health.return_value = MagicMock(allowed=True)
                        mock_guard.check_order.return_value = MagicMock(allowed=True)

                        with patch("main.log_decision"):
                            from main import trading_cycle
                            await trading_cycle()

        # Error notification should be sent
        mock_notifier.notify_alert.assert_called()

    @pytest.mark.asyncio
    async def test_market_data_unavailable(self):
        """Test handling when market data is unavailable"""
        mock_trader = MagicMock()
        mock_trader.get_account.return_value = {
            "cash": 100000.0,
            "portfolio_value": 100000.0,
            "buying_power": 200000.0,
            "equity": 100000.0,
            "daily_pnl": 0.0
        }
        mock_trader.get_positions.return_value = []
        mock_trader.get_market_data.return_value = {}  # No market data

        mock_grok = MagicMock()
        mock_notifier = MagicMock()
        mock_notifier.notify_alert = AsyncMock()

        with patch("main.trader", mock_trader):
            with patch("main.grok", mock_grok):
                with patch("main.notifier", mock_notifier):
                    with patch("main.guard") as mock_guard:
                        mock_guard.check_system_health.return_value = MagicMock(allowed=True)

                        from main import trading_cycle
                        await trading_cycle()

        # Grok should not be called without market data
        mock_grok.analyze_market.assert_not_called()


class TestMultipleCycles:
    """Multiple trading cycle tests"""

    @pytest.mark.asyncio
    async def test_consecutive_cycles(self):
        """Test multiple consecutive trading cycles"""
        mock_trader = MagicMock()
        mock_trader.get_account.return_value = {
            "cash": 100000.0,
            "portfolio_value": 100000.0,
            "buying_power": 200000.0,
            "equity": 100000.0,
            "daily_pnl": 0.0
        }
        mock_trader.get_positions.return_value = []
        mock_trader.get_market_data.return_value = {"MSTR": {"price": 350.0}}
        mock_trader.execute_order.return_value = {
            "order_id": "order-1",
            "status": "filled"
        }

        cycle_count = 0

        def dynamic_grok_response(*args, **kwargs):
            nonlocal cycle_count
            cycle_count += 1
            if cycle_count == 1:
                return {"action": "buy", "symbol": "MSTR", "quantity": 5,
                        "reasoning": "Entry", "confidence": 80, "order_type": "market"}
            elif cycle_count == 2:
                return {"action": "hold", "symbol": "MSTR", "quantity": 0,
                        "reasoning": "Wait", "confidence": 60, "order_type": "market"}
            else:
                return {"action": "sell", "symbol": "MSTR", "quantity": 5,
                        "reasoning": "Exit", "confidence": 75, "order_type": "market"}

        mock_grok = MagicMock()
        mock_grok.analyze_market.side_effect = dynamic_grok_response

        mock_notifier = MagicMock()
        mock_notifier.notify_trade = AsyncMock()
        mock_notifier.notify_alert = AsyncMock()

        with patch("main.trader", mock_trader):
            with patch("main.grok", mock_grok):
                with patch("main.notifier", mock_notifier):
                    with patch("main.guard") as mock_guard:
                        mock_guard.check_system_health.return_value = MagicMock(allowed=True)
                        mock_guard.check_order.return_value = MagicMock(allowed=True)

                        with patch("main.log_decision"):
                            with patch("main.log_trade"):
                                from main import trading_cycle

                                # Run 3 cycles
                                await trading_cycle()  # Buy
                                await trading_cycle()  # Hold
                                await trading_cycle()  # Sell

        # Should have 2 orders (buy and sell, not hold)
        assert mock_trader.execute_order.call_count == 2
