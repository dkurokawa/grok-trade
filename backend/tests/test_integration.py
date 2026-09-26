"""Integration tests - Component interaction and data flow for 4-stage pipeline"""
import pytest
import os
from unittest.mock import patch, MagicMock, AsyncMock
from datetime import datetime, date
# Set environment before imports
os.environ["ALPACA_API_KEY"] = "test_key"
os.environ["ALPACA_SECRET_KEY"] = "test_secret"
os.environ["ALPACA_PAPER"] = "true"
os.environ["GROK_API_KEY"] = "test_grok_key"
os.environ["ANTHROPIC_API_KEY"] = "test_anthropic_key"


@pytest.fixture(autouse=True)
def _pipeline_env(dynamo_table, monkeypatch):
    """Route the data layer through moto and exercise the Opus decision path."""
    monkeypatch.setenv("DECISION_ENGINE", "opus")
    yield


def _grok_report(significant=True, sentiment=55):
    return {
        "timestamp": datetime.now().isoformat(),
        "significant_change": significant,
        "sentiment": {"overall": sentiment, "trending_tickers": [{"symbol": "MSTR"}], "notable_signals": []},
        "breaking_news": [],
        "market_context": {"spy_trend": "bullish", "vix_level": "normal", "sector_rotation": "tech"},
    }


def _opus_decision(action="buy", symbol="MSTR", quantity=10, confidence=80):
    return {
        "action": action, "symbol": symbol, "quantity": quantity,
        "order_type": "market", "limit_price": None,
        "stop_loss": 330.0 if action == "buy" else None,
        "take_profit": 400.0 if action == "buy" else None,
        "position_size_pct": 30, "reasoning": "Test",
        "risk_assessment": "medium", "confidence": confidence,
        "adjustments": [],
    }


class TestRiskGuardTraderIntegration:
    """Test RiskGuard and Trader interaction"""

    @pytest.fixture
    def risk_guard(self):
        with patch.dict(os.environ, {"MAX_DAILY_LOSS": "500", "MAX_POSITION_RATIO": "0.5"}):
            from risk_guard import RiskGuard
            return RiskGuard()

    @pytest.fixture
    def mock_trader_data(self):
        return {
            "account": {
                "cash": 100000.0, "portfolio_value": 100000.0,
                "buying_power": 200000.0, "equity": 100000.0, "daily_pnl": -200.0,
            },
            "positions": [
                {"symbol": "MSTR", "qty": 10.0, "avg_entry_price": 340.0,
                 "market_value": 3500.0, "unrealized_pl": 100.0}
            ],
        }

    def test_risk_check_with_real_trader_data(self, risk_guard, mock_trader_data):
        """Test risk check with realistic trader data"""
        decision = {
            "action": "buy", "confidence": 80, "stop_loss": 300,
            "position_size_pct": 10, "quantity": 10,
        }
        portfolio = {
            "daily_pnl": mock_trader_data["account"]["daily_pnl"],
            "positions": mock_trader_data["positions"],
        }
        result = risk_guard.check(
            decision, portfolio, price=350.0, equity=mock_trader_data["account"]["equity"],
        )
        assert result.allowed is True

    def test_risk_caps_oversized_order_quantity(self, risk_guard, mock_trader_data):
        """Individual trades are capped at max_single_trade_pct, so an
        oversized quantity is shrunk rather than outright blocked."""
        decision = {
            "action": "buy", "confidence": 80, "stop_loss": 300,
            "position_size_pct": 50, "quantity": 200,
        }
        portfolio = {
            "daily_pnl": mock_trader_data["account"]["daily_pnl"],
            "positions": mock_trader_data["positions"],
        }
        result = risk_guard.check(
            decision, portfolio, price=350.0, equity=mock_trader_data["account"]["equity"],
        )
        assert result.allowed is True
        assert decision["quantity"] < 200
        assert any(a["field"] == "quantity" for a in result.adjustments)

    def test_risk_blocks_after_loss(self, risk_guard, mock_trader_data):
        """Test risk guard blocks trading after significant loss"""
        mock_trader_data["account"]["daily_pnl"] = -600.0
        decision = {
            "action": "buy", "confidence": 80, "stop_loss": 300,
            "position_size_pct": 10, "quantity": 1,
        }
        portfolio = {
            "daily_pnl": mock_trader_data["account"]["daily_pnl"],
            "positions": mock_trader_data["positions"],
        }
        result = risk_guard.check(
            decision, portfolio, price=350.0, equity=mock_trader_data["account"]["equity"],
        )
        assert result.allowed is False
        assert result.reason == "daily_loss_limit_reached"


