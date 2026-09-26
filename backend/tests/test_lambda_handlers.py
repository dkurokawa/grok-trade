"""Lambda entrypoint tests (EventBridge task dispatch + Mangum API adapter)."""
from unittest.mock import AsyncMock, patch

import pytest


class TestTaskDispatch:
    def test_defaults_to_trading_cycle(self, dynamo_table):
        import lambda_trading

        with patch.object(lambda_trading, "trading_cycle", new_callable=AsyncMock) as cycle:
            result = lambda_trading.handler({}, None)

        cycle.assert_awaited_once()
        assert result == {"ok": True, "task": "trading_cycle"}

    def test_runs_emergency_check(self, dynamo_table):
        import lambda_trading

        with patch.object(lambda_trading, "emergency_check", new_callable=AsyncMock) as check:
            result = lambda_trading.handler({"task": "emergency_check"}, None)

        check.assert_awaited_once()
        assert result == {"ok": True, "task": "emergency_check"}

    def test_unknown_task_raises(self, dynamo_table):
        import lambda_trading

        with pytest.raises(ValueError, match="Unknown task"):
            lambda_trading.handler({"task": "nope"}, None)


class TestApiHandler:
    def test_mangum_handler_exists(self, dynamo_table):
        import app
        from mangum import Mangum

        assert isinstance(app.handler, Mangum)


class TestGuardRails:
    """A paused bot or a missing secret must stop the cycle before any client
    is constructed - the first AWS deploy had no keys and crashed on import."""

    def test_paused_bot_skips_cycle(self, dynamo_table):
        import asyncio

        import db.dynamo as dyn
        import trading_core

        dyn.set_scheduler_state(False)
        with patch.object(trading_core, "trader", None):
            asyncio.run(trading_core.trading_cycle())
            assert trading_core.trader is None

    def test_missing_secret_skips_cycle(self, dynamo_table, monkeypatch):
        import asyncio

        import trading_core

        monkeypatch.delenv("ALPACA_API_KEY", raising=False)
        with patch.object(trading_core, "trader", None):
            asyncio.run(trading_core.trading_cycle())
            assert trading_core.trader is None

    def test_health_reports_missing_secrets(self, dynamo_table, monkeypatch):
        import app
        from fastapi.testclient import TestClient

        monkeypatch.delenv("ALPACA_API_KEY", raising=False)
        body = TestClient(app.app).get("/health").json()
        assert body["status"] == "degraded"
        assert "ALPACA_API_KEY" in body["missing_secrets"]
        assert body["secrets"]["ALPACA_API_KEY"] is False  # names only, never values

    def test_status_returns_503_when_secrets_missing(self, dynamo_table, monkeypatch):
        import app
        from fastapi.testclient import TestClient

        monkeypatch.delenv("ALPACA_API_KEY", raising=False)
        r = TestClient(app.app).get("/status", headers={"x-api-key": "test_shared_secret"})
        assert r.status_code == 503
        assert "ALPACA_API_KEY" in r.json()["detail"]


class TestDecisionEngineConfig:
    def test_defaults_to_grok(self, monkeypatch):
        import config

        monkeypatch.delenv("DECISION_ENGINE", raising=False)
        assert config.decision_engine() == "grok"
        assert "ANTHROPIC_API_KEY" not in config.required_keys()

    def test_opus_requires_anthropic_key(self, monkeypatch):
        import config

        monkeypatch.setenv("DECISION_ENGINE", "opus")
        assert config.decision_engine() == "opus"
        assert "ANTHROPIC_API_KEY" in config.required_keys()

    def test_unknown_engine_falls_back_to_grok(self, monkeypatch):
        import config

        monkeypatch.setenv("DECISION_ENGINE", "typo")
        assert config.decision_engine() == "grok"
