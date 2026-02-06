"""End-to-End tests - Full 4-stage pipeline trading cycle simulation"""
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
os.environ["ANTHROPIC_API_KEY"] = "test_anthropic_key"
os.environ["DISCORD_WEBHOOK_URL"] = ""
os.environ["MAX_DAILY_LOSS"] = "500"
os.environ["MAX_POSITION_RATIO"] = "0.5"


def make_mock_grok_report(significant=True, sentiment=60, news=None):
    """ヘルパー: Grokレポート生成"""
    return {
        "timestamp": datetime.now().isoformat(),
        "significant_change": significant,
        "sentiment": {"overall": sentiment, "trending_tickers": [{"symbol": "MSTR"}], "notable_signals": []},
        "breaking_news": news or [],
        "market_context": {"spy_trend": "bullish", "vix_level": "normal", "sector_rotation": "tech"},
    }


def make_mock_opus_decision(action="buy", symbol="MSTR", quantity=10, confidence=80):
    """ヘルパー: Opus判断生成"""
    return {
        "action": action,
        "symbol": symbol,
        "quantity": quantity,
        "order_type": "market",
        "limit_price": None,
        "stop_loss": 330.0 if action == "buy" else None,
        "take_profit": 400.0 if action == "buy" else None,
        "position_size_pct": 30,
        "reasoning": "Test decision",
        "risk_assessment": "medium",
        "confidence": confidence,
        "adjustments": [],
    }