class TestRiskGuardOpusIntegration:
    """Test Opus decisions flowing through the new check() method"""

    @pytest.fixture
    def risk_guard(self):
        with patch.dict(os.environ, {
            "MAX_DAILY_LOSS": "500", "MAX_POSITION_RATIO": "0.5",
            "MIN_CONFIDENCE": "40",
        }):
            from risk_guard import RiskGuard
            return RiskGuard()

    def test_opus_buy_with_stop_loss_passes(self, risk_guard):
        decision = _opus_decision(action="buy", confidence=80)
        result = risk_guard.check(decision, {"daily_pnl": 0}, price=350.0, equity=100000.0)
        assert result.allowed is True

    def test_opus_low_confidence_blocked(self, risk_guard):
        decision = _opus_decision(action="buy", confidence=30)
        result = risk_guard.check(decision, {"daily_pnl": 0}, price=350.0, equity=100000.0)
        assert result.allowed is False
        assert "confidence" in result.reason.lower()

    def test_opus_buy_no_stop_loss_blocked(self, risk_guard):
        decision = _opus_decision(action="buy")
        decision["stop_loss"] = None
        result = risk_guard.check(decision, {"daily_pnl": 0}, price=350.0, equity=100000.0)
        assert result.allowed is False
        assert "stop_loss" in result.reason.lower()

    def test_opus_hold_passes(self, risk_guard):
        decision = _opus_decision(action="hold", quantity=0)
        result = risk_guard.check(decision, {"daily_pnl": 0})
        assert result.allowed is True

    def test_opus_sell_always_passes(self, risk_guard):
        decision = _opus_decision(action="sell")
        decision["stop_loss"] = None
        result = risk_guard.check(decision, {"daily_pnl": 0})
        assert result.allowed is True


class TestDatabaseIntegration:
    """DynamoDB data-layer integration (moto-backed)."""

    @pytest.fixture
    def dyn(self):
        import db.dynamo as d
        return d

    def test_trade_and_pipeline_consistency(self, dyn):
        """The logged trade matches the decision recorded for the same cycle"""
        decision = _opus_decision()
        dyn.log_pipeline(
            cycle_id="cycle-123",
            decision_engine="opus",
            grok_output=_grok_report(),
            opus_output=decision,
            rg_passed=True,
            order_submitted=True,
            alpaca_order_id="order-123",
        )
        dyn.log_trade(
            cycle_id="cycle-123", symbol="MSTR", action="buy", quantity=10.0,
            price=350.0, order_type="market", status="filled",
            alpaca_order_id="order-123", stop_loss=330.0, take_profit=400.0,
        )

        logged = dyn.get_pipeline_logs(1)[0]
        trade = dyn.get_trades(1)[0]

        assert logged["cycle_id"] == trade["cycle_id"]
        assert logged["opus_output"]["action"] == trade["action"]
        assert logged["opus_output"]["symbol"] == trade["symbol"]
        assert logged["alpaca_order_id"] == trade["alpaca_order_id"]

    def test_pipeline_log_creation(self, dyn):
        """A full cycle round-trips every stage"""
        dyn.log_pipeline(
            cycle_id="test-uuid-123",
            decision_engine="opus",
            grok_input={"market_data": {"MSTR": {"price": 350}}},
            grok_output=_grok_report(),
            grok_latency_ms=120,
            opus_output=_opus_decision(),
            opus_latency_ms=800,
            rg_passed=True,
            order_submitted=True,
            alpaca_order_id="order-123",
            execution_result={"status": "filled"},
        )

        saved = dyn.get_pipeline_logs(1)[0]
        assert saved["cycle_id"] == "test-uuid-123"
        assert saved["grok_latency_ms"] == 120
        assert saved["opus_output"]["action"] == "buy"
        assert saved["order_submitted"] is True
        assert saved["decision_engine"] == "opus"

    def test_pipeline_log_decision_skipped(self, dyn):
        """A skipped cycle records the Grok report and no decision"""
        dyn.log_pipeline(
            cycle_id="skip-uuid",
            grok_output=_grok_report(significant=False),
            grok_latency_ms=80,
            opus_skipped=True,
        )

        saved = dyn.get_pipeline_logs(1)[0]
        assert saved["opus_skipped"] is True
        assert saved["opus_output"] is None

    def test_trade_with_stop_loss_take_profit(self, dyn):
        dyn.log_trade(
            cycle_id="cycle-123", symbol="MSTR", action="buy", quantity=10.0,
            price=350.0, order_type="market", status="filled",
            alpaca_order_id="order-sl", stop_loss=330.0, take_profit=400.0,
        )

        saved = dyn.get_trades(1)[0]
        assert saved["stop_loss"] == 330.0
        assert saved["take_profit"] == 400.0
        assert saved["cycle_id"] == "cycle-123"

    def test_system_state_persistence(self, dyn):
        dyn.set_scheduler_state(True)
        assert dyn.get_scheduler_state() is True

        dyn.set_scheduler_state(False)
        assert dyn.get_scheduler_state() is False


