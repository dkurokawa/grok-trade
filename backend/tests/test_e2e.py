"""End-to-End tests - Full 4-stage pipeline trading cycle simulation"""
import os
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Set environment before imports
os.environ["ALPACA_API_KEY"] = "test_key"
os.environ["ALPACA_SECRET_KEY"] = "test_secret"
os.environ["ALPACA_PAPER"] = "true"
os.environ["GROK_API_KEY"] = "test_grok_key"
os.environ["ANTHROPIC_API_KEY"] = "test_anthropic_key"
os.environ["DISCORD_WEBHOOK_URL"] = ""
os.environ["MAX_DAILY_LOSS"] = "500"
os.environ["MAX_POSITION_RATIO"] = "0.5"


@pytest.fixture(autouse=True)
def _pipeline_env(dynamo_table, monkeypatch):
    """Route the data layer through moto and exercise the Opus decision path."""
    monkeypatch.setenv("DECISION_ENGINE", "opus")
    yield


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
        with patch("trading_core.trader", mock_trader), \
             patch("trading_core.grok", mock_grok), \
             patch("trading_core.opus", mock_opus), \
             patch("trading_core.notifier", mock_notifier), \
             patch("trading_core.guard") as mock_guard, \
             patch("trading_core.log_pipeline"), \
             patch("trading_core.log_trade"):
            mock_guard.check_system_health.return_value = MagicMock(allowed=True)
            mock_guard.check.return_value = MagicMock(allowed=True, adjustments=[])
            mock_guard.max_daily_loss = 500

            from trading_core import trading_cycle
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

        with patch("trading_core.trader", mock_trader), \
             patch("trading_core.grok", mock_grok), \
             patch("trading_core.opus", mock_opus), \
             patch("trading_core.notifier", mock_notifier), \
             patch("trading_core.guard") as mock_guard, \
             patch("trading_core.log_pipeline"):
            mock_guard.check_system_health.return_value = MagicMock(allowed=True)
            mock_guard.check.return_value = MagicMock(allowed=True, adjustments=[])
            mock_guard.max_daily_loss = 500

            from trading_core import trading_cycle
            await trading_cycle()

        mock_trader.execute_order.assert_not_called()

    @pytest.mark.asyncio
    async def test_risk_blocked_no_trade(self, mock_trader, mock_grok, mock_opus, mock_notifier):
        """Test risk guard blocking trade"""
        with patch("trading_core.trader", mock_trader), \
             patch("trading_core.grok", mock_grok), \
             patch("trading_core.opus", mock_opus), \
             patch("trading_core.notifier", mock_notifier), \
             patch("trading_core.guard") as mock_guard, \
             patch("trading_core.log_pipeline"):
            mock_guard.check_system_health.return_value = MagicMock(allowed=True)
            mock_guard.check.return_value = MagicMock(
                allowed=False, reason="confidence_too_low: 35", adjustments=[]
            )
            mock_guard.max_daily_loss = 500

            from trading_core import trading_cycle
            await trading_cycle()

        mock_trader.execute_order.assert_not_called()

    @pytest.mark.asyncio
    async def test_system_health_stop(self, mock_trader, mock_grok, mock_notifier):
        """Test system stops when health check fails"""
        mock_trader.get_account.return_value["daily_pnl"] = -600.0

        with patch("trading_core.trader", mock_trader), \
             patch("trading_core.grok", mock_grok), \
             patch("trading_core.notifier", mock_notifier), \
             patch("trading_core.guard") as mock_guard, \
             patch("trading_core.set_scheduler_state") as mock_set_state:
            mock_guard.check_system_health.return_value = MagicMock(
                allowed=False, reason="Daily loss limit exceeded"
            )

            from trading_core import trading_cycle
            await trading_cycle()

            mock_set_state.assert_called_once_with(False)

        mock_notifier.notify_system_stop.assert_called()

    @pytest.mark.asyncio
    async def test_system_health_stop_write_failure_still_skips_trade(self, mock_trader, mock_grok, mock_notifier):
        """If persisting the stop flag fails, the cycle must still not trade
        (the next cycle might not see the flag, but this one must not act as
        if nothing happened), and the failure must be surfaced as an error."""
        mock_trader.get_account.return_value["daily_pnl"] = -600.0

        with patch("trading_core.trader", mock_trader), \
             patch("trading_core.grok", mock_grok), \
             patch("trading_core.notifier", mock_notifier), \
             patch("trading_core.guard") as mock_guard, \
             patch("trading_core.set_scheduler_state", side_effect=RuntimeError("boom")):
            mock_guard.check_system_health.return_value = MagicMock(
                allowed=False, reason="Daily loss limit exceeded"
            )

            from trading_core import trading_cycle
            await trading_cycle()

        mock_notifier.notify_system_stop.assert_called()
        mock_notifier.notify_alert.assert_called()
        mock_trader.execute_order.assert_not_called()

    @pytest.mark.asyncio
    async def test_opus_skipped_no_significant_change(self, mock_trader, mock_notifier):
        """Test Opus is skipped when no significant change"""
        mock_grok = MagicMock()
        mock_grok.collect_market_report.return_value = (
            make_mock_grok_report(significant=False, sentiment=10),
            80,
        )

        mock_opus = MagicMock()

        with patch("trading_core.trader", mock_trader), \
             patch("trading_core.grok", mock_grok), \
             patch("trading_core.opus", mock_opus), \
             patch("trading_core.notifier", mock_notifier), \
             patch("trading_core.guard") as mock_guard, \
             patch("trading_core.log_pipeline"):
            mock_guard.check_system_health.return_value = MagicMock(allowed=True)

            from trading_core import trading_cycle
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

        with patch("trading_core.trader", mock_trader), \
             patch("trading_core.grok", mock_grok), \
             patch("trading_core.opus", mock_opus), \
             patch("trading_core.notifier", mock_notifier), \
             patch("trading_core.guard") as mock_guard, \
             patch("trading_core.log_pipeline"), \
             patch("trading_core.log_trade"):
            mock_guard.check_system_health.return_value = MagicMock(allowed=True)
            mock_guard.check.return_value = MagicMock(allowed=True, adjustments=[])
            mock_guard.max_daily_loss = 500

            from trading_core import trading_cycle
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

        with patch("trading_core.trader", mock_trader), \
             patch("trading_core.grok", mock_grok), \
             patch("trading_core.notifier", mock_notifier), \
             patch("trading_core.guard") as mock_guard:
            mock_guard.check_system_health.return_value = MagicMock(allowed=True)

            from trading_core import trading_cycle
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

        with patch("trading_core.trader", mock_trader), \
             patch("trading_core.grok", mock_grok), \
             patch("trading_core.opus", mock_opus), \
             patch("trading_core.notifier", mock_notifier), \
             patch("trading_core.guard") as mock_guard, \
             patch("trading_core.log_pipeline"):
            mock_guard.check_system_health.return_value = MagicMock(allowed=True)
            mock_guard.max_daily_loss = 500

            from trading_core import trading_cycle
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

        with patch("trading_core.trader", mock_trader), \
             patch("trading_core.grok", mock_grok), \
             patch("trading_core.opus", mock_opus), \
             patch("trading_core.notifier", mock_notifier), \
             patch("trading_core.guard") as mock_guard, \
             patch("trading_core.log_pipeline"):
            mock_guard.check_system_health.return_value = MagicMock(allowed=True)
            mock_guard.check.return_value = MagicMock(allowed=True, adjustments=[])
            mock_guard.max_daily_loss = 500

            from trading_core import trading_cycle
            await trading_cycle()

        mock_notifier.notify_alert.assert_called()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("bad_status", ["rejected", "canceled", "expired"])
    async def test_rejected_order_is_not_recorded_as_a_trade(self, bad_status):
        """Alpaca accepts the request and returns an Order object even when
        it rejects/cancels/expires it - trader.execute_order() doesn't raise
        or return None for this. The pipeline must still treat it as a
        failure (Discord alert) and must not log it to `trades`."""
        mock_trader = MagicMock()
        mock_trader.get_account.return_value = {
            "cash": 100000.0, "portfolio_value": 100000.0,
            "buying_power": 200000.0, "equity": 100000.0,
            "last_equity": 100000.0, "daily_pnl": 0.0,
        }
        mock_trader.get_positions.return_value = []
        mock_trader.get_market_data.return_value = {"MSTR": {"price": 350.0}}
        mock_trader.execute_order.return_value = {
            "order_id": "order-bad", "status": bad_status,
        }

        mock_grok = MagicMock()
        mock_grok.collect_market_report.return_value = (make_mock_grok_report(), 100)

        mock_opus = MagicMock()
        mock_opus.analyze.return_value = (make_mock_opus_decision(), 800)

        mock_notifier = MagicMock()
        mock_notifier.notify_alert = AsyncMock()
        mock_notifier.send_pipeline_log = AsyncMock()

        with patch("trading_core.trader", mock_trader), \
             patch("trading_core.grok", mock_grok), \
             patch("trading_core.opus", mock_opus), \
             patch("trading_core.notifier", mock_notifier), \
             patch("trading_core.guard") as mock_guard, \
             patch("trading_core.log_pipeline") as mock_log_pipeline, \
             patch("trading_core.log_trade") as mock_log_trade:
            mock_guard.check_system_health.return_value = MagicMock(allowed=True)
            mock_guard.check.return_value = MagicMock(allowed=True, adjustments=[])
            mock_guard.max_daily_loss = 500

            from trading_core import trading_cycle
            await trading_cycle()

        mock_log_trade.assert_not_called()
        assert mock_log_pipeline.call_args.kwargs["order_submitted"] is False
        mock_notifier.notify_alert.assert_awaited_once()
        assert bad_status in mock_notifier.notify_alert.call_args.args[0]

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

        with patch("trading_core.trader", mock_trader), \
             patch("trading_core.grok", mock_grok), \
             patch("trading_core.notifier", mock_notifier), \
             patch("trading_core.guard") as mock_guard:
            mock_guard.check_system_health.return_value = MagicMock(allowed=True)

            from trading_core import trading_cycle
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
        # A held MSTR position is present throughout (a real Alpaca account
        # would only reflect the buy after cycle 1, but this mock doesn't
        # model that state transition) so the cycle-3 sell passes
        # decision_schema's "symbol must be held" / "quantity <= held" checks.
        mock_trader.get_positions.return_value = [
            {"symbol": "MSTR", "qty": 10.0, "market_value": 3500.0}
        ]
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

        with patch("trading_core.trader", mock_trader), \
             patch("trading_core.grok", mock_grok), \
             patch("trading_core.opus", mock_opus), \
             patch("trading_core.notifier", mock_notifier), \
             patch("trading_core.guard") as mock_guard, \
             patch("trading_core.log_pipeline"), \
             patch("trading_core.log_trade"), \
             patch("trading_core.acquire_lock", return_value=True):
            # acquire_lock is forced to always succeed: these 3 calls simulate
            # 3 separate scheduled cycles (30 min apart in reality), but
            # running them back-to-back in a test would otherwise resolve to
            # the same slot and get deduped after the first (Issue #4).
            mock_guard.check_system_health.return_value = MagicMock(allowed=True)
            mock_guard.check.return_value = MagicMock(allowed=True, adjustments=[])
            mock_guard.max_daily_loss = 500

            from trading_core import trading_cycle
            await trading_cycle()  # Buy
            await trading_cycle()  # Hold
            await trading_cycle()  # Sell

        # Should have 2 orders (buy and sell, not hold)
        assert mock_trader.execute_order.call_count == 2