class TestFullTradingCycle:
    """Complete 4-stage pipeline E2E tests"""

    @pytest.fixture
    def mock_trader(self):
        """Create mock trader with realistic responses"""
        t = MagicMock()
        t.get_account.return_value = {
            "cash": 100000.0,
            "portfolio_value": 100000.0,
            "buying_power": 200000.0,
            "equity": 100000.0,
            "last_equity": 99500.0,
            "daily_pnl": 0.0,
        }
        t.get_positions.return_value = []
        t.get_market_data.return_value = {
            "MSTR": {"price": 350.0, "change_5d": "+5%", "volume": 1000000},
        }
        t.execute_order.return_value = {
            "order_id": "test-order-123",
            "symbol": "MSTR",
            "side": "buy",
            "qty": 10.0,
            "type": "market",
            "status": "filled",
            "submitted_at": datetime.now().isoformat(),
        }
        return t

    @pytest.fixture
    def mock_grok(self):
        grok = MagicMock()
        grok.collect_market_report.return_value = (make_mock_grok_report(), 120)
        return grok

    @pytest.fixture
    def mock_opus(self):
        opus = MagicMock()
        opus.analyze.return_value = (make_mock_opus_decision(), 800)
        return opus

    @pytest.fixture
    def mock_notifier(self):
        n = MagicMock()
        n.notify_trade = AsyncMock()
        n.notify_alert = AsyncMock()
        n.notify_system_stop = AsyncMock()
        n.send_pipeline_log = AsyncMock()
        return n

    @pytest.mark.asyncio
    async def test_successful_buy_cycle(self, mock_trader, mock_grok, mock_opus, mock_notifier):
        """Test complete successful buy cycle through 4 stages"""
        with patch("main.trader", mock_trader), \
             patch("main.grok", mock_grok), \
             patch("main.opus", mock_opus), \
             patch("main.notifier", mock_notifier), \
             patch("main.guard") as mock_guard, \
             patch("main.log_pipeline"), \
             patch("main.log_trade"):
            mock_guard.check_system_health.return_value = MagicMock(allowed=True)
            mock_guard.check.return_value = MagicMock(allowed=True, adjustments=[])
            mock_guard.max_daily_loss = 500

            from main import trading_cycle
            await trading_cycle()

        # Verify 4-stage flow
        mock_grok.collect_market_report.assert_called_once()
        mock_opus.analyze.assert_called_once()
        mock_trader.execute_order.assert_called_once()

        # Verify Discord logs for each stage
        log_calls = [c.args[1] for c in mock_notifier.send_pipeline_log.call_args_list]
        assert "grok" in log_calls
        assert "opus_decision" in log_calls
        assert "execution" in log_calls

    @pytest.mark.asyncio
    async def test_hold_decision_no_trade(self, mock_trader, mock_grok, mock_notifier):
        """Test hold decision doesn't execute trade"""
        mock_opus = MagicMock()
        mock_opus.analyze.return_value = (make_mock_opus_decision(action="hold", quantity=0), 500)

        with patch("main.trader", mock_trader), \
             patch("main.grok", mock_grok), \
             patch("main.opus", mock_opus), \
             patch("main.notifier", mock_notifier), \
             patch("main.guard") as mock_guard, \
             patch("main.log_pipeline"):
            mock_guard.check_system_health.return_value = MagicMock(allowed=True)
            mock_guard.check.return_value = MagicMock(allowed=True, adjustments=[])
            mock_guard.max_daily_loss = 500

            from main import trading_cycle
            await trading_cycle()

        mock_trader.execute_order.assert_not_called()

    @pytest.mark.asyncio
    async def test_risk_blocked_no_trade(self, mock_trader, mock_grok, mock_opus, mock_notifier):
        """Test risk guard blocking trade"""
        with patch("main.trader", mock_trader), \
             patch("main.grok", mock_grok), \
             patch("main.opus", mock_opus), \
             patch("main.notifier", mock_notifier), \
             patch("main.guard") as mock_guard, \
             patch("main.log_pipeline"):
            mock_guard.check_system_health.return_value = MagicMock(allowed=True)
            mock_guard.check.return_value = MagicMock(
                allowed=False, reason="confidence_too_low: 35", adjustments=[]
            )
            mock_guard.max_daily_loss = 500

            from main import trading_cycle
            await trading_cycle()

        mock_trader.execute_order.assert_not_called()

    @pytest.mark.asyncio
    async def test_system_health_stop(self, mock_trader, mock_grok, mock_notifier):
        """Test system stops when health check fails"""
        mock_trader.get_account.return_value["daily_pnl"] = -600.0

        with patch("main.trader", mock_trader), \
             patch("main.grok", mock_grok), \
             patch("main.notifier", mock_notifier), \
             patch("main.guard") as mock_guard, \
             patch("main.scheduler") as mock_scheduler:
            mock_guard.check_system_health.return_value = MagicMock(
                allowed=False, reason="Daily loss limit exceeded"
            )

            from main import trading_cycle
            await trading_cycle()

            mock_scheduler.pause.assert_called_once()

        mock_notifier.notify_system_stop.assert_called()

    @pytest.mark.asyncio
    async def test_opus_skipped_no_significant_change(self, mock_trader, mock_notifier):
        """Test Opus is skipped when no significant change"""
        mock_grok = MagicMock()
        mock_grok.collect_market_report.return_value = (
            make_mock_grok_report(significant=False, sentiment=10),
            80,
        )

        mock_opus = MagicMock()

        with patch("main.trader", mock_trader), \
             patch("main.grok", mock_grok), \
             patch("main.opus", mock_opus), \
             patch("main.notifier", mock_notifier), \
             patch("main.guard") as mock_guard, \
             patch("main.log_pipeline"):
            mock_guard.check_system_health.return_value = MagicMock(allowed=True)

            from main import trading_cycle
            await trading_cycle()

        # Opus should NOT be called
        mock_opus.analyze.assert_not_called()
        mock_trader.execute_order.assert_not_called()

        # Discord skip log
        log_calls = [c.args[1] for c in mock_notifier.send_pipeline_log.call_args_list]
        assert "skip" in log_calls


