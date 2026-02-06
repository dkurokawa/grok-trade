"""Integration tests - Component interaction and data flow for 4-stage pipeline"""
import pytest
import os
from unittest.mock import patch, MagicMock, AsyncMock
from datetime import datetime, date
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Set environment before imports
os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["ALPACA_API_KEY"] = "test_key"
os.environ["ALPACA_SECRET_KEY"] = "test_secret"
os.environ["ALPACA_PAPER"] = "true"
os.environ["GROK_API_KEY"] = "test_grok_key"
os.environ["ANTHROPIC_API_KEY"] = "test_anthropic_key"


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
        result = risk_guard.check_order(
            action="buy", symbol="MSTR", quantity=10, price=350.0,
            account_balance=mock_trader_data["account"]["cash"],
            current_positions=mock_trader_data["positions"],
            daily_pnl=mock_trader_data["account"]["daily_pnl"],
        )
        assert result.allowed is True

    def test_risk_blocks_large_order(self, risk_guard, mock_trader_data):
        """Test that risk guard blocks oversized orders"""
        result = risk_guard.check_order(
            action="buy", symbol="MSTR", quantity=200, price=350.0,
            account_balance=mock_trader_data["account"]["cash"],
            current_positions=mock_trader_data["positions"],
            daily_pnl=mock_trader_data["account"]["daily_pnl"],
        )
        assert result.allowed is False
        assert "Position ratio" in result.reason

    def test_risk_blocks_after_loss(self, risk_guard, mock_trader_data):
        """Test risk guard blocks trading after significant loss"""
        mock_trader_data["account"]["daily_pnl"] = -600.0
        result = risk_guard.check_order(
            action="buy", symbol="MSTR", quantity=1, price=350.0,
            account_balance=mock_trader_data["account"]["cash"],
            current_positions=mock_trader_data["positions"],
            daily_pnl=mock_trader_data["account"]["daily_pnl"],
        )
        assert result.allowed is False
        assert "Daily loss limit" in result.reason


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
        result = risk_guard.check(decision, {"daily_pnl": 0})
        assert result.allowed is True

    def test_opus_low_confidence_blocked(self, risk_guard):
        decision = _opus_decision(action="buy", confidence=30)
        result = risk_guard.check(decision, {"daily_pnl": 0})
        assert result.allowed is False
        assert "confidence" in result.reason.lower()

    def test_opus_buy_no_stop_loss_blocked(self, risk_guard):
        decision = _opus_decision(action="buy")
        decision["stop_loss"] = None
        result = risk_guard.check(decision, {"daily_pnl": 0})
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
    """Test database operations integration"""

    @pytest.fixture
    def db_session(self):
        from db.models import Base
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        Session = sessionmaker(bind=engine)
        session = Session()
        yield session
        session.close()

    def test_trade_decision_consistency(self, db_session):
        from db.models import Trade, Decision
        decision = Decision(
            market_context={"balance": 100000},
            grok_response='{"action": "buy"}',
            parsed_action={"action": "buy", "symbol": "MSTR", "quantity": 10},
            executed=True,
        )
        db_session.add(decision)

        trade = Trade(
            symbol="MSTR", action="buy", quantity=10.0, price=350.0,
            order_type="market", status="filled", alpaca_order_id="order-123",
        )
        db_session.add(trade)
        db_session.commit()

        saved_decision = db_session.query(Decision).first()
        saved_trade = db_session.query(Trade).first()
        assert saved_decision.parsed_action["action"] == saved_trade.action
        assert saved_decision.parsed_action["symbol"] == saved_trade.symbol

    def test_pipeline_log_creation(self, db_session):
        """Test PipelineLog stores full cycle data"""
        from db.models import PipelineLog
        log = PipelineLog(
            cycle_id="test-uuid-123",
            grok_input={"market_data": {"MSTR": {"price": 350}}},
            grok_output=_grok_report(),
            grok_latency_ms=120,
            opus_output=_opus_decision(),
            opus_latency_ms=800,
            opus_adjustments=[],
            risk_guard_passed=True,
            risk_guard_adjustments=[],
            order_submitted=True,
            alpaca_order_id="order-123",
            execution_result={"status": "filled"},
        )
        db_session.add(log)
        db_session.commit()

        saved = db_session.query(PipelineLog).first()
        assert saved.cycle_id == "test-uuid-123"
        assert saved.grok_latency_ms == 120
        assert saved.opus_output["action"] == "buy"
        assert saved.order_submitted is True

    def test_pipeline_log_opus_skipped(self, db_session):
        """Test PipelineLog when Opus is skipped"""
        from db.models import PipelineLog
        log = PipelineLog(
            cycle_id="skip-uuid",
            grok_output=_grok_report(significant=False),
            grok_latency_ms=80,
            opus_skipped=True,
        )
        db_session.add(log)
        db_session.commit()

        saved = db_session.query(PipelineLog).first()
        assert saved.opus_skipped is True
        assert saved.opus_output is None

    def test_trade_with_stop_loss_take_profit(self, db_session):
        """Test Trade with new stop_loss / take_profit fields"""
        from db.models import Trade
        trade = Trade(
            symbol="MSTR", action="buy", quantity=10.0, price=350.0,
            order_type="market", status="filled", alpaca_order_id="order-sl",
            cycle_id="cycle-123", stop_loss=330.0, take_profit=400.0,
        )
        db_session.add(trade)
        db_session.commit()

        saved = db_session.query(Trade).first()
        assert saved.stop_loss == 330.0
        assert saved.take_profit == 400.0
        assert saved.cycle_id == "cycle-123"

    def test_system_state_persistence(self, db_session):
        from db.models import SystemState
        state = SystemState(
            key="scheduler_running",
            value={"running": True, "last_cycle": datetime.now().isoformat()},
        )
        db_session.add(state)
        db_session.commit()

        saved = db_session.query(SystemState).filter_by(key="scheduler_running").first()
        assert saved.value["running"] is True

        saved.value = {"running": False, "reason": "Manual stop"}
        db_session.commit()

        updated = db_session.query(SystemState).filter_by(key="scheduler_running").first()
        assert updated.value["running"] is False