class TestDuplicateSlotLock:
    """A retried/duplicate invocation for the same scheduled slot must not
    place a second order (Issue #4). Unlike TestMultipleCycles, this
    exercises the real DynamoDB lock (moto-backed via the module's autouse
    _pipeline_env fixture) instead of forcing it to always succeed."""

    @pytest.mark.asyncio
    async def test_second_call_in_same_slot_does_not_trade_again(self):
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

        mock_opus = MagicMock()
        mock_opus.analyze.return_value = (make_mock_opus_decision(action="buy"), 600)

        mock_notifier = MagicMock()
        mock_notifier.notify_alert = AsyncMock()
        mock_notifier.send_pipeline_log = AsyncMock()

        with patch("trading_core.trader", mock_trader), \
             patch("trading_core.grok", mock_grok), \
             patch("trading_core.opus", mock_opus), \
             patch("trading_core.notifier", mock_notifier), \
             patch("trading_core.guard") as mock_guard, \
             patch("trading_core.log_pipeline"), \
             patch("trading_core.log_trade"):
            mock_guard.check_system_health.return_value = MagicMock(allowed=True)
            mock_guard.check.return_value = MagicMock(allowed=True, adjustments=[])
            mock_guard.max_daily_loss = 500

            from trading_core import trading_cycle
            await trading_cycle()  # first invocation for this slot: trades
            await trading_cycle()  # retried invocation, same slot: must not

        mock_trader.execute_order.assert_called_once()

    @pytest.mark.asyncio
    async def test_alpaca_side_duplicate_is_not_alerted(self):
        """If Alpaca itself rejects the order as a client_order_id duplicate
        (trader.execute_order raises DuplicateOrderError), that is not a
        failure and must not trigger the "Order failed" alert."""
        from trader import DuplicateOrderError

        mock_trader = MagicMock()
        mock_trader.get_account.return_value = {
            "cash": 100000.0, "portfolio_value": 100000.0,
            "buying_power": 200000.0, "equity": 100000.0,
            "last_equity": 100000.0, "daily_pnl": 0.0,
        }
        mock_trader.get_positions.return_value = []
        mock_trader.get_market_data.return_value = {"MSTR": {"price": 350.0}}
        mock_trader.execute_order.side_effect = DuplicateOrderError("already submitted")

        mock_grok = MagicMock()
        mock_grok.collect_market_report.return_value = (make_mock_grok_report(), 100)

        mock_opus = MagicMock()
        mock_opus.analyze.return_value = (make_mock_opus_decision(action="buy"), 600)

        mock_notifier = MagicMock()
        mock_notifier.notify_alert = AsyncMock()
        mock_notifier.send_pipeline_log = AsyncMock()

        with patch("trading_core.trader", mock_trader), \
             patch("trading_core.grok", mock_grok), \
             patch("trading_core.opus", mock_opus), \
             patch("trading_core.notifier", mock_notifier), \
             patch("trading_core.guard") as mock_guard, \
             patch("trading_core.log_pipeline") as mock_log_pipeline, \
             patch("trading_core.log_trade"):
            mock_guard.check_system_health.return_value = MagicMock(allowed=True)
            mock_guard.check.return_value = MagicMock(allowed=True, adjustments=[])
            mock_guard.max_daily_loss = 500

            from trading_core import trading_cycle
            await trading_cycle()

        mock_notifier.notify_alert.assert_not_called()
        assert mock_log_pipeline.call_args.kwargs["order_submitted"] is False
        assert mock_log_pipeline.call_args.kwargs["execution_result"] == {"skipped": "duplicate_order"}