class TestSellCycle:
    """Sell order E2E tests"""

    @pytest.mark.asyncio
    async def test_successful_sell_cycle(self):
        """Test complete sell cycle with existing position"""
        mock_trader = MagicMock()
        mock_trader.get_account.return_value = {
            "cash": 50000.0, "portfolio_value": 100000.0,
            "buying_power": 100000.0, "equity": 100000.0,
            "last_equity": 99500.0, "daily_pnl": 500.0,
        }
        mock_trader.get_positions.return_value = [
            {"symbol": "MSTR", "qty": 20.0, "avg_entry_price": 340.0,
             "market_value": 7000.0, "unrealized_pl": 200.0}
        ]
        mock_trader.get_market_data.return_value = {
            "MSTR": {"price": 350.0, "change_5d": "+5%", "volume": 1000000}
        }
        mock_trader.execute_order.return_value = {
            "order_id": "sell-order-456", "symbol": "MSTR",
            "side": "sell", "qty": 10.0, "type": "market",
            "status": "filled", "submitted_at": datetime.now().isoformat(),
        }

        mock_grok = MagicMock()
        mock_grok.collect_market_report.return_value = (make_mock_grok_report(), 100)

        mock_opus = MagicMock()
        mock_opus.analyze.return_value = (
            make_mock_opus_decision(action="sell", symbol="MSTR", quantity=10),
            600,
        )

        mock_notifier = MagicMock()
        mock_notifier.notify_trade = AsyncMock()
        mock_notifier.notify_alert = AsyncMock()
        mock_notifier.send_pipeline_log = AsyncMock()

        with patch("main.trader", mock_trader), \
             patch("main.grok", mock_grok), \
             patch("main.opus", mock_opus), \
             patch("main.notifier", mock_notifier), \
             patch("main.guard") as mock_guard, \
             patch("main.log_pipeline"), \
             patch("main.log_trade"):
            mock_guard.check_system_health.return_value = MagicMock(allowed=True)
            mock_guard.check.return_value = MagicMock(allowed=True, adjustments=[])
            mock_guard.max_daily_loss = 500

            from main import trading_cycle
            await trading_cycle()

        mock_trader.execute_order.assert_called_once()
        call_kwargs = mock_trader.execute_order.call_args[1]
        assert call_kwargs["action"] == "sell"
        assert call_kwargs["symbol"] == "MSTR"


