"""Lambda entrypoint tests (EventBridge task dispatch + Mangum API adapter)."""
from unittest.mock import AsyncMock, MagicMock, patch

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

    def test_passes_scheduled_time_through(self, dynamo_table):
        """EventBridge's <aws.scheduler.scheduled-time> (Issue M3) must reach
        trading_core so the slot is keyed off the intended fire time."""
        import lambda_trading

        with patch.object(lambda_trading, "trading_cycle", new_callable=AsyncMock) as cycle:
            lambda_trading.handler(
                {"task": "trading_cycle", "scheduled_time": "2026-06-01T13:30:00Z"}, None
            )

        cycle.assert_awaited_once_with(scheduled_time="2026-06-01T13:30:00Z")

    def test_manual_invocation_has_no_scheduled_time(self, dynamo_table):
        import lambda_trading

        with patch.object(lambda_trading, "trading_cycle", new_callable=AsyncMock) as cycle:
            lambda_trading.handler({}, None)

        cycle.assert_awaited_once_with(scheduled_time=None)


class TestApiHandler:
    def test_mangum_handler_exists(self, dynamo_table):
        from mangum import Mangum

        import app

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

    def test_scheduler_read_failure_skips_cycle(self, dynamo_table, monkeypatch):
        """A DynamoDB read error must fail closed (treated as stopped), not
        fail open and trade with a stale/unknown scheduler state."""
        import asyncio

        import db.dynamo as dyn
        import trading_core

        broken_table = MagicMock()
        broken_table.get_item.side_effect = RuntimeError("boom")
        monkeypatch.setattr(dyn, "_get_table", lambda: broken_table)

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
        from fastapi.testclient import TestClient

        import app

        monkeypatch.delenv("ALPACA_API_KEY", raising=False)
        body = TestClient(app.app).get("/health").json()
        assert body["status"] == "degraded"
        assert "ALPACA_API_KEY" in body["missing_secrets"]
        assert body["secrets"]["ALPACA_API_KEY"] is False  # names only, never values

    def test_status_returns_503_when_secrets_missing(self, dynamo_table, monkeypatch):
        from fastapi.testclient import TestClient

        import app

        monkeypatch.delenv("ALPACA_API_KEY", raising=False)
        r = TestClient(app.app).get("/status", headers={"x-api-key": "test_shared_secret"})
        assert r.status_code == 503
        assert "ALPACA_API_KEY" in r.json()["detail"]


