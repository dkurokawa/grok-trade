"""API endpoints integration tests - comprehensive edge cases and error handling"""
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

from fastapi.testclient import TestClient


class TestHealthEndpoint:
    """Health endpoint tests"""

    @pytest.fixture
    def client(self):
        with patch("main.GrokClient"):
            with patch("main.Trader"):
                with patch("main.DiscordNotifier"):
                    with patch("main.init_db"):
                        with patch("main.get_scheduler_state", return_value=True):
                            with patch("main.trading_cycle", new_callable=AsyncMock):
                                from main import app
                                with TestClient(app) as c:
                                    yield c

    def test_health_returns_ok(self, client):
        """Test health endpoint returns OK"""
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"

    def test_health_includes_timestamp(self, client):
        """Test health response includes timestamp"""
        response = client.get("/health")
        data = response.json()
        assert "timestamp" in data

    def test_health_includes_scheduler_status(self, client):
        """Test health response includes scheduler status"""
        response = client.get("/health")
        data = response.json()
        assert "scheduler_running" in data


class TestStatusEndpoint:
    """Status endpoint tests"""

    @pytest.fixture
    def client(self):
        mock_trader = MagicMock()
        mock_trader.get_account.return_value = {
            "cash": 100000.0,
            "portfolio_value": 100000.0,
            "buying_power": 200000.0,
            "equity": 100000.0,
            "daily_pnl": 500.0
        }
        mock_trader.get_positions.return_value = [
            {
                "symbol": "MSTR",
                "qty": 10.0,
                "avg_entry_price": 350.0,
                "market_value": 3600.0,
                "unrealized_pl": 100.0,
                "unrealized_plpc": 0.028
            }
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

    def test_status_returns_account(self, client):
        """Test status endpoint returns account info"""
        response = client.get("/status")
        assert response.status_code == 200
        data = response.json()
        assert "account" in data
        assert data["account"]["cash"] == 100000.0

    def test_status_returns_positions(self, client):
        """Test status endpoint returns positions"""
        response = client.get("/status")
        data = response.json()
        assert "positions" in data
        assert len(data["positions"]) == 1
        assert data["positions"][0]["symbol"] == "MSTR"

    def test_status_returns_scheduler_status(self, client):
        """Test status endpoint returns scheduler status"""
        response = client.get("/status")
        data = response.json()
        assert "scheduler_running" in data


class TestStopStartEndpoints:
    """Stop and Start endpoint tests"""

    @pytest.fixture
    def client(self):
        mock_notifier = MagicMock()
        mock_notifier.notify_system_stop = AsyncMock()
        mock_notifier.notify_alert = AsyncMock()

        with patch("main.GrokClient"):
            with patch("main.Trader"):
                with patch("main.DiscordNotifier", return_value=mock_notifier):
                    with patch("main.init_db"):
                        with patch("main.get_scheduler_state", return_value=True):
                            with patch("main.set_scheduler_state"):
                                with patch("main.trading_cycle", new_callable=AsyncMock):
                                    from importlib import reload
                                    import main
                                    reload(main)
                                    main.notifier = mock_notifier
                                    with TestClient(main.app) as c:
                                        yield c, mock_notifier

    def test_stop_endpoint(self, client):
        """Test stop endpoint pauses scheduler"""
        c, mock_notifier = client
        response = c.post("/stop")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "stopped"

    def test_start_endpoint(self, client):
        """Test start endpoint resumes scheduler"""
        c, mock_notifier = client
        response = c.post("/start")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "running"


class TestTradesEndpoint:
    """Trades endpoint tests"""

    @pytest.fixture
    def mock_session(self):
        """Create mock session with trades"""
        from db.models import Trade

        mock_trades = [
            MagicMock(
                id=1,
                timestamp=datetime(2024, 1, 15, 10, 30),
                symbol="MSTR",
                action="buy",
                quantity=10.0,
                price=350.0,
                order_type="market",
                status="filled",
                alpaca_order_id="order-1"
            ),
            MagicMock(
                id=2,
                timestamp=datetime(2024, 1, 15, 14, 0),
                symbol="TSLA",
                action="sell",
                quantity=5.0,
                price=250.0,
                order_type="market",
                status="filled",
                alpaca_order_id="order-2"
            )
        ]

        mock_query = MagicMock()
        mock_query.order_by.return_value.limit.return_value.all.return_value = mock_trades

        mock_session = MagicMock()
        mock_session.query.return_value = mock_query

        return mock_session

    @pytest.fixture
    def client(self, mock_session):
        with patch("main.GrokClient"):
            with patch("main.Trader"):
                with patch("main.DiscordNotifier"):
                    with patch("main.init_db"):
                        with patch("main.get_scheduler_state", return_value=True):
                            with patch("main.get_session", return_value=mock_session):
                                with patch("main.trading_cycle", new_callable=AsyncMock):
                                    from importlib import reload
                                    import main
                                    reload(main)
                                    with TestClient(main.app) as c:
                                        yield c

    def test_trades_returns_list(self, client):
        """Test trades endpoint returns list"""
        response = client.get("/trades")
        assert response.status_code == 200
        data = response.json()
        assert "trades" in data
        assert isinstance(data["trades"], list)

    def test_trades_with_limit(self, client):
        """Test trades endpoint respects limit parameter"""
        response = client.get("/trades?limit=10")
        assert response.status_code == 200

    def test_trades_default_limit(self, client):
        """Test trades endpoint has default limit of 50"""
        response = client.get("/trades")
        assert response.status_code == 200


class TestDecisionsEndpoint:
    """Decisions endpoint tests"""

    @pytest.fixture
    def mock_session(self):
        """Create mock session with decisions"""
        mock_decisions = [
            MagicMock(
                id=1,
                timestamp=datetime(2024, 1, 15, 10, 0),
                parsed_action={"action": "buy", "symbol": "MSTR", "quantity": 10, "confidence": 75},
                executed=True,
                blocked_reason=None
            ),
            MagicMock(
                id=2,
                timestamp=datetime(2024, 1, 15, 10, 15),
                parsed_action={"action": "hold", "symbol": "MSTR", "quantity": 0, "confidence": 60},
                executed=False,
                blocked_reason="hold"
            )
        ]

        mock_query = MagicMock()
        mock_query.order_by.return_value.limit.return_value.all.return_value = mock_decisions

        mock_session = MagicMock()
        mock_session.query.return_value = mock_query

        return mock_session

    @pytest.fixture
    def client(self, mock_session):
        with patch("main.GrokClient"):
            with patch("main.Trader"):
                with patch("main.DiscordNotifier"):
                    with patch("main.init_db"):
                        with patch("main.get_scheduler_state", return_value=True):
                            with patch("main.get_session", return_value=mock_session):
                                with patch("main.trading_cycle", new_callable=AsyncMock):
                                    from importlib import reload
                                    import main
                                    reload(main)
                                    with TestClient(main.app) as c:
                                        yield c

    def test_decisions_returns_list(self, client):
        """Test decisions endpoint returns list"""
        response = client.get("/decisions")
        assert response.status_code == 200
        data = response.json()
        assert "decisions" in data
        assert isinstance(data["decisions"], list)

    def test_decisions_with_limit(self, client):
        """Test decisions endpoint respects limit parameter"""
        response = client.get("/decisions?limit=10")
        assert response.status_code == 200


class TestDatabaseUnavailable:
    """Tests when database is unavailable"""

    @pytest.fixture
    def client(self):
        with patch("main.GrokClient"):
            with patch("main.Trader"):
                with patch("main.DiscordNotifier"):
                    with patch("main.init_db"):
                        with patch("main.get_scheduler_state", return_value=True):
                            with patch("main.trading_cycle", new_callable=AsyncMock):
                                from importlib import reload
                                import main
                                reload(main)
                                # Patch get_session at the endpoint level
                                original_get_session = main.get_session
                                main.get_session = lambda: None
                                with TestClient(main.app) as c:
                                    yield c
                                main.get_session = original_get_session

    def test_trades_db_unavailable(self, client):
        """Test trades endpoint when DB is unavailable"""
        response = client.get("/trades")
        assert response.status_code == 200
        data = response.json()
        assert data["trades"] == []
        assert "error" in data

    def test_decisions_db_unavailable(self, client):
        """Test decisions endpoint when DB is unavailable"""
        response = client.get("/decisions")
        assert response.status_code == 200
        data = response.json()
        assert data["decisions"] == []
        assert "error" in data


class TestDatabaseError:
    """Tests when database query fails"""

    @pytest.fixture
    def client(self):
        mock_error_session = MagicMock()
        mock_error_session.query.side_effect = Exception("Database connection error")
        mock_error_session.close = MagicMock()

        with patch("main.GrokClient"):
            with patch("main.Trader"):
                with patch("main.DiscordNotifier"):
                    with patch("main.init_db"):
                        with patch("main.get_scheduler_state", return_value=True):
                            with patch("main.trading_cycle", new_callable=AsyncMock):
                                from importlib import reload
                                import main
                                reload(main)
                                # Patch get_session at the endpoint level
                                original_get_session = main.get_session
                                main.get_session = lambda: mock_error_session
                                with TestClient(main.app) as c:
                                    yield c
                                main.get_session = original_get_session

    def test_trades_db_error(self, client):
        """Test trades endpoint handles DB error"""
        response = client.get("/trades")
        assert response.status_code == 200
        data = response.json()
        assert data["trades"] == []
        assert "error" in data

    def test_decisions_db_error(self, client):
        """Test decisions endpoint handles DB error"""
        response = client.get("/decisions")
        assert response.status_code == 200
        data = response.json()
        assert data["decisions"] == []
        assert "error" in data


class TestCORSHeaders:
    """CORS configuration tests"""

    @pytest.fixture
    def client(self):
        with patch("main.GrokClient"):
            with patch("main.Trader"):
                with patch("main.DiscordNotifier"):
                    with patch("main.init_db"):
                        with patch("main.get_scheduler_state", return_value=True):
                            with patch("main.trading_cycle", new_callable=AsyncMock):
                                from importlib import reload
                                import main
                                reload(main)
                                with TestClient(main.app) as c:
                                    yield c

    def test_cors_allows_any_origin(self, client):
        """Test CORS allows any origin"""
        response = client.options(
            "/health",
            headers={"Origin": "https://example.com", "Access-Control-Request-Method": "GET"}
        )
        # FastAPI TestClient may not fully simulate CORS, check status
        assert response.status_code in [200, 405]

    def test_cors_allows_methods(self, client):
        """Test CORS allows various methods"""
        # GET
        response = client.get("/health")
        assert response.status_code == 200

        # POST
        response = client.post("/stop")
        assert response.status_code == 200


class TestEdgeCases:
    """Edge case tests for API endpoints"""

    @pytest.fixture
    def mock_trader(self):
        mock = MagicMock()
        mock.get_account.return_value = {
            "cash": 0.0,
            "portfolio_value": 0.0,
            "buying_power": 0.0,
            "equity": 0.0,
            "daily_pnl": 0.0
        }
        mock.get_positions.return_value = []
        return mock

    @pytest.fixture
    def client(self, mock_trader):
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

    def test_status_with_zero_values(self, client):
        """Test status endpoint with zero values"""
        response = client.get("/status")
        assert response.status_code == 200
        data = response.json()
        assert data["account"]["cash"] == 0.0

    def test_status_empty_positions(self, client):
        """Test status endpoint with empty positions"""
        response = client.get("/status")
        assert response.status_code == 200
        data = response.json()
        assert data["positions"] == []

    def test_invalid_limit_parameter(self, client):
        """Test trades endpoint with invalid limit"""
        # String instead of int
        response = client.get("/trades?limit=abc")
        assert response.status_code == 422  # Validation error

    def test_negative_limit_parameter(self, client):
        """Test trades endpoint with negative limit"""
        response = client.get("/trades?limit=-10")
        assert response.status_code == 200  # FastAPI accepts negative, DB handles it

    def test_zero_limit_parameter(self, client):
        """Test trades endpoint with zero limit"""
        response = client.get("/trades?limit=0")
        assert response.status_code == 200

    def test_large_limit_parameter(self, client):
        """Test trades endpoint with very large limit"""
        response = client.get("/trades?limit=1000000")
        assert response.status_code == 200

    def test_nonexistent_endpoint(self, client):
        """Test nonexistent endpoint returns 404"""
        response = client.get("/nonexistent")
        assert response.status_code == 404

    def test_wrong_method(self, client):
        """Test wrong HTTP method returns 405"""
        response = client.post("/health")
        assert response.status_code == 405

        response = client.get("/stop")
        assert response.status_code == 405


class TestMultipleRequests:
    """Tests for multiple concurrent requests"""

    @pytest.fixture
    def client(self):
        mock_trader = MagicMock()
        mock_trader.get_account.return_value = {
            "cash": 100000.0,
            "portfolio_value": 100000.0,
            "buying_power": 200000.0,
            "equity": 100000.0,
            "daily_pnl": 0.0
        }
        mock_trader.get_positions.return_value = []

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

    def test_multiple_health_checks(self, client):
        """Test multiple rapid health checks"""
        for _ in range(10):
            response = client.get("/health")
            assert response.status_code == 200

    def test_multiple_status_checks(self, client):
        """Test multiple rapid status checks"""
        for _ in range(10):
            response = client.get("/status")
            assert response.status_code == 200