class TestErrorRecovery:
    """Error handling and recovery E2E tests"""

    @pytest.mark.asyncio
    async def test_grok_api_failure_recovery(self):
        """Test graceful handling of Grok API failure"""
        mock_trader = MagicMock()
        mock_trader.get_account.return_value = {
            "cash": 100000.0, "portfolio_value": 100000.0,
            "buying_power": 200000.0, "equity": 100000.0,
            "last_equity": 100000.0, "daily_pnl": 0.0,
        }
        mock_trader.get_positions.return_value = []
        mock_trader.get_market_data.return_value = {"MSTR": {"price": 350.0}}

        mock_grok = MagicMock()
        mock_grok.collect_market_report.return_value = (None, 0)

        mock_notifier = MagicMock()
        mock_notifier.notify_alert = AsyncMock()
        mock_notifier.send_pipeline_log = AsyncMock()

        with patch("main.trader", mock_trader), \
             patch("main.grok", mock_grok), \
             patch("main.notifier", mock_notifier), \
             patch("main.guard") as mock_guard:
            mock_guard.check_system_health.return_value = MagicMock(allowed=True)

            from main import trading_cycle
            await trading_cycle()

        mock_trader.execute_order.assert_not_called()

    @pytest.mark.asyncio
    async def test_opus_api_failure_recovery(self):
        """Test graceful handling of Opus API failure"""
        mock_trader = MagicMock()
        mock_trader.get_account.return_value = {
            "cash": 100000.0, "portfolio_value": 100000.0,
            "buying_power": 200000.0, "equity": 100000.0,
            "last_equity": 100000.0, "daily_pnl": 0.0,
        }
        mock_trader.get_positions.return_value = []
        mock_trader.get_market_data.return_value = {"MSTR": {"price": 350.0}}

        mock_grok = MagicMock()
        mock_grok.collect_market_report.return_value = (make_mock_grok_report(), 100)

        mock_opus = MagicMock()
        mock_opus.analyze.return_value = (None, 0)

        mock_notifier = MagicMock()
        mock_notifier.notify_alert = AsyncMock()
        mock_notifier.send_pipeline_log = AsyncMock()

        with patch("main.trader", mock_trader), \
             patch("main.grok", mock_grok), \
             patch("main.opus", mock_opus), \
             patch("main.notifier", mock_notifier), \
             patch("main.guard") as mock_guard, \
             patch("main.log_pipeline"):
            mock_guard.check_system_health.return_value = MagicMock(allowed=True)
            mock_guard.max_daily_loss = 500

            from main import trading_cycle
            await trading_cycle()

        mock_trader.execute_order.assert_not_called()

    @pytest.mark.asyncio
    async def test_order_execution_failure(self):
        """Test handling of order execution failure"""
        mock_trader = MagicMock()
        mock_trader.get_account.return_value = {
            "cash": 100000.0, "portfolio_value": 100000.0,
            "buying_power": 200000.0, "equity": 100000.0,
            "last_equity": 100000.0, "daily_pnl": 0.0,
        }
        mock_trader.get_positions.return_value = []
        mock_trader.get_market_data.return_value = {"MSTR": {"price": 350.0}}
        mock_trader.execute_order.return_value = None

        mock_grok = MagicMock()
        mock_grok.collect_market_report.return_value = (make_mock_grok_report(), 100)

        mock_opus = MagicMock()
        mock_opus.analyze.return_value = (make_mock_opus_decision(), 800)

        mock_notifier = MagicMock()
        mock_notifier.notify_alert = AsyncMock()
        mock_notifier.send_pipeline_log = AsyncMock()

        with patch("main.trader", mock_trader), \
             patch("main.grok", mock_grok), \
             patch("main.opus", mock_opus), \
             patch("main.notifier", mock_notifier), \
             patch("main.guard") as mock_guard, \
             patch("main.log_pipeline"):
            mock_guard.check_system_health.return_value = MagicMock(allowed=True)
            mock_guard.check.return_value = MagicMock(allowed=True, adjustments=[])
            mock_guard.max_daily_loss = 500

            from main import trading_cycle
            await trading_cycle()

        mock_notifier.notify_alert.assert_called()

    @pytest.mark.asyncio
    async def test_market_data_unavailable(self):
        """Test handling when market data is unavailable"""
        mock_trader = MagicMock()
        mock_trader.get_account.return_value = {
            "cash": 100000.0, "portfolio_value": 100000.0,
            "buying_power": 200000.0, "equity": 100000.0,
            "last_equity": 100000.0, "daily_pnl": 0.0,
        }
        mock_trader.get_positions.return_value = []
        mock_trader.get_market_data.return_value = {}

        mock_grok = MagicMock()
        mock_notifier = MagicMock()
        mock_notifier.notify_alert = AsyncMock()

        with patch("main.trader", mock_trader), \
             patch("main.grok", mock_grok), \
             patch("main.notifier", mock_notifier), \
             patch("main.guard") as mock_guard:
            mock_guard.check_system_health.return_value = MagicMock(allowed=True)

            from main import trading_cycle
            await trading_cycle()

        mock_grok.collect_market_report.assert_not_called()


