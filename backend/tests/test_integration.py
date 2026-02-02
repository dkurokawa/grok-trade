"""Integration tests - Component interaction and data flow"""
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


class TestRiskGuardTraderIntegration:
    """Test RiskGuard and Trader interaction"""

    @pytest.fixture
    def risk_guard(self):
        with patch.dict(os.environ, {"MAX_DAILY_LOSS": "500", "MAX_POSITION_RATIO": "0.5"}):
            from risk_guard import RiskGuard
            return RiskGuard()

    @pytest.fixture
    def mock_trader_data(self):
        """Simulated trader data"""
        return {
            "account": {
                "cash": 100000.0,
                "portfolio_value": 100000.0,
                "buying_power": 200000.0,
                "equity": 100000.0,
                "daily_pnl": -200.0
            },
            "positions": [
                {
                    "symbol": "MSTR",
                    "qty": 10.0,
                    "avg_entry_price": 340.0,
                    "market_value": 3500.0,
                    "unrealized_pl": 100.0
                }
            ]
        }

    def test_risk_check_with_real_trader_data(self, risk_guard, mock_trader_data):
        """Test risk check with realistic trader data"""
        result = risk_guard.check_order(
            action="buy",
            symbol="MSTR",
            quantity=10,
            price=350.0,
            account_balance=mock_trader_data["account"]["cash"],
            current_positions=mock_trader_data["positions"],
            daily_pnl=mock_trader_data["account"]["daily_pnl"]
        )
        # Should be allowed: 3500 + 3500 = 7000 / 100000 = 7% < 50%
        assert result.allowed is True

    def test_risk_blocks_large_order(self, risk_guard, mock_trader_data):
        """Test that risk guard blocks oversized orders"""
        result = risk_guard.check_order(
            action="buy",
            symbol="MSTR",
            quantity=200,  # Very large order
            price=350.0,
            account_balance=mock_trader_data["account"]["cash"],
            current_positions=mock_trader_data["positions"],
            daily_pnl=mock_trader_data["account"]["daily_pnl"]
        )
        # Should be blocked: 3500 + 70000 = 73500 / 100000 = 73.5% > 50%
        assert result.allowed is False
        assert "Position ratio" in result.reason

    def test_risk_blocks_after_loss(self, risk_guard, mock_trader_data):
        """Test risk guard blocks trading after significant loss"""
        mock_trader_data["account"]["daily_pnl"] = -600.0  # Exceeds $500 limit

        result = risk_guard.check_order(
            action="buy",
            symbol="MSTR",
            quantity=1,
            price=350.0,
            account_balance=mock_trader_data["account"]["cash"],
            current_positions=mock_trader_data["positions"],
            daily_pnl=mock_trader_data["account"]["daily_pnl"]
        )
        assert result.allowed is False
        assert "Daily loss limit" in result.reason


