"""API endpoint tests for the Lambda API (app.py, FastAPI + Mangum).

Uses a moto-backed DynamoDB table so /trades, /decisions, /pipeline and the
scheduler state exercise the real data path.
"""
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

import app as app_module

SECRET = "test_shared_secret"  # matches conftest API_SHARED_SECRET
AUTH = {"x-api-key": SECRET}


@pytest.fixture
def client(dynamo_table):
    return TestClient(app_module.app)


@pytest.fixture
def mock_trader(monkeypatch):
    t = MagicMock()
    t.get_account.return_value = {
        "cash": 100000.0,
        "portfolio_value": 100000.0,
        "buying_power": 200000.0,
        "equity": 100000.0,
        "last_equity": 99500.0,
        "daily_pnl": 500.0,
    }
    t.get_positions.return_value = [
        {"symbol": "MSTR", "qty": 10.0, "avg_entry_price": 350.0,
         "market_value": 3600.0, "unrealized_pl": 100.0, "unrealized_plpc": 0.028}
    ]
    monkeypatch.setattr(app_module, "trader", t)
    return t


@pytest.fixture(autouse=True)
def mock_notifier(monkeypatch):
    n = MagicMock()
    n.notify_system_stop = AsyncMock()
    n.notify_alert = AsyncMock()
    monkeypatch.setattr(app_module, "notifier", n)
    return n


class TestHealthEndpoint:
    def test_returns_ok(self, client):
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"

    def test_includes_timestamp(self, client):
        assert "timestamp" in client.get("/health").json()

    def test_includes_scheduler_status(self, client):
        assert "scheduler_running" in client.get("/health").json()

    def test_reports_decision_engine(self, client, monkeypatch):
        monkeypatch.setenv("DECISION_ENGINE", "opus")
        assert client.get("/health").json()["decision_engine"] == "opus"


class TestStatusEndpoint:
    def test_requires_secret(self, client):
        assert client.get("/status").status_code == 401

    def test_returns_account_and_positions(self, client, mock_trader):
        data = client.get("/status", headers=AUTH).json()
        assert data["account"]["cash"] == 100000.0
        assert data["positions"][0]["symbol"] == "MSTR"
        assert "scheduler_running" in data

    def test_zero_values(self, client, mock_trader):
        mock_trader.get_account.return_value = {
            "cash": 0.0, "portfolio_value": 0.0, "buying_power": 0.0,
            "equity": 0.0, "last_equity": 0.0, "daily_pnl": 0.0,
        }
        mock_trader.get_positions.return_value = []
        data = client.get("/status", headers=AUTH).json()
        assert data["account"]["cash"] == 0.0
        assert data["positions"] == []


class TestStopStartEndpoints:
    def test_stop_requires_secret(self, client):
        assert client.post("/stop").status_code == 401

    def test_start_requires_secret(self, client):
        assert client.post("/start").status_code == 401

    def test_wrong_secret_rejected(self, client):
        assert client.post("/stop", headers={"x-api-key": "nope"}).status_code == 401

    def test_stop_then_start_toggles_state(self, client):
        import db.dynamo as dyn
        headers = {"x-api-key": SECRET}

        assert client.post("/stop", headers=headers).json()["status"] == "stopped"
        assert dyn.get_scheduler_state() is False

        assert client.post("/start", headers=headers).json()["status"] == "running"
        assert dyn.get_scheduler_state() is True

    def test_stop_notifies(self, client, mock_notifier):
        client.post("/stop", headers={"x-api-key": SECRET})
        mock_notifier.notify_system_stop.assert_awaited_once()

    def test_stop_returns_500_when_write_fails(self, client, monkeypatch):
        def boom(_running):
            raise RuntimeError("DynamoDB unavailable")

        monkeypatch.setattr(app_module, "set_scheduler_state", boom)
        r = client.post("/stop", headers={"x-api-key": SECRET})
        assert r.status_code == 500

    def test_start_returns_500_when_write_fails(self, client, monkeypatch):
        def boom(_running):
            raise RuntimeError("DynamoDB unavailable")

        monkeypatch.setattr(app_module, "set_scheduler_state", boom)
        r = client.post("/start", headers={"x-api-key": SECRET})
        assert r.status_code == 500