class TestAPIIntegration:
    """Test API endpoints with real component integration"""

    SECRET = "test_shared_secret"  # matches conftest API_SHARED_SECRET

    @pytest.fixture
    def client(self, monkeypatch):
        from fastapi.testclient import TestClient

        import app as app_module

        mock_trader = MagicMock()
        mock_trader.get_account.return_value = {
            "cash": 100000.0, "portfolio_value": 100000.0,
            "buying_power": 200000.0, "equity": 100000.0, "daily_pnl": 500.0,
        }
        mock_trader.get_positions.return_value = [
            {"symbol": "MSTR", "qty": 10.0, "market_value": 3500.0}
        ]
        mock_notifier = MagicMock()
        mock_notifier.notify_system_stop = AsyncMock()
        mock_notifier.notify_alert = AsyncMock()

        monkeypatch.setattr(app_module, "trader", mock_trader)
        monkeypatch.setattr(app_module, "notifier", mock_notifier)
        return TestClient(app_module.app)

    def test_health_reflects_scheduler_state(self, client):
        response = client.get("/health")
        assert response.status_code == 200
        assert "scheduler_running" in response.json()

    def test_status_includes_all_data(self, client):
        response = client.get("/status", headers={"x-api-key": self.SECRET})
        assert response.status_code == 200
        data = response.json()
        assert data["account"]["cash"] == 100000.0
        assert len(data["positions"]) == 1

    def test_stop_start_cycle(self, client):
        import db.dynamo as dyn
        headers = {"x-api-key": self.SECRET}

        stop_response = client.post("/stop", headers=headers)
        assert stop_response.status_code == 200
        assert stop_response.json()["status"] == "stopped"
        assert dyn.get_scheduler_state() is False

        start_response = client.post("/start", headers=headers)
        assert start_response.status_code == 200
        assert start_response.json()["status"] == "running"
        assert dyn.get_scheduler_state() is True

    def test_mutating_endpoints_require_secret(self, client):
        assert client.post("/stop").status_code == 401
        assert client.post("/start").status_code == 401

    def test_pipeline_endpoint_returns_logs(self, client):
        import db.dynamo as dyn
        dyn.log_pipeline(cycle_id="c1", decision_engine="opus", grok_latency_ms=90)

        response = client.get("/pipeline", headers={"x-api-key": self.SECRET})
        assert response.status_code == 200
        logs = response.json()["logs"]
        assert len(logs) == 1
        assert logs[0]["cycle_id"] == "c1"