class TestGrokClientRiskGuardIntegration:
    """Test Grok decisions flowing through RiskGuard"""

    @pytest.fixture
    def risk_guard(self):
        with patch.dict(os.environ, {"MAX_DAILY_LOSS": "500", "MAX_POSITION_RATIO": "0.5"}):
            from risk_guard import RiskGuard
            return RiskGuard()

    def test_grok_buy_decision_validated(self, risk_guard):
        """Test Grok buy decision passes risk validation"""
        grok_decision = {
            "action": "buy",
            "symbol": "MSTR",
            "quantity": 10,
            "reasoning": "Bullish trend",
            "confidence": 80,
            "order_type": "market"
        }

        result = risk_guard.check_order(
            action=grok_decision["action"],
            symbol=grok_decision["symbol"],
            quantity=grok_decision["quantity"],
            price=350.0,
            account_balance=100000.0,
            current_positions=[],
            daily_pnl=0.0
        )
        assert result.allowed is True

    def test_grok_sell_always_allowed(self, risk_guard):
        """Test Grok sell decision always passes"""
        grok_decision = {
            "action": "sell",
            "symbol": "MSTR",
            "quantity": 100,
            "reasoning": "Taking profit",
            "confidence": 90,
            "order_type": "market"
        }

        result = risk_guard.check_order(
            action=grok_decision["action"],
            symbol=grok_decision["symbol"],
            quantity=grok_decision["quantity"],
            price=350.0,
            account_balance=100000.0,
            current_positions=[{"symbol": "MSTR", "market_value": 35000.0}],
            daily_pnl=0.0
        )
        assert result.allowed is True

    def test_grok_hold_skips_execution(self, risk_guard):
        """Test Grok hold decision is allowed but skips trade"""
        grok_decision = {
            "action": "hold",
            "symbol": "MSTR",
            "quantity": 0,
            "reasoning": "Waiting",
            "confidence": 60,
            "order_type": "market"
        }

        result = risk_guard.check_order(
            action=grok_decision["action"],
            symbol=grok_decision["symbol"],
            quantity=grok_decision["quantity"],
            price=350.0,
            account_balance=100000.0,
            current_positions=[],
            daily_pnl=0.0
        )
        assert result.allowed is True


class TestDatabaseIntegration:
    """Test database operations integration"""

    @pytest.fixture
    def db_session(self):
        """Create in-memory database session"""
        from db.models import Base, Trade, Decision, DailySummary, SystemState
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        Session = sessionmaker(bind=engine)
        session = Session()
        yield session
        session.close()

    def test_trade_decision_consistency(self, db_session):
        """Test trade and decision records are consistent"""
        from db.models import Trade, Decision

        # Log a decision
        decision = Decision(
            market_context={"balance": 100000, "positions": []},
            grok_response='{"action": "buy", "symbol": "MSTR", "quantity": 10}',
            parsed_action={"action": "buy", "symbol": "MSTR", "quantity": 10},
            executed=True,
            blocked_reason=None
        )
        db_session.add(decision)

        # Log corresponding trade
        trade = Trade(
            symbol="MSTR",
            action="buy",
            quantity=10.0,
            price=350.0,
            order_type="market",
            status="filled",
            alpaca_order_id="order-123"
        )
        db_session.add(trade)
        db_session.commit()

        # Verify both records exist and match
        saved_decision = db_session.query(Decision).first()
        saved_trade = db_session.query(Trade).first()

        assert saved_decision.parsed_action["action"] == saved_trade.action
        assert saved_decision.parsed_action["symbol"] == saved_trade.symbol
        assert saved_decision.parsed_action["quantity"] == saved_trade.quantity
        assert saved_decision.executed is True

    def test_blocked_decision_no_trade(self, db_session):
        """Test blocked decisions don't create trades"""
        from db.models import Trade, Decision

        # Log a blocked decision
        decision = Decision(
            market_context={"balance": 100000, "positions": []},
            grok_response='{"action": "buy", "symbol": "MSTR", "quantity": 100}',
            parsed_action={"action": "buy", "symbol": "MSTR", "quantity": 100},
            executed=False,
            blocked_reason="Position ratio would exceed limit"
        )
        db_session.add(decision)
        db_session.commit()

        # Verify no trade was created
        trades = db_session.query(Trade).all()
        decisions = db_session.query(Decision).all()

        assert len(trades) == 0
        assert len(decisions) == 1
        assert decisions[0].executed is False
        assert decisions[0].blocked_reason is not None

    def test_system_state_persistence(self, db_session):
        """Test system state persists across operations"""
        from db.models import SystemState

        # Set scheduler state
        state = SystemState(
            key="scheduler_running",
            value={"running": True, "last_cycle": datetime.now().isoformat()}
        )
        db_session.add(state)
        db_session.commit()

        # Retrieve and verify
        saved_state = db_session.query(SystemState).filter_by(key="scheduler_running").first()
        assert saved_state.value["running"] is True

        # Update state
        saved_state.value = {"running": False, "reason": "Manual stop"}
        db_session.commit()

        # Verify update
        updated_state = db_session.query(SystemState).filter_by(key="scheduler_running").first()
        assert updated_state.value["running"] is False

    def test_daily_summary_aggregation(self, db_session):
        """Test daily summary calculation"""
        from db.models import Trade, DailySummary

        # Create multiple trades for today
        for i in range(5):
            trade = Trade(
                symbol="MSTR",
                action="buy" if i % 2 == 0 else "sell",
                quantity=10.0,
                price=350.0 + i,
                order_type="market",
                status="filled",
                alpaca_order_id=f"order-{i}"
            )
            db_session.add(trade)
        db_session.commit()

        # Create daily summary (using actual model fields)
        today_trades = db_session.query(Trade).all()

        summary = DailySummary(
            date=date.today(),
            starting_balance=100000.0,
            ending_balance=100500.0,
            pnl=500.0,
            trade_count=len(today_trades),
            win_rate=0.6  # 60% win rate
        )
        db_session.add(summary)
        db_session.commit()

        # Verify summary
        saved_summary = db_session.query(DailySummary).first()
        assert saved_summary.trade_count == 5
        assert saved_summary.pnl == 500.0


