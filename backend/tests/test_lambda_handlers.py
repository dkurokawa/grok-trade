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

    def test_retries_secrets_on_every_invocation(self, dynamo_table):
        # A cold-start SSM failure must not disable the drawdown monitor for the
        # life of a warm container: every invocation retries (a no-op once loaded).
        import lambda_trading

        with (
            patch.object(lambda_trading, "emergency_check", new_callable=AsyncMock),
            patch.object(lambda_trading, "load_secrets") as load,
        ):
            lambda_trading.handler({"task": "emergency_check"}, None)
            lambda_trading.handler({"task": "emergency_check"}, None)

        assert load.call_count == 2

    def test_unknown_task_raises(self, dynamo_table):
        import lambda_trading

        with pytest.raises(ValueError, match="Unknown task"):
            lambda_trading.handler({"task": "nope"}, None)

    def test_passes_scheduled_time_through(self, dynamo_table):
        """EventBridge's <aws.scheduler.scheduled-time> (Issue M3) must reach
        trading_core so the slot is keyed off the intended fire time."""
        import lambda_trading

        with patch.object(lambda_trading, "trading_cycle", new_callable=AsyncMock) as cycle:
            lambda_trading.handler({"task": "trading_cycle", "scheduled_time": "2026-06-01T13:30:00Z"}, None)

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

        with patch.object(trading_core, "trader", mock_trader), patch.object(trading_core, "notifier", mock_notifier):
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

        with (
            patch.object(trading_core, "trader", mock_trader),
            patch.object(trading_core, "notifier", mock_notifier),
            patch.object(trading_core, "grok", None),
            patch.object(trading_core, "opus", None),
        ):
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
    """M1/E2: a failed emergency liquidation must notify Discord with an
    error, still set the stop flag, and re-raise so the Lambda invocation is
    reported as failed (CloudWatch Errors) - never look like a normal run."""

    def _mock_trader(self, drawdown_pct: float = 10.0):
        t = MagicMock()
        t.get_account.return_value = {"equity": 100 - drawdown_pct, "last_equity": 100.0}
        t.get_positions.return_value = [{"symbol": "MSTR", "qty": 10.0, "market_value": 3500.0}]
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

        with patch.object(trading_core, "trader", mock_trader), patch.object(trading_core, "notifier", mock_notifier):
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

        with patch.object(trading_core, "trader", mock_trader), patch.object(trading_core, "notifier", mock_notifier):
            with pytest.raises(EmergencyLiquidationFailed):
                asyncio.run(trading_core.emergency_check())

        mock_notifier.notify_alert.assert_awaited_once()
        assert "Alpaca API down" in mock_notifier.notify_alert.call_args.args[0]
        assert dyn.get_scheduler_state() is False

    def test_stops_trading_even_if_get_positions_fails(self, dynamo_table):
        import asyncio

        import db.dynamo as dyn
        import trading_core

        mock_trader = self._mock_trader()
        mock_trader.get_positions.side_effect = RuntimeError("positions endpoint down")
        mock_notifier = MagicMock()
        mock_notifier.send_pipeline_log = AsyncMock()
        mock_notifier.notify_alert = AsyncMock()

        with patch.object(trading_core, "trader", mock_trader), patch.object(trading_core, "notifier", mock_notifier):
            with pytest.raises(RuntimeError):
                asyncio.run(trading_core.emergency_check())

        # The flag is written before get_positions() runs, so new buys stop.
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

        with patch.object(trading_core, "trader", mock_trader), patch.object(trading_core, "notifier", mock_notifier):
            asyncio.run(trading_core.emergency_check())  # must not raise

        mock_notifier.notify_alert.assert_not_called()

    def test_stop_flag_write_failure_still_raises(self, dynamo_table, monkeypatch):
        """E2: even if BOTH the liquidation and the stop-flag write fail,
        EmergencyLiquidationFailed must still be raised at the end (with the
        stop-flag failure folded into its message) - one failure must not
        swallow the other."""
        import asyncio

        import trading_core
        from trading_core import EmergencyLiquidationFailed

        mock_trader = self._mock_trader()
        mock_trader.execute_emergency_liquidation.side_effect = RuntimeError("Alpaca API down")
        mock_notifier = MagicMock()
        mock_notifier.send_pipeline_log = AsyncMock()
        mock_notifier.notify_alert = AsyncMock()

        def boom(_running):
            raise RuntimeError("DynamoDB unavailable")

        monkeypatch.setattr(trading_core, "set_scheduler_state", boom)

        with patch.object(trading_core, "trader", mock_trader), patch.object(trading_core, "notifier", mock_notifier):
            with pytest.raises(EmergencyLiquidationFailed) as exc_info:
                asyncio.run(trading_core.emergency_check())

        assert "Alpaca API down" in str(exc_info.value)
        assert "stop flag write failed" in str(exc_info.value)
        mock_notifier.notify_alert.assert_awaited_once()

    def test_alert_failure_still_raises(self, dynamo_table):
        """E2: if the failure alert itself can't be sent (Discord down), the
        exception must still be raised."""
        import asyncio

        import db.dynamo as dyn
        import trading_core
        from trading_core import EmergencyLiquidationFailed

        mock_trader = self._mock_trader()
        mock_trader.execute_emergency_liquidation.side_effect = RuntimeError("Alpaca API down")
        mock_notifier = MagicMock()
        mock_notifier.send_pipeline_log = AsyncMock()
        mock_notifier.notify_alert = AsyncMock(side_effect=RuntimeError("Discord webhook down"))

        with patch.object(trading_core, "trader", mock_trader), patch.object(trading_core, "notifier", mock_notifier):
            with pytest.raises(EmergencyLiquidationFailed) as exc_info:
                asyncio.run(trading_core.emergency_check())

        assert "Alpaca API down" in str(exc_info.value)
        # stop flag is still set even though the alert itself failed
        assert dyn.get_scheduler_state() is False

    def test_get_account_failure_notifies_and_raises(self, dynamo_table):
        """E2: a failure in the check itself (not the liquidation path) must
        also log, notify, and re-raise - not just be swallowed."""
        import asyncio

        import trading_core

        mock_trader = MagicMock()
        mock_trader.get_account.side_effect = RuntimeError("Alpaca API down")
        mock_notifier = MagicMock()
        mock_notifier.notify_alert = AsyncMock()

        with patch.object(trading_core, "trader", mock_trader), patch.object(trading_core, "notifier", mock_notifier):
            with pytest.raises(RuntimeError, match="Alpaca API down"):
                asyncio.run(trading_core.emergency_check())

        mock_notifier.notify_alert.assert_awaited_once()
        assert mock_notifier.notify_alert.call_args.args[1] == "error"

    def test_full_success_but_stop_flag_failure_still_raises(self, dynamo_table, monkeypatch):
        """F2: 清算そのものは成功しても、停止フラグの保存が失敗したら素通り
        にせず通知して例外を送出する（以前は extra_failures に積まれる
        だけで、liquidation_error も failed も無いので何も起きなかった）。"""
        import asyncio

        import trading_core
        from trading_core import EmergencyLiquidationFailed

        mock_trader = self._mock_trader()
        mock_trader.execute_emergency_liquidation.return_value = [
            {"symbol": "MSTR", "status": 200, "ok": True},
        ]
        mock_notifier = MagicMock()
        mock_notifier.send_pipeline_log = AsyncMock()
        mock_notifier.notify_alert = AsyncMock()

        def boom(_running):
            raise RuntimeError("DynamoDB unavailable")

        monkeypatch.setattr(trading_core, "set_scheduler_state", boom)

        with patch.object(trading_core, "trader", mock_trader), patch.object(trading_core, "notifier", mock_notifier):
            with pytest.raises(EmergencyLiquidationFailed, match="stop flag write failed"):
                asyncio.run(trading_core.emergency_check())

        mock_notifier.notify_alert.assert_awaited_once()

    def test_full_success_but_record_write_failure_still_raises(self, dynamo_table, monkeypatch):
        """F2: 「本日処理済み」の記録の書き込み失敗も同様に扱う。"""
        import asyncio

        import trading_core
        from trading_core import EmergencyLiquidationFailed

        mock_trader = self._mock_trader()
        mock_trader.execute_emergency_liquidation.return_value = [
            {"symbol": "MSTR", "status": 200, "ok": True},
        ]
        mock_notifier = MagicMock()
        mock_notifier.send_pipeline_log = AsyncMock()
        mock_notifier.notify_alert = AsyncMock()

        def boom(_date):
            raise RuntimeError("DynamoDB unavailable")

        monkeypatch.setattr(trading_core, "record_emergency_liquidation", boom)

        with patch.object(trading_core, "trader", mock_trader), patch.object(trading_core, "notifier", mock_notifier):
            with pytest.raises(EmergencyLiquidationFailed, match="liquidation record write failed"):
                asyncio.run(trading_core.emergency_check())

        mock_notifier.notify_alert.assert_awaited_once()

    def test_get_account_failure_notify_also_failing_still_raises(self, dynamo_table):
        """The general-exception path's own notify_alert failing must not
        swallow the original exception either."""
        import asyncio

        import trading_core

        mock_trader = MagicMock()
        mock_trader.get_account.side_effect = RuntimeError("Alpaca API down")
        mock_notifier = MagicMock()
        mock_notifier.notify_alert = AsyncMock(side_effect=RuntimeError("Discord webhook down"))

        with patch.object(trading_core, "trader", mock_trader), patch.object(trading_core, "notifier", mock_notifier):
            with pytest.raises(RuntimeError, match="Alpaca API down"):
                asyncio.run(trading_core.emergency_check())