class TestNotificationIntegration:
    """Test notification integration with trading events"""

    @pytest.fixture
    def mock_notifier(self):
        n = MagicMock()
        n.notify_trade = AsyncMock()
        n.notify_alert = AsyncMock()
        n.notify_system_stop = AsyncMock()
        n.send_pipeline_log = AsyncMock()
        return n

    @pytest.mark.asyncio
    async def test_grok_stage_sends_discord_log(self, mock_notifier):
        """Test Grok stage sends Discord pipeline log"""
        mock_trader = MagicMock()
        mock_trader.get_account.return_value = {
            "cash": 100000.0, "portfolio_value": 100000.0,
            "buying_power": 200000.0, "equity": 100000.0,
            "last_equity": 100000.0, "daily_pnl": 0.0,
        }
        mock_trader.get_positions.return_value = []
        mock_trader.get_market_data.return_value = {"MSTR": {"price": 350.0}}
        mock_trader.execute_order.return_value = {
            "order_id": "order-123", "status": "filled",
        }

        mock_grok = MagicMock()
        mock_grok.collect_market_report.return_value = (_grok_report(), 120)

        mock_opus = MagicMock()
        mock_opus.analyze.return_value = (_opus_decision(), 800)

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

        # Verify pipeline logs sent for grok, opus, execution
        stages_logged = [c.args[1] for c in mock_notifier.send_pipeline_log.call_args_list]
        assert "grok" in stages_logged
        assert "opus_decision" in stages_logged
        assert "execution" in stages_logged

    @pytest.mark.asyncio
    async def test_system_stop_triggers_notification(self, mock_notifier):
        """Test system stop sends notification"""
        mock_trader = MagicMock()
        mock_trader.get_account.return_value = {
            "cash": 100000.0, "portfolio_value": 100000.0,
            "buying_power": 200000.0, "equity": 100000.0,
            "last_equity": 100000.0, "daily_pnl": -600.0,
        }
        mock_trader.get_positions.return_value = []

        with patch("trading_core.trader", mock_trader), \
             patch("trading_core.notifier", mock_notifier), \
             patch("trading_core.guard") as mock_guard, \
             patch("trading_core.set_scheduler_state"):
            mock_guard.check_system_health.return_value = MagicMock(
                allowed=False, reason="Daily loss limit"
            )

            from trading_core import trading_cycle
            await trading_cycle()

        mock_notifier.notify_system_stop.assert_called_once()


class TestPipelineDataFlow:
    """Test complete pipeline data flow"""

    @pytest.mark.asyncio
    async def test_grok_report_fed_to_opus(self):
        """Test Grok output is correctly passed to Opus"""
        mock_trader = MagicMock()
        mock_trader.get_account.return_value = {
            "cash": 100000.0, "portfolio_value": 100000.0,
            "buying_power": 200000.0, "equity": 100000.0,
            "last_equity": 100000.0, "daily_pnl": 0.0,
        }
        mock_trader.get_positions.return_value = []
        mock_trader.get_market_data.return_value = {
            "MSTR": {"price": 350.0, "change_5d": "+5%", "volume": 1000000},
        }
        mock_trader.execute_order.return_value = {
            "order_id": "o1", "status": "filled",
        }

        grok_report = _grok_report()
        mock_grok = MagicMock()
        mock_grok.collect_market_report.return_value = (grok_report, 100)

        mock_opus = MagicMock()
        mock_opus.analyze.return_value = (_opus_decision(), 800)

        mock_notifier = MagicMock()
        mock_notifier.send_pipeline_log = AsyncMock()
        mock_notifier.notify_alert = AsyncMock()

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

        # Verify Opus received the grok_report
        opus_call_kwargs = mock_opus.analyze.call_args[1]
        assert "grok_report" in opus_call_kwargs
        assert opus_call_kwargs["grok_report"] == grok_report
        assert opus_call_kwargs["balance"] == 100000.0

    @pytest.mark.asyncio
    async def test_opus_decision_fed_to_risk_guard(self):
        """Test Opus decision is correctly validated by Risk Guard"""
        mock_trader = MagicMock()
        mock_trader.get_account.return_value = {
            "cash": 100000.0, "portfolio_value": 100000.0,
            "buying_power": 200000.0, "equity": 100000.0,
            "last_equity": 100000.0, "daily_pnl": 0.0,
        }
        mock_trader.get_positions.return_value = []
        mock_trader.get_market_data.return_value = {"MSTR": {"price": 350.0}}
        mock_trader.execute_order.return_value = {
            "order_id": "o1", "status": "filled",
        }

        mock_grok = MagicMock()
        mock_grok.collect_market_report.return_value = (_grok_report(), 100)

        decision = _opus_decision()
        mock_opus = MagicMock()
        mock_opus.analyze.return_value = (decision, 800)

        mock_notifier = MagicMock()
        mock_notifier.send_pipeline_log = AsyncMock()
        mock_notifier.notify_alert = AsyncMock()

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

        # Verify guard.check() received Opus's decision (it passes through
        # decision_schema.validate_decision first, which returns a rebuilt
        # dict - same values, plus "decision_engine" added afterward - so
        # compare per-field rather than by dict identity/equality).
        mock_guard.check.assert_called_once()
        received = mock_guard.check.call_args[0][0]
        for key, value in decision.items():
            assert received[key] == value, key