class TestAPIIntegration:
    """Test API endpoints with real component integration"""

    @pytest.fixture
    def client(self):
        """Create test client with minimal mocking"""
        from fastapi.testclient import TestClient

        mock_trader = MagicMock()
        mock_trader.get_account.return_value = {
            "cash": 100000.0,
            "portfolio_value": 100000.0,
            "buying_power": 200000.0,
            "equity": 100000.0,
            "daily_pnl": 500.0
        }
        mock_trader.get_positions.return_value = [
            {"symbol": "MSTR", "qty": 10.0, "market_value": 3500.0}
        ]

        with patch("main.GrokClient"):
            with patch("main.Trader", return_value=mock_trader):
                with patch("main.DiscordNotifier"):
                    with patch("main.init_db"):
                        with patch("main.get_scheduler_state", return_value=True):
                            with patch("main.trading_cycle", new_callable=AsyncMock):
                                from importlib import reload
                                import main
                                reload(main)
                                main.trader = mock_trader
                                with TestClient(main.app) as c:
                                    yield c

    def test_health_reflects_scheduler_state(self, client):
        """Test health endpoint reflects scheduler state"""
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert "scheduler_running" in data
        assert "timestamp" in data

    def test_status_includes_all_data(self, client):
        """Test status endpoint includes complete data"""
        response = client.get("/status")
        assert response.status_code == 200
        data = response.json()

        assert "account" in data
        assert "positions" in data
        assert "scheduler_running" in data

        assert data["account"]["cash"] == 100000.0
        assert len(data["positions"]) == 1
        assert data["positions"][0]["symbol"] == "MSTR"

    def test_stop_start_cycle(self, client):
        """Test stop/start API cycle"""
        with patch("main.set_scheduler_state"):
            # Stop
            stop_response = client.post("/stop")
            assert stop_response.status_code == 200
            assert stop_response.json()["status"] == "stopped"

            # Start
            start_response = client.post("/start")
            assert start_response.status_code == 200
            assert start_response.json()["status"] == "running"