class TestEmergencyCheckIndependentOfSchedulerAndAI:
    """emergency_check() is a drawdown safety net that must not depend on
    anything trading_cycle() depends on: not the pause flag (it must keep
    watching while paused), not the AI keys (it never calls Grok/Opus)."""

    def _mock_trader(self, drawdown_pct: float = 10.0):
        t = MagicMock()
        t.get_account.return_value = {"equity": 100 - drawdown_pct, "last_equity": 100.0}
        return t

    def test_liquidates_while_paused(self, dynamo_table):
        import asyncio

        import db.dynamo as dyn
        import trading_core

        dyn.set_scheduler_state(False)  # bot is paused
        mock_trader = self._mock_trader()
        mock_notifier = MagicMock()
        mock_notifier.send_pipeline_log = AsyncMock()

        with patch.object(trading_core, "trader", mock_trader), \
             patch.object(trading_core, "notifier", mock_notifier):
            asyncio.run(trading_core.emergency_check())

        mock_trader.execute_emergency_liquidation.assert_called_once()

    def test_runs_without_ai_keys(self, dynamo_table, monkeypatch):
        import asyncio

        import trading_core

        monkeypatch.delenv("GROK_API_KEY", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        mock_trader = self._mock_trader()
        mock_notifier = MagicMock()
        mock_notifier.send_pipeline_log = AsyncMock()

        with patch.object(trading_core, "trader", mock_trader), \
             patch.object(trading_core, "notifier", mock_notifier), \
             patch.object(trading_core, "grok", None), \
             patch.object(trading_core, "opus", None):
            asyncio.run(trading_core.emergency_check())
            mock_trader.execute_emergency_liquidation.assert_called_once()
            # never touched Grok/Opus, even with the keys missing
            assert trading_core.grok is None
            assert trading_core.opus is None

    def test_skips_without_alpaca_keys(self, dynamo_table, monkeypatch):
        import asyncio

        import trading_core

        monkeypatch.delenv("ALPACA_API_KEY", raising=False)
        with patch.object(trading_core, "trader", None):
            asyncio.run(trading_core.emergency_check())
            assert trading_core.trader is None


class TestEmergencyLiquidationFailureHandling:
    """M1: a failed emergency liquidation must notify Discord with an error,
    still set the stop flag, and re-raise so the Lambda invocation is
    reported as failed (CloudWatch Errors) - never look like a normal run."""

    def _mock_trader(self, drawdown_pct: float = 10.0):
        t = MagicMock()
        t.get_account.return_value = {"equity": 100 - drawdown_pct, "last_equity": 100.0}
        return t

    def test_per_symbol_failure_notifies_stops_and_raises(self, dynamo_table):
        import asyncio

        import db.dynamo as dyn
        import trading_core
        from trading_core import EmergencyLiquidationFailed

        mock_trader = self._mock_trader()
        mock_trader.execute_emergency_liquidation.return_value = [
            {"symbol": "MSTR", "status": 200, "ok": True},
            {"symbol": "TSLA", "status": 500, "ok": False, "error": "insufficient qty available"},
        ]
        mock_notifier = MagicMock()
        mock_notifier.send_pipeline_log = AsyncMock()
        mock_notifier.notify_alert = AsyncMock()

        with patch.object(trading_core, "trader", mock_trader), \
             patch.object(trading_core, "notifier", mock_notifier):
            with pytest.raises(EmergencyLiquidationFailed):
                asyncio.run(trading_core.emergency_check())

        mock_notifier.notify_alert.assert_awaited_once()
        assert mock_notifier.notify_alert.call_args.args[1] == "error"
        assert "TSLA" in mock_notifier.notify_alert.call_args.args[0]
        # stop flag is still set despite the failed liquidation
        assert dyn.get_scheduler_state() is False

    def test_liquidation_exception_notifies_stops_and_raises(self, dynamo_table):
        import asyncio

        import db.dynamo as dyn
        import trading_core
        from trading_core import EmergencyLiquidationFailed

        mock_trader = self._mock_trader()
        mock_trader.execute_emergency_liquidation.side_effect = RuntimeError("Alpaca API down")
        mock_notifier = MagicMock()
        mock_notifier.send_pipeline_log = AsyncMock()
        mock_notifier.notify_alert = AsyncMock()

        with patch.object(trading_core, "trader", mock_trader), \
             patch.object(trading_core, "notifier", mock_notifier):
            with pytest.raises(EmergencyLiquidationFailed):
                asyncio.run(trading_core.emergency_check())

        mock_notifier.notify_alert.assert_awaited_once()
        assert "Alpaca API down" in mock_notifier.notify_alert.call_args.args[0]
        assert dyn.get_scheduler_state() is False

    def test_full_success_does_not_raise_or_alert(self, dynamo_table):
        import asyncio

        import trading_core

        mock_trader = self._mock_trader()
        mock_trader.execute_emergency_liquidation.return_value = [
            {"symbol": "MSTR", "status": 200, "ok": True},
        ]
        mock_notifier = MagicMock()
        mock_notifier.send_pipeline_log = AsyncMock()
        mock_notifier.notify_alert = AsyncMock()

        with patch.object(trading_core, "trader", mock_trader), \
             patch.object(trading_core, "notifier", mock_notifier):
            asyncio.run(trading_core.emergency_check())  # must not raise

        mock_notifier.notify_alert.assert_not_called()


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