class TestEmergencyCheckLockFailOpen:
    """E3: if acquire_lock() itself raises (a DynamoDB problem, not a
    legitimate "already claimed"), emergency_check must continue anyway - a
    redundant check is far cheaper than silently skipping the safety net."""

    def test_lock_check_failure_continues_anyway(self, dynamo_table, monkeypatch):
        import asyncio

        import trading_core

        def boom(_kind, _slot):
            raise RuntimeError("DynamoDB unavailable")

        monkeypatch.setattr(trading_core, "acquire_lock", boom)

        mock_trader = MagicMock()
        mock_trader.get_account.return_value = {"equity": 100.0, "last_equity": 100.0}  # no drawdown
        mock_notifier = MagicMock()

        with patch.object(trading_core, "trader", mock_trader), patch.object(trading_core, "notifier", mock_notifier):
            asyncio.run(trading_core.emergency_check())  # must not raise, must not skip

        mock_trader.get_account.assert_called_once()


class TestEmergencyLiquidationDailyDedup:
    """F1/F4: the "already handled today" record exists only to avoid
    repeating notification/order-cancellation noise once nothing is left to
    do - it never blocks a retry while positions remain, and it is only
    written when the liquidation attempt fully succeeded."""

    def _mock_trader(self, drawdown_pct: float = 10.0):
        t = MagicMock()
        t.get_account.return_value = {"equity": 100 - drawdown_pct, "last_equity": 100.0}
        t.get_positions.return_value = [{"symbol": "MSTR", "qty": 10.0, "market_value": 3500.0}]
        t.execute_emergency_liquidation.return_value = [
            {"symbol": "MSTR", "status": 200, "ok": True},
        ]
        return t

    def test_record_exists_but_positions_remain_liquidates_again(self, dynamo_table):
        """F1: 記録は通知/注文取り消しの重複を避けるためだけに使う。保有が
        残っている限り、同じ日でも清算を再度試みる。"""
        import asyncio

        import trading_core

        mock_trader = self._mock_trader()  # always reports the same open position
        mock_notifier = MagicMock()
        mock_notifier.send_pipeline_log = AsyncMock()
        mock_notifier.notify_alert = AsyncMock()

        with (
            patch.object(trading_core, "trader", mock_trader),
            patch.object(trading_core, "notifier", mock_notifier),
            patch.object(trading_core, "acquire_lock", return_value=True),
        ):
            asyncio.run(trading_core.emergency_check(scheduled_time="2026-06-01T14:00:00Z"))
            asyncio.run(trading_core.emergency_check(scheduled_time="2026-06-01T14:05:00Z"))

        assert mock_trader.execute_emergency_liquidation.call_count == 2

    def test_failed_liquidation_does_not_record(self, dynamo_table):
        """F1: 清算注文の作成に失敗したら「本日処理済み」を記録しない -
        記録すると保有が残っているのに再試行が止まってしまう。"""
        import asyncio

        import db.dynamo as dyn
        import trading_core
        from trading_core import EmergencyLiquidationFailed

        mock_trader = self._mock_trader()
        mock_trader.execute_emergency_liquidation.side_effect = RuntimeError("Alpaca API down")
        mock_notifier = MagicMock()
        mock_notifier.send_pipeline_log = AsyncMock()
        mock_notifier.notify_alert = AsyncMock()

        with patch.object(trading_core, "trader", mock_trader), patch.object(trading_core, "notifier", mock_notifier):
            with pytest.raises(EmergencyLiquidationFailed):
                asyncio.run(trading_core.emergency_check(scheduled_time="2026-06-01T14:00:00Z"))

        assert dyn.emergency_liquidation_recorded_for("2026-06-01") is False

    def test_next_day_checks_again(self, dynamo_table):
        import asyncio

        import trading_core

        mock_trader = self._mock_trader()
        mock_notifier = MagicMock()
        mock_notifier.send_pipeline_log = AsyncMock()
        mock_notifier.notify_alert = AsyncMock()

        with (
            patch.object(trading_core, "trader", mock_trader),
            patch.object(trading_core, "notifier", mock_notifier),
            patch.object(trading_core, "acquire_lock", return_value=True),
        ):
            asyncio.run(trading_core.emergency_check(scheduled_time="2026-06-01T14:00:00Z"))
            asyncio.run(trading_core.emergency_check(scheduled_time="2026-06-02T14:00:00Z"))

        assert mock_trader.execute_emergency_liquidation.call_count == 2

    def test_no_positions_held_stops_and_cancels_pending_orders(self, dynamo_table):
        """F4: 保有がゼロでも、清算対象が無いだけで停止と未約定注文の取り消し
        は行う（放置すると未約定の買い注文がそのまま約定してしまう）。"""
        import asyncio

        import db.dynamo as dyn
        import trading_core

        mock_trader = self._mock_trader()
        mock_trader.get_positions.return_value = []
        mock_notifier = MagicMock()
        mock_notifier.send_pipeline_log = AsyncMock()
        mock_notifier.notify_alert = AsyncMock()

        with patch.object(trading_core, "trader", mock_trader), patch.object(trading_core, "notifier", mock_notifier):
            asyncio.run(trading_core.emergency_check())

        mock_trader.execute_emergency_liquidation.assert_not_called()
        mock_trader.cancel_all_orders.assert_called_once()
        assert dyn.get_scheduler_state() is False
        mock_notifier.notify_alert.assert_not_called()

    def test_no_positions_cancel_failure_notifies_and_raises(self, dynamo_table):
        """F4: 未約定注文の取り消し自体が失敗したら、素通りにせず通知して
        例外を送出する（保有がある場合の F2 と同じ扱い）。"""
        import asyncio

        import trading_core
        from trading_core import EmergencyLiquidationFailed

        mock_trader = self._mock_trader()
        mock_trader.get_positions.return_value = []
        mock_trader.cancel_all_orders.side_effect = RuntimeError("Alpaca API down")
        mock_notifier = MagicMock()
        mock_notifier.notify_alert = AsyncMock()

        with patch.object(trading_core, "trader", mock_trader), patch.object(trading_core, "notifier", mock_notifier):
            with pytest.raises(EmergencyLiquidationFailed, match="cancel pending orders failed"):
                asyncio.run(trading_core.emergency_check())

        mock_notifier.notify_alert.assert_awaited_once()

    def test_no_positions_stop_flag_failure_notifies_and_raises(self, dynamo_table, monkeypatch):
        """F4: 停止フラグの保存に失敗した場合も同様に通知して例外を送出する。"""
        import asyncio

        import trading_core
        from trading_core import EmergencyLiquidationFailed

        mock_trader = self._mock_trader()
        mock_trader.get_positions.return_value = []
        mock_notifier = MagicMock()
        mock_notifier.notify_alert = AsyncMock()

        def boom(_running):
            raise RuntimeError("DynamoDB unavailable")

        monkeypatch.setattr(trading_core, "set_scheduler_state", boom)

        with patch.object(trading_core, "trader", mock_trader), patch.object(trading_core, "notifier", mock_notifier):
            with pytest.raises(EmergencyLiquidationFailed, match="stop flag write failed"):
                asyncio.run(trading_core.emergency_check())

        mock_notifier.notify_alert.assert_awaited_once()

    def test_record_read_failure_fails_open_and_still_liquidates(self, dynamo_table, monkeypatch):
        """E3/E4: a failure reading today's record must not be read as "skip
        the safety net" - it should proceed as if not yet recorded."""
        import asyncio

        import trading_core

        def boom(_date):
            raise RuntimeError("DynamoDB unavailable")

        monkeypatch.setattr(trading_core, "emergency_liquidation_recorded_for", boom)

        mock_trader = self._mock_trader()
        mock_notifier = MagicMock()
        mock_notifier.send_pipeline_log = AsyncMock()
        mock_notifier.notify_alert = AsyncMock()

        with patch.object(trading_core, "trader", mock_trader), patch.object(trading_core, "notifier", mock_notifier):
            asyncio.run(trading_core.emergency_check())

        mock_trader.execute_emergency_liquidation.assert_called_once()


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