class TestNotificationIntegration:
    """Test notification integration with trading events"""

    @pytest.fixture
    def mock_notifier(self):
        notifier = MagicMock()
        notifier.notify_trade = AsyncMock()
        notifier.notify_alert = AsyncMock()
        notifier.notify_system_stop = AsyncMock()
        return notifier

    @pytest.mark.asyncio
    async def test_trade_triggers_notification(self, mock_notifier):
        """Test successful trade sends notification"""
        mock_trader = MagicMock()
        mock_trader.get_account.return_value = {
            "cash": 100000.0, "portfolio_value": 100000.0,
            "buying_power": 200000.0, "equity": 100000.0, "daily_pnl": 0.0
        }
        mock_trader.get_positions.return_value = []
        mock_trader.get_market_data.return_value = {"MSTR": {"price": 350.0}}
        mock_trader.execute_order.return_value = {
            "order_id": "order-123", "status": "filled"
        }

        mock_grok = MagicMock()
        mock_grok.analyze_market.return_value = {
            "action": "buy", "symbol": "MSTR", "quantity": 10,
            "reasoning": "Buy", "confidence": 80, "order_type": "market"
        }

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

        mock_notifier.notify_trade.assert_called_once()

    @pytest.mark.asyncio
    async def test_system_stop_triggers_notification(self, mock_notifier):
        """Test system stop sends notification"""
        mock_trader = MagicMock()
        mock_trader.get_account.return_value = {
            "cash": 100000.0, "portfolio_value": 100000.0,
            "buying_power": 200000.0, "equity": 100000.0, "daily_pnl": -600.0
        }
        mock_trader.get_positions.return_value = []

        with patch("main.trader", mock_trader):
            with patch("main.notifier", mock_notifier):
                with patch("main.guard") as mock_guard:
                    mock_guard.check_system_health.return_value = MagicMock(
                        allowed=False, reason="Daily loss limit"
                    )
                    with patch("main.scheduler"):
                        from main import trading_cycle
                        await trading_cycle()

        mock_notifier.notify_system_stop.assert_called_once()


class TestTradingCycleFlowIntegration:
    """Test complete trading cycle data flow"""

    @pytest.fixture
    def full_mock_setup(self):
        """Create full mock setup for trading cycle"""
        mock_trader = MagicMock()
        mock_trader.get_account.return_value = {
            "cash": 100000.0, "portfolio_value": 100000.0,
            "buying_power": 200000.0, "equity": 100000.0, "daily_pnl": 0.0
        }
        mock_trader.get_positions.return_value = []
        mock_trader.get_market_data.return_value = {
            "MSTR": {"price": 350.0, "change_5d": "+5%", "volume": 1000000},
            "TSLA": {"price": 250.0, "change_5d": "-2%", "volume": 2000000},
            "QQQ": {"price": 420.0, "change_5d": "+1%", "volume": 3000000},
            "SPY": {"price": 485.0, "change_5d": "+0.5%", "volume": 4000000}
        }
        mock_trader.execute_order.return_value = {
            "order_id": "order-123", "symbol": "MSTR", "side": "buy",
            "qty": 10.0, "type": "market", "status": "filled"
        }

        mock_grok = MagicMock()
        mock_grok.analyze_market.return_value = {
            "action": "buy", "symbol": "MSTR", "quantity": 10,
            "reasoning": "BTC momentum strong", "confidence": 80, "order_type": "market"
        }

        mock_notifier = MagicMock()
        mock_notifier.notify_trade = AsyncMock()
        mock_notifier.notify_alert = AsyncMock()
        mock_notifier.notify_system_stop = AsyncMock()

        return mock_trader, mock_grok, mock_notifier

    @pytest.mark.asyncio
    async def test_full_cycle_data_flow(self, full_mock_setup):
        """Test data flows correctly through entire cycle"""
        mock_trader, mock_grok, mock_notifier = full_mock_setup

        with patch("main.trader", mock_trader):
            with patch("main.grok", mock_grok):
                with patch("main.notifier", mock_notifier):
                    with patch("main.guard") as mock_guard:
                        mock_guard.check_system_health.return_value = MagicMock(allowed=True)
                        mock_guard.check_order.return_value = MagicMock(allowed=True)
                        with patch("main.log_decision") as mock_log_decision:
                            with patch("main.log_trade") as mock_log_trade:
                                from main import trading_cycle
                                await trading_cycle()

        # Verify Grok received correct data
        grok_call_args = mock_grok.analyze_market.call_args
        assert grok_call_args[1]["balance"] == 100000.0
        assert "MSTR" in grok_call_args[1]["market_data"]

        # Verify order executed with Grok's decision
        order_call_args = mock_trader.execute_order.call_args
        assert order_call_args[1]["symbol"] == "MSTR"
        assert order_call_args[1]["action"] == "buy"
        assert order_call_args[1]["quantity"] == 10

        # Verify logging called
        mock_log_trade.assert_called_once()
        mock_log_decision.assert_called()

    @pytest.mark.asyncio
    async def test_grok_decision_to_risk_guard_flow(self, full_mock_setup):
        """Test Grok decision is correctly validated by risk guard"""
        mock_trader, mock_grok, mock_notifier = full_mock_setup

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

        # Verify risk guard called with Grok's decision
        risk_call_args = mock_guard.check_order.call_args
        assert risk_call_args[1]["action"] == "buy"
        assert risk_call_args[1]["symbol"] == "MSTR"
        assert risk_call_args[1]["quantity"] == 10
        assert risk_call_args[1]["price"] == 350.0

    @pytest.mark.asyncio
    async def test_notification_contains_trade_details(self, full_mock_setup):
        """Test notification contains correct trade details"""
        mock_trader, mock_grok, mock_notifier = full_mock_setup

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

        # Verify notification called with trade details
        notify_call_args = mock_notifier.notify_trade.call_args
        assert notify_call_args[1]["symbol"] == "MSTR"
        assert notify_call_args[1]["action"] == "buy"
        assert notify_call_args[1]["quantity"] == 10
        assert notify_call_args[1]["price"] == 350.0


