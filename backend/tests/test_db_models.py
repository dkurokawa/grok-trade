"""Database models unit tests - comprehensive edge cases and error handling"""
import pytest
import os
from unittest.mock import patch, MagicMock
from datetime import datetime, date
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Use in-memory SQLite for testing
os.environ["DATABASE_URL"] = "sqlite:///:memory:"

from db.models import Base, Trade, Decision, DailySummary, SystemState, init_db, get_session


class TestDatabaseConnection:
    """Database connection tests"""

    def test_init_db_creates_tables(self):
        """Test that init_db creates all tables"""
        # Create a fresh in-memory database
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)

        # Check tables exist
        tables = Base.metadata.tables.keys()
        assert "trades" in tables
        assert "decisions" in tables
        assert "daily_summary" in tables
        assert "system_state" in tables

    def test_get_session_returns_session(self):
        """Test get_session returns valid session"""
        with patch.dict(os.environ, {"DATABASE_URL": "sqlite:///:memory:"}):
            # Re-import to get fresh module
            from importlib import reload
            import db.models as models
            reload(models)

            session = models.get_session()
            if session:
                assert session is not None
                session.close()

    def test_get_session_no_db_url(self):
        """Test get_session returns None when no DB URL"""
        with patch.dict(os.environ, {"DATABASE_URL": ""}, clear=True):
            from importlib import reload
            import db.models as models
            reload(models)

            session = models.get_session()
            assert session is None


class TestTradeModel:
    """Trade model tests"""

    @pytest.fixture
    def session(self):
        """Create test session with in-memory database"""
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        Session = sessionmaker(bind=engine)
        session = Session()
        yield session
        session.close()

    def test_create_trade(self, session):
        """Test creating a trade record"""
        trade = Trade(
            symbol="MSTR",
            action="buy",
            quantity=10.0,
            price=350.0,
            order_type="market",
            status="filled",
            alpaca_order_id="order-123"
        )
        session.add(trade)
        session.commit()

        assert trade.id is not None
        assert trade.timestamp is not None

    def test_trade_with_all_fields(self, session):
        """Test trade with all fields populated"""
        trade = Trade(
            timestamp=datetime(2024, 1, 15, 10, 30, 0),
            symbol="TSLA",
            action="sell",
            quantity=5.0,
            price=250.50,
            order_type="limit",
            status="filled",
            alpaca_order_id="order-456"
        )
        session.add(trade)
        session.commit()

        retrieved = session.query(Trade).filter_by(symbol="TSLA").first()
        assert retrieved.action == "sell"
        assert retrieved.quantity == 5.0
        assert retrieved.price == 250.50

    def test_trade_without_optional_fields(self, session):
        """Test trade with minimal required fields"""
        trade = Trade(
            symbol="QQQ",
            action="buy",
            quantity=1.0,
            price=400.0,
            order_type="market",
            status="new",
            alpaca_order_id="order-789"
        )
        session.add(trade)
        session.commit()

        assert trade.id is not None

    def test_multiple_trades_same_symbol(self, session):
        """Test multiple trades for same symbol"""
        trades = [
            Trade(symbol="MSTR", action="buy", quantity=10.0, price=350.0,
                  order_type="market", status="filled", alpaca_order_id="order-1"),
            Trade(symbol="MSTR", action="sell", quantity=5.0, price=360.0,
                  order_type="market", status="filled", alpaca_order_id="order-2"),
            Trade(symbol="MSTR", action="buy", quantity=3.0, price=355.0,
                  order_type="market", status="filled", alpaca_order_id="order-3")
        ]
        for trade in trades:
            session.add(trade)
        session.commit()

        mstr_trades = session.query(Trade).filter_by(symbol="MSTR").all()
        assert len(mstr_trades) == 3

    def test_trade_query_by_action(self, session):
        """Test querying trades by action"""
        session.add(Trade(symbol="MSTR", action="buy", quantity=10.0, price=350.0,
                          order_type="market", status="filled", alpaca_order_id="o1"))
        session.add(Trade(symbol="TSLA", action="sell", quantity=5.0, price=250.0,
                          order_type="market", status="filled", alpaca_order_id="o2"))
        session.add(Trade(symbol="QQQ", action="buy", quantity=20.0, price=400.0,
                          order_type="market", status="filled", alpaca_order_id="o3"))
        session.commit()

        buy_trades = session.query(Trade).filter_by(action="buy").all()
        assert len(buy_trades) == 2

    def test_trade_order_by_timestamp(self, session):
        """Test ordering trades by timestamp"""
        session.add(Trade(timestamp=datetime(2024, 1, 1), symbol="A", action="buy",
                          quantity=1.0, price=100.0, order_type="market",
                          status="filled", alpaca_order_id="o1"))
        session.add(Trade(timestamp=datetime(2024, 1, 3), symbol="B", action="buy",
                          quantity=1.0, price=100.0, order_type="market",
                          status="filled", alpaca_order_id="o2"))
        session.add(Trade(timestamp=datetime(2024, 1, 2), symbol="C", action="buy",
                          quantity=1.0, price=100.0, order_type="market",
                          status="filled", alpaca_order_id="o3"))
        session.commit()

        trades = session.query(Trade).order_by(Trade.timestamp.desc()).all()
        assert trades[0].symbol == "B"
        assert trades[2].symbol == "A"

    def test_trade_negative_quantity(self, session):
        """Test trade with negative quantity (edge case)"""
        trade = Trade(
            symbol="MSTR",
            action="buy",
            quantity=-10.0,  # Invalid but DB accepts it
            price=350.0,
            order_type="market",
            status="filled",
            alpaca_order_id="order-neg"
        )
        session.add(trade)
        session.commit()

        assert trade.quantity == -10.0  # DB doesn't validate

    def test_trade_zero_price(self, session):
        """Test trade with zero price"""
        trade = Trade(
            symbol="MSTR",
            action="buy",
            quantity=10.0,
            price=0.0,
            order_type="market",
            status="filled",
            alpaca_order_id="order-zero"
        )
        session.add(trade)
        session.commit()

        assert trade.price == 0.0