class TestErrorHandlingIntegration:
    """Test error handling across components"""

    @pytest.mark.asyncio
    async def test_trader_exception_handled_gracefully(self):
        """Test trader exceptions don't crash the cycle"""
        mock_trader = MagicMock()
        mock_trader.get_account.side_effect = Exception("API connection failed")

        mock_notifier = MagicMock()
        mock_notifier.notify_alert = AsyncMock()

        with patch("trading_core.trader", mock_trader), \
             patch("trading_core.notifier", mock_notifier):
            from trading_core import trading_cycle
            await trading_cycle()

        mock_notifier.notify_alert.assert_called()

    @pytest.mark.asyncio
    async def test_grok_exception_handled_gracefully(self):
        """Test Grok exceptions don't crash the cycle"""
        mock_trader = MagicMock()
        mock_trader.get_account.return_value = {
            "cash": 100000.0, "portfolio_value": 100000.0,
            "buying_power": 200000.0, "equity": 100000.0,
            "last_equity": 100000.0, "daily_pnl": 0.0,
        }
        mock_trader.get_positions.return_value = []
        mock_trader.get_market_data.return_value = {"MSTR": {"price": 350.0}}

        mock_grok = MagicMock()
        mock_grok.collect_market_report.side_effect = Exception("Grok API error")

        mock_notifier = MagicMock()
        mock_notifier.notify_alert = AsyncMock()

        with patch("trading_core.trader", mock_trader), \
             patch("trading_core.grok", mock_grok), \
             patch("trading_core.notifier", mock_notifier), \
             patch("trading_core.guard") as mock_guard:
            mock_guard.check_system_health.return_value = MagicMock(allowed=True)
            from trading_core import trading_cycle
            await trading_cycle()

        mock_notifier.notify_alert.assert_called()

    @pytest.mark.asyncio
    async def test_notification_failure_doesnt_block_trade(self):
        """Test notification failure doesn't prevent trade execution"""
        mock_trader = MagicMock()
        mock_trader.get_account.return_value = {
            "cash": 100000.0, "portfolio_value": 100000.0,
            "buying_power": 200000.0, "equity": 100000.0,
            "last_equity": 100000.0, "daily_pnl": 0.0,
        }
        mock_trader.get_positions.return_value = []
        mock_trader.get_market_data.return_value = {"MSTR": {"price": 350.0}}
        mock_trader.execute_order.return_value = {
            "order_id": "order-123", "status": "filled",
        }

        mock_grok = MagicMock()
        mock_grok.collect_market_report.return_value = (_grok_report(), 100)

        mock_opus = MagicMock()
        mock_opus.analyze.return_value = (_opus_decision(), 800)

        mock_notifier = MagicMock()
        mock_notifier.send_pipeline_log = AsyncMock(side_effect=Exception("Discord error"))
        mock_notifier.notify_alert = AsyncMock()

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