class TestMarketDataToGrokIntegration:
    """Test market data formatting for Grok analysis"""

    @pytest.mark.asyncio
    async def test_multiple_symbols_passed_to_grok(self):
        """Test all watchlist symbols' data passed to Grok"""
        mock_trader = MagicMock()
        mock_trader.get_account.return_value = {
            "cash": 100000.0, "portfolio_value": 100000.0,
            "buying_power": 200000.0, "equity": 100000.0, "daily_pnl": 0.0
        }
        mock_trader.get_positions.return_value = []
        mock_trader.get_market_data.return_value = {
            "MSTR": {"price": 350.0, "change_5d": "+5%"},
            "TSLA": {"price": 250.0, "change_5d": "-2%"},
            "QQQ": {"price": 420.0, "change_5d": "+1%"},
            "SPY": {"price": 485.0, "change_5d": "+0.5%"}
        }

        mock_grok = MagicMock()
        mock_grok.analyze_market.return_value = {
            "action": "hold", "symbol": "MSTR", "quantity": 0,
            "reasoning": "Waiting", "confidence": 50, "order_type": "market"
        }

        mock_notifier = MagicMock()

        with patch("main.trader", mock_trader):
            with patch("main.grok", mock_grok):
                with patch("main.notifier", mock_notifier):
                    with patch("main.guard") as mock_guard:
                        mock_guard.check_system_health.return_value = MagicMock(allowed=True)
                        with patch("main.log_decision"):
                            from main import trading_cycle
                            await trading_cycle()

        # Verify all symbols passed to Grok
        grok_call_args = mock_grok.analyze_market.call_args
        market_data = grok_call_args[1]["market_data"]
        assert "MSTR" in market_data
        assert "TSLA" in market_data
        assert "QQQ" in market_data
        assert "SPY" in market_data

    @pytest.mark.asyncio
    async def test_positions_passed_to_grok(self):
        """Test existing positions passed to Grok for context"""
        mock_trader = MagicMock()
        mock_trader.get_account.return_value = {
            "cash": 50000.0, "portfolio_value": 100000.0,
            "buying_power": 100000.0, "equity": 100000.0, "daily_pnl": 500.0
        }
        mock_trader.get_positions.return_value = [
            {"symbol": "MSTR", "qty": 20.0, "market_value": 7000.0, "unrealized_pl": 500.0}
        ]
        mock_trader.get_market_data.return_value = {
            "MSTR": {"price": 350.0, "change_5d": "+5%"}
        }

        mock_grok = MagicMock()
        mock_grok.analyze_market.return_value = {
            "action": "hold", "symbol": "MSTR", "quantity": 0,
            "reasoning": "Already have position", "confidence": 60, "order_type": "market"
        }

        mock_notifier = MagicMock()

        with patch("main.trader", mock_trader):
            with patch("main.grok", mock_grok):
                with patch("main.notifier", mock_notifier):
                    with patch("main.guard") as mock_guard:
                        mock_guard.check_system_health.return_value = MagicMock(allowed=True)
                        with patch("main.log_decision"):
                            from main import trading_cycle
                            await trading_cycle()

        # Verify positions passed to Grok
        grok_call_args = mock_grok.analyze_market.call_args
        positions = grok_call_args[1]["positions"]
        assert len(positions) == 1
        assert positions[0]["symbol"] == "MSTR"
        assert positions[0]["qty"] == 20.0