class TestDecisionValidationInPipeline:
    """decision_schema.validate_decision is wired in ahead of RiskGuard/Alpaca
    (Issue #5): an invalid AI decision must never reach either."""

    @pytest.mark.asyncio
    async def test_invalid_decision_forces_hold_and_warns(self):
        mock_trader = MagicMock()
        mock_trader.get_account.return_value = {
            "cash": 100000.0, "portfolio_value": 100000.0,
            "buying_power": 200000.0, "equity": 100000.0,
            "last_equity": 100000.0, "daily_pnl": 0.0,
        }
        mock_trader.get_positions.return_value = []  # nothing held
        mock_trader.get_market_data.return_value = {"MSTR": {"price": 350.0}}

        mock_grok = MagicMock()
        mock_grok.collect_market_report.return_value = (make_mock_grok_report(), 100)

        mock_opus = MagicMock()
        # sell an unheld symbol - decision_schema must reject this
        mock_opus.analyze.return_value = (make_mock_opus_decision(action="sell", quantity=10), 700)

        mock_notifier = MagicMock()
        mock_notifier.notify_alert = AsyncMock()
        mock_notifier.send_pipeline_log = AsyncMock()

        with patch("trading_core.trader", mock_trader), \
             patch("trading_core.grok", mock_grok), \
             patch("trading_core.opus", mock_opus), \
             patch("trading_core.notifier", mock_notifier), \
             patch("trading_core.guard") as mock_guard, \
             patch("trading_core.log_pipeline") as mock_log_pipeline, \
             patch("trading_core.log_trade"):
            mock_guard.check_system_health.return_value = MagicMock(allowed=True)
            mock_guard.max_daily_loss = 500

            from trading_core import trading_cycle
            await trading_cycle()

        # RiskGuard still runs (it must, hold included), but with the decision
        # already forced to hold - never with the invalid "sell" - and Alpaca
        # is never reached.
        mock_guard.check.assert_called_once()
        assert mock_guard.check.call_args[0][0]["action"] == "hold"
        mock_trader.execute_order.assert_not_called()

        # warned, but as a warning (not the "Trading error" failure path)
        mock_notifier.notify_alert.assert_awaited_once()
        assert mock_notifier.notify_alert.call_args.args[1] == "warning"

        assert "invalid_decision" in mock_log_pipeline.call_args.kwargs["rg_reason"]
        assert mock_log_pipeline.call_args.kwargs["opus_output"]["action"] == "hold"


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

        with patch("trading_core.trader", mock_trader), \
             patch("trading_core.grok", mock_grok), \
             patch("trading_core.opus", mock_opus), \
             patch("trading_core.notifier", mock_notifier), \
             patch("trading_core.guard") as mock_guard, \
             patch("trading_core.log_pipeline"), \
             patch("trading_core.log_trade"):
            mock_guard.check_system_health.return_value = MagicMock(allowed=True)
            mock_guard.check.return_value = MagicMock(
                allowed=True,
                adjustments=[
                    {"field": "position_size_pct", "original": 60, "adjusted": 50, "reason": "exceeds max"}
                ],
            )
            mock_guard.max_daily_loss = 500

            from trading_core import trading_cycle
            await trading_cycle()

        # Verify risk_guard adjustment log sent
        rg_calls = [
            c for c in mock_notifier.send_pipeline_log.call_args_list
            if c.args[1] == "risk_guard"
        ]
        assert len(rg_calls) >= 1