class TestMultipleCycles:
    """Multiple trading cycle tests"""

    @pytest.mark.asyncio
    async def test_consecutive_cycles(self):
        """Test multiple consecutive trading cycles"""
        mock_trader = MagicMock()
        mock_trader.get_account.return_value = {
            "cash": 100000.0, "portfolio_value": 100000.0,
            "buying_power": 200000.0, "equity": 100000.0,
            "last_equity": 100000.0, "daily_pnl": 0.0,
        }
        mock_trader.get_positions.return_value = []
        mock_trader.get_market_data.return_value = {"MSTR": {"price": 350.0}}
        mock_trader.execute_order.return_value = {
            "order_id": "order-1", "status": "filled",
        }

        mock_grok = MagicMock()
        mock_grok.collect_market_report.return_value = (make_mock_grok_report(), 100)

        cycle_count = 0

        def dynamic_opus_response(*args, **kwargs):
            nonlocal cycle_count
            cycle_count += 1
            if cycle_count == 1:
                return (make_mock_opus_decision(action="buy"), 600)
            elif cycle_count == 2:
                return (make_mock_opus_decision(action="hold", quantity=0), 500)
            else:
                return (make_mock_opus_decision(action="sell"), 700)

        mock_opus = MagicMock()
        mock_opus.analyze.side_effect = dynamic_opus_response

        mock_notifier = MagicMock()
        mock_notifier.notify_trade = AsyncMock()
        mock_notifier.notify_alert = AsyncMock()
        mock_notifier.send_pipeline_log = AsyncMock()

        with patch("main.trader", mock_trader), \
             patch("main.grok", mock_grok), \
             patch("main.opus", mock_opus), \
             patch("main.notifier", mock_notifier), \
             patch("main.guard") as mock_guard, \
             patch("main.log_pipeline"), \
             patch("main.log_trade"):
            mock_guard.check_system_health.return_value = MagicMock(allowed=True)
            mock_guard.check.return_value = MagicMock(allowed=True, adjustments=[])
            mock_guard.max_daily_loss = 500

            from main import trading_cycle
            await trading_cycle()  # Buy
            await trading_cycle()  # Hold
            await trading_cycle()  # Sell

        # Should have 2 orders (buy and sell, not hold)
        assert mock_trader.execute_order.call_count == 2


class TestRiskGuardAdjustments:
    """Tests for Risk Guard adjustments in pipeline"""

    @pytest.mark.asyncio
    async def test_position_size_adjusted(self):
        """Test that Risk Guard adjustments are logged"""
        mock_trader = MagicMock()
        mock_trader.get_account.return_value = {
            "cash": 100000.0, "portfolio_value": 100000.0,
            "buying_power": 200000.0, "equity": 100000.0,
            "last_equity": 100000.0, "daily_pnl": 0.0,
        }
        mock_trader.get_positions.return_value = []
        mock_trader.get_market_data.return_value = {"MSTR": {"price": 350.0}}
        mock_trader.execute_order.return_value = {
            "order_id": "order-adj", "status": "filled",
        }

        mock_grok = MagicMock()
        mock_grok.collect_market_report.return_value = (make_mock_grok_report(), 100)

        mock_opus = MagicMock()
        decision = make_mock_opus_decision()
        decision["position_size_pct"] = 60  # Will be adjusted down
        mock_opus.analyze.return_value = (decision, 800)

        mock_notifier = MagicMock()
        mock_notifier.notify_alert = AsyncMock()
        mock_notifier.send_pipeline_log = AsyncMock()

        with patch("main.trader", mock_trader), \
             patch("main.grok", mock_grok), \
             patch("main.opus", mock_opus), \
             patch("main.notifier", mock_notifier), \
             patch("main.guard") as mock_guard, \
             patch("main.log_pipeline"), \
             patch("main.log_trade"):
            mock_guard.check_system_health.return_value = MagicMock(allowed=True)
            mock_guard.check.return_value = MagicMock(
                allowed=True,
                adjustments=[
                    {"field": "position_size_pct", "original": 60, "adjusted": 50, "reason": "exceeds max"}
                ],
            )
            mock_guard.max_daily_loss = 500

            from main import trading_cycle
            await trading_cycle()

        # Verify risk_guard adjustment log sent
        rg_calls = [
            c for c in mock_notifier.send_pipeline_log.call_args_list
            if c.args[1] == "risk_guard"
        ]
        assert len(rg_calls) >= 1