class TestErrorHandlingIntegration:
    """Test error handling across components"""

    @pytest.mark.asyncio
    async def test_trader_exception_handled_gracefully(self):
        """Test trader exceptions don't crash the cycle"""
        mock_trader = MagicMock()
        mock_trader.get_account.side_effect = Exception("API connection failed")

        mock_notifier = MagicMock()
        mock_notifier.notify_alert = AsyncMock()

        with patch("main.trader", mock_trader):
            with patch("main.notifier", mock_notifier):
                from main import trading_cycle
                # Should not raise
                await trading_cycle()

        # Alert should be sent
        mock_notifier.notify_alert.assert_called()

    @pytest.mark.asyncio
    async def test_grok_exception_handled_gracefully(self):
        """Test Grok exceptions don't crash the cycle"""
        mock_trader = MagicMock()
        mock_trader.get_account.return_value = {
            "cash": 100000.0, "portfolio_value": 100000.0,
            "buying_power": 200000.0, "equity": 100000.0, "daily_pnl": 0.0
        }
        mock_trader.get_positions.return_value = []
        mock_trader.get_market_data.return_value = {"MSTR": {"price": 350.0}}

        mock_grok = MagicMock()
        mock_grok.analyze_market.side_effect = Exception("Grok API error")

        mock_notifier = MagicMock()
        mock_notifier.notify_alert = AsyncMock()

        with patch("main.trader", mock_trader):
            with patch("main.grok", mock_grok):
                with patch("main.notifier", mock_notifier):
                    with patch("main.guard") as mock_guard:
                        mock_guard.check_system_health.return_value = MagicMock(allowed=True)
                        from main import trading_cycle
                        # Should not raise
                        await trading_cycle()

        # Alert should be sent
        mock_notifier.notify_alert.assert_called()

    @pytest.mark.asyncio
    async def test_notification_failure_doesnt_block_trade(self):
        """Test notification failure doesn't prevent trade execution"""
        mock_trader = MagicMock()
        mock_trader.get_account.return_value = {
            "cash": 100000.0, "portfolio_value": 100000.0,
            "buying_power": 200000.0, "equity": 100000.0, "daily_pnl": 0.0
        }
        mock_trader.get_positions.return_value = []
        mock_trader.get_market_data.return_value = {"MSTR": {"price": 350.0}}
        mock_trader.execute_order.return_value = {
            "order_id": "order-123", "status": "filled"
        }

        mock_grok = MagicMock()
        mock_grok.analyze_market.return_value = {
            "action": "buy", "symbol": "MSTR", "quantity": 10,
            "reasoning": "Buy", "confidence": 80, "order_type": "market"
        }

        mock_notifier = MagicMock()
        mock_notifier.notify_trade = AsyncMock(side_effect=Exception("Discord error"))
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
                                # Should not raise even with notification error
                                await trading_cycle()

        # Trade should still have been executed
        mock_trader.execute_order.assert_called_once()