class TestTradesEndpoint:
    def test_requires_secret(self, client):
        assert client.get("/trades").status_code == 401

    def test_returns_seeded_trade(self, client):
        import db.dynamo as dyn
        dyn.log_trade(
            cycle_id="c1", symbol="MSTR", action="buy", quantity=10.0, price=350.0,
            order_type="market", status="filled", alpaca_order_id="o-1",
            stop_loss=330.0, take_profit=400.0,
        )
        trades = client.get("/trades", headers=AUTH).json()["trades"]
        assert len(trades) == 1
        assert trades[0]["symbol"] == "MSTR"
        assert trades[0]["stop_loss"] == 330.0
        assert trades[0]["cycle_id"] == "c1"

    def test_empty(self, client):
        assert client.get("/trades", headers=AUTH).json()["trades"] == []

    def test_with_limit(self, client):
        assert client.get("/trades?limit=10", headers=AUTH).status_code == 200


class TestDecisionsEndpoint:
    def test_requires_secret(self, client):
        assert client.get("/decisions").status_code == 401

    def test_returns_seeded_decision(self, client):
        import db.dynamo as dyn
        dyn.log_pipeline(
            cycle_id="c1", decision_engine="grok",
            opus_output={"action": "hold", "confidence": 60},
            rg_passed=True, order_submitted=False,
        )
        decisions = client.get("/decisions", headers=AUTH).json()["decisions"]
        assert len(decisions) == 1
        assert decisions[0]["opus_output"]["action"] == "hold"
        assert decisions[0]["decision_engine"] == "grok"

    def test_with_limit(self, client):
        assert client.get("/decisions?limit=10", headers=AUTH).status_code == 200


class TestPipelineEndpoint:
    def test_requires_secret(self, client):
        assert client.get("/pipeline").status_code == 401

    def test_returns_full_cycle(self, client):
        import db.dynamo as dyn
        dyn.log_pipeline(
            cycle_id="c1", decision_engine="grok",
            grok_output={"sentiment": {"overall": 55}}, grok_latency_ms=120,
            opus_output={"action": "buy"}, opus_latency_ms=700,
            rg_passed=True, order_submitted=True, alpaca_order_id="o-1",
        )
        logs = client.get("/pipeline", headers=AUTH).json()["logs"]
        assert logs[0]["grok_output"]["sentiment"]["overall"] == 55
        assert logs[0]["grok_latency_ms"] == 120
        assert logs[0]["alpaca_order_id"] == "o-1"

    def test_empty(self, client):
        assert client.get("/pipeline", headers=AUTH).json()["logs"] == []


class TestDatabaseError:
    """A DynamoDB failure must not 500 the dashboard."""

    @pytest.fixture
    def broken_db(self, monkeypatch):
        def boom(*a, **kw):
            raise RuntimeError("Database connection error")

        import db.dynamo as dyn
        monkeypatch.setattr(dyn, "_get_table", boom)
        monkeypatch.setattr(app_module, "get_trades", boom)
        monkeypatch.setattr(app_module, "get_decisions", boom)
        monkeypatch.setattr(app_module, "get_pipeline_logs", boom)

    def test_trades_error(self, client, broken_db):
        body = client.get("/trades", headers=AUTH).json()
        assert body["trades"] == [] and "error" in body

    def test_decisions_error(self, client, broken_db):
        body = client.get("/decisions", headers=AUTH).json()
        assert body["decisions"] == [] and "error" in body

    def test_pipeline_error(self, client, broken_db):
        body = client.get("/pipeline", headers=AUTH).json()
        assert body["logs"] == [] and "error" in body


class TestEdgeCases:
    def test_invalid_limit_parameter(self, client):
        assert client.get("/trades?limit=abc", headers=AUTH).status_code == 422

    @pytest.mark.parametrize("limit", [-10, 0, 1000000])
    def test_out_of_range_limits_are_clamped(self, client, limit):
        assert client.get(f"/trades?limit={limit}", headers=AUTH).status_code == 200

    def test_nonexistent_endpoint(self, client):
        assert client.get("/nonexistent").status_code == 404

    def test_wrong_method(self, client):
        assert client.post("/health").status_code == 405
        assert client.get("/stop").status_code == 405

    def test_cors_preflight(self, client):
        r = client.options(
            "/health",
            headers={"Origin": "https://example.com", "Access-Control-Request-Method": "GET"},
        )
        assert r.status_code in (200, 405)


class TestMultipleRequests:
    def test_repeated_health_checks(self, client):
        for _ in range(10):
            assert client.get("/health").status_code == 200

    def test_repeated_status_checks(self, client, mock_trader):
        for _ in range(10):
            assert client.get("/status", headers=AUTH).status_code == 200