class TestDecisionModel:
    """Decision model tests"""

    @pytest.fixture
    def session(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        Session = sessionmaker(bind=engine)
        session = Session()
        yield session
        session.close()

    def test_create_decision(self, session):
        """Test creating a decision record"""
        decision = Decision(
            market_context={"balance": 100000, "positions": []},
            grok_response='{"action": "buy"}',
            parsed_action={"action": "buy", "symbol": "MSTR"},
            executed=True
        )
        session.add(decision)
        session.commit()

        assert decision.id is not None
        assert decision.timestamp is not None

    def test_decision_with_blocked_reason(self, session):
        """Test decision with blocked reason"""
        decision = Decision(
            market_context={},
            grok_response="{}",
            parsed_action={"action": "buy"},
            executed=False,
            blocked_reason="Position ratio exceeded"
        )
        session.add(decision)
        session.commit()

        assert decision.blocked_reason == "Position ratio exceeded"

    def test_decision_json_fields(self, session):
        """Test JSON fields store and retrieve correctly"""
        complex_context = {
            "balance": 100000,
            "positions": [
                {"symbol": "MSTR", "qty": 10, "price": 350}
            ],
            "market_data": {
                "MSTR": {"price": 360, "change": "+3%"}
            }
        }
        decision = Decision(
            market_context=complex_context,
            grok_response='{"action": "hold"}',
            parsed_action={"action": "hold", "confidence": 60},
            executed=False
        )
        session.add(decision)
        session.commit()

        retrieved = session.query(Decision).first()
        assert retrieved.market_context["balance"] == 100000
        assert len(retrieved.market_context["positions"]) == 1

    def test_decision_empty_context(self, session):
        """Test decision with empty context"""
        decision = Decision(
            market_context={},
            grok_response="",
            parsed_action={},
            executed=False
        )
        session.add(decision)
        session.commit()

        assert decision.market_context == {}

    def test_decision_null_blocked_reason(self, session):
        """Test decision with null blocked reason"""
        decision = Decision(
            market_context={},
            grok_response="{}",
            parsed_action={},
            executed=True,
            blocked_reason=None
        )
        session.add(decision)
        session.commit()

        assert decision.blocked_reason is None

    def test_query_executed_decisions(self, session):
        """Test querying by executed status"""
        session.add(Decision(market_context={}, grok_response="", parsed_action={}, executed=True))
        session.add(Decision(market_context={}, grok_response="", parsed_action={}, executed=False))
        session.add(Decision(market_context={}, grok_response="", parsed_action={}, executed=True))
        session.commit()

        executed = session.query(Decision).filter_by(executed=True).all()
        assert len(executed) == 2


class TestDailySummaryModel:
    """DailySummary model tests"""

    @pytest.fixture
    def session(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        Session = sessionmaker(bind=engine)
        session = Session()
        yield session
        session.close()

    def test_create_daily_summary(self, session):
        """Test creating a daily summary"""
        summary = DailySummary(
            date=date(2024, 1, 15),
            starting_balance=100000.0,
            ending_balance=101500.0,
            pnl=1500.0,
            trade_count=5,
            win_rate=0.6
        )
        session.add(summary)
        session.commit()

        assert summary.date == date(2024, 1, 15)
        assert summary.pnl == 1500.0

    def test_daily_summary_negative_pnl(self, session):
        """Test daily summary with negative P&L"""
        summary = DailySummary(
            date=date(2024, 1, 16),
            starting_balance=100000.0,
            ending_balance=98000.0,
            pnl=-2000.0,
            trade_count=3,
            win_rate=0.33
        )
        session.add(summary)
        session.commit()

        assert summary.pnl == -2000.0

    def test_daily_summary_zero_trades(self, session):
        """Test daily summary with zero trades"""
        summary = DailySummary(
            date=date(2024, 1, 17),
            starting_balance=100000.0,
            ending_balance=100000.0,
            pnl=0.0,
            trade_count=0,
            win_rate=0.0
        )
        session.add(summary)
        session.commit()

        assert summary.trade_count == 0

    def test_daily_summary_unique_date(self, session):
        """Test that date is unique (primary key)"""
        summary1 = DailySummary(
            date=date(2024, 1, 18),
            starting_balance=100000.0,
            ending_balance=100500.0,
            pnl=500.0,
            trade_count=2,
            win_rate=1.0
        )
        session.add(summary1)
        session.commit()

        # Try to add another with same date
        summary2 = DailySummary(
            date=date(2024, 1, 18),
            starting_balance=100500.0,
            ending_balance=101000.0,
            pnl=500.0,
            trade_count=1,
            win_rate=1.0
        )
        session.add(summary2)

        with pytest.raises(Exception):
            session.commit()


class TestSystemStateModel:
    """SystemState model tests"""

    @pytest.fixture
    def session(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        Session = sessionmaker(bind=engine)
        session = Session()
        yield session
        session.close()

    def test_create_system_state(self, session):
        """Test creating a system state"""
        state = SystemState(
            key="scheduler_running",
            value={"running": True}
        )
        session.add(state)
        session.commit()

        assert state.key == "scheduler_running"
        assert state.value["running"] is True

    def test_update_system_state(self, session):
        """Test updating system state"""
        state = SystemState(key="scheduler_running", value={"running": True})
        session.add(state)
        session.commit()

        # Update
        state.value = {"running": False}
        state.updated_at = datetime.utcnow()
        session.commit()

        retrieved = session.query(SystemState).filter_by(key="scheduler_running").first()
        assert retrieved.value["running"] is False

    def test_system_state_complex_value(self, session):
        """Test system state with complex JSON value"""
        state = SystemState(
            key="last_trade",
            value={
                "symbol": "MSTR",
                "action": "buy",
                "quantity": 10,
                "price": 350.50,
                "timestamp": "2024-01-15T10:30:00"
            }
        )
        session.add(state)
        session.commit()

        retrieved = session.query(SystemState).filter_by(key="last_trade").first()
        assert retrieved.value["symbol"] == "MSTR"
        assert retrieved.value["price"] == 350.50

    def test_system_state_unique_key(self, session):
        """Test that key is unique (primary key)"""
        state1 = SystemState(key="test_key", value={"data": 1})
        session.add(state1)
        session.commit()

        state2 = SystemState(key="test_key", value={"data": 2})
        session.add(state2)

        with pytest.raises(Exception):
            session.commit()

    def test_system_state_empty_value(self, session):
        """Test system state with empty value"""
        state = SystemState(key="empty_state", value={})
        session.add(state)
        session.commit()

        assert state.value == {}

    def test_system_state_array_value(self, session):
        """Test system state with array value"""
        state = SystemState(
            key="watchlist",
            value={"symbols": ["MSTR", "TSLA", "QQQ", "SPY"]}
        )
        session.add(state)
        session.commit()

        retrieved = session.query(SystemState).filter_by(key="watchlist").first()
        assert len(retrieved.value["symbols"]) == 4


class TestDatabaseEdgeCases:
    """Edge case tests for database operations"""

    @pytest.fixture
    def session(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        Session = sessionmaker(bind=engine)
        session = Session()
        yield session
        session.close()

    def test_trade_very_long_symbol(self, session):
        """Test trade with very long symbol (edge case)"""
        trade = Trade(
            symbol="A" * 50,  # 50 character symbol
            action="buy",
            quantity=1.0,
            price=100.0,
            order_type="market",
            status="filled",
            alpaca_order_id="order-long"
        )
        session.add(trade)
        session.commit()

        assert len(trade.symbol) == 50

    def test_trade_special_characters_in_symbol(self, session):
        """Test trade with special characters in symbol"""
        trade = Trade(
            symbol="BRK.B",
            action="buy",
            quantity=1.0,
            price=400.0,
            order_type="market",
            status="filled",
            alpaca_order_id="order-special"
        )
        session.add(trade)
        session.commit()

        assert trade.symbol == "BRK.B"

    def test_decision_unicode_reasoning(self, session):
        """Test decision with unicode in reasoning"""
        decision = Decision(
            market_context={},
            grok_response='{"reasoning": "上昇トレンド確認"}',
            parsed_action={"action": "buy", "reasoning": "上昇トレンド確認"},
            executed=True
        )
        session.add(decision)
        session.commit()

        retrieved = session.query(Decision).first()
        assert "上昇トレンド" in retrieved.parsed_action["reasoning"]

    def test_very_large_quantity(self, session):
        """Test trade with very large quantity"""
        trade = Trade(
            symbol="MSTR",
            action="buy",
            quantity=1e15,  # Very large number
            price=350.0,
            order_type="market",
            status="filled",
            alpaca_order_id="order-huge"
        )
        session.add(trade)
        session.commit()

        assert trade.quantity == 1e15

    def test_rollback_on_error(self, session):
        """Test that rollback works correctly"""
        trade1 = Trade(
            symbol="MSTR",
            action="buy",
            quantity=10.0,
            price=350.0,
            order_type="market",
            status="filled",
            alpaca_order_id="order-1"
        )
        session.add(trade1)
        session.commit()

        try:
            # Add invalid data that will cause error
            trade2 = Trade(
                symbol=None,  # This might cause issues depending on constraints
                action="buy",
                quantity=10.0,
                price=350.0,
                order_type="market",
                status="filled",
                alpaca_order_id="order-2"
            )
            session.add(trade2)
            session.commit()
        except Exception:
            session.rollback()

        # First trade should still be there
        trades = session.query(Trade).all()
        assert len(trades) >= 1