class TestAPIIntegration:
    """Test API endpoints with real component integration"""

    @pytest.fixture
    def client(self):
        from fastapi.testclient import TestClient
        mock_trader = MagicMock()
        mock_trader.get_account.return_value = {
            "cash": 100000.0, "portfolio_value": 100000.0,
            "buying_power": 200000.0, "equity": 100000.0, "daily_pnl": 500.0,
        }
        mock_trader.get_positions.return_value = [
            {"symbol": "MSTR", "qty": 10.0, "market_value": 3500.0}
        ]

        with patch("main.GrokClient"), \
             patch("main.OpusClient"), \
             patch("main.Trader", return_value=mock_trader), \
             patch("main.DiscordNotifier"), \
             patch("main.init_db"), \
             patch("main.get_scheduler_state", return_value=True), \
             patch("main.trading_cycle", new_callable=AsyncMock):
            from importlib import reload
            import main
            reload(main)
            main.trader = mock_trader
            with TestClient(main.app) as c:
                yield c

    def test_health_reflects_scheduler_state(self, client):
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert "scheduler_running" in data

    def test_status_includes_all_data(self, client):
        response = client.get("/status")
        assert response.status_code == 200
        data = response.json()
        assert "account" in data
        assert "positions" in data
        assert data["account"]["cash"] == 100000.0
        assert len(data["positions"]) == 1

    def test_stop_start_cycle(self, client):
        with patch("main.set_scheduler_state"):
            stop_response = client.post("/stop")
            assert stop_response.status_code == 200
            assert stop_response.json()["status"] == "stopped"

            start_response = client.post("/start")
            assert start_response.status_code == 200
            assert start_response.json()["status"] == "running"


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

        with patch("main.trader", mock_trader), \
             patch("main.notifier", mock_notifier), \
             patch("main.guard") as mock_guard, \
             patch("main.scheduler"):
            mock_guard.check_system_health.return_value = MagicMock(
                allowed=False, reason="Daily loss limit"
            )

            from main import trading_cycle
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

        # Verify guard.check() received the decision
        mock_guard.check.assert_called_once()
        check_args = mock_guard.check.call_args
        assert check_args[0][0] == decision  # first positional arg


class TestErrorHandlingIntegration:
    """Test error handling across components"""

    @pytest.mark.asyncio
    async def test_trader_exception_handled_gracefully(self):
        """Test trader exceptions don't crash the cycle"""
        mock_trader = MagicMock()
        mock_trader.get_account.side_effect = Exception("API connection failed")

        mock_notifier = MagicMock()
        mock_notifier.notify_alert = AsyncMock()

        with patch("main.trader", mock_trader), \
             patch("main.notifier", mock_notifier):
            from main import trading_cycle
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

        with patch("main.trader", mock_trader), \
             patch("main.grok", mock_grok), \
             patch("main.notifier", mock_notifier), \
             patch("main.guard") as mock_guard:
            mock_guard.check_system_health.return_value = MagicMock(allowed=True)
            from main import trading_cycle
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
