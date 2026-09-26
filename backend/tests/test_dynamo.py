"""DynamoDB data-layer tests (moto-backed)."""

import time
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def dyn(dynamo_table):
    import db.dynamo as d

    return d


class TestSchedulerState:
    def test_default_is_running(self, dyn):
        assert dyn.get_scheduler_state() is True

    def test_round_trip(self, dyn):
        dyn.set_scheduler_state(False)
        assert dyn.get_scheduler_state() is False
        dyn.set_scheduler_state(True)
        assert dyn.get_scheduler_state() is True

    def test_read_failure_fails_closed(self, dyn, monkeypatch):
        """A DynamoDB read error must read as "stopped", never "running" -
        the opposite would make the kill switch fail open."""
        broken_table = MagicMock()
        broken_table.get_item.side_effect = RuntimeError("boom")
        monkeypatch.setattr(dyn, "_get_table", lambda: broken_table)
        assert dyn.get_scheduler_state() is False

    def test_write_failure_raises(self, dyn, monkeypatch):
        """A DynamoDB write error must propagate, not be swallowed - callers
        (the /stop /start endpoints, the daily-loss auto-stop) need to know
        the flag did not actually change."""
        broken_table = MagicMock()
        broken_table.put_item.side_effect = RuntimeError("boom")
        monkeypatch.setattr(dyn, "_get_table", lambda: broken_table)
        with pytest.raises(RuntimeError):
            dyn.set_scheduler_state(False)


class TestTrades:
    def _log(self, dyn, symbol="MSTR", **kw):
        kw.setdefault("cycle_id", "c1")
        kw.setdefault("action", "buy")
        kw.setdefault("quantity", 10.0)
        kw.setdefault("price", 350.55)
        kw.setdefault("order_type", "market")
        kw.setdefault("status", "filled")
        kw.setdefault("alpaca_order_id", "o1")
        dyn.log_trade(symbol=symbol, **kw)

    def test_newest_first(self, dyn):
        self._log(dyn, "MSTR")
        time.sleep(0.002)
        self._log(dyn, "TSLA")
        trades = dyn.get_trades(10)
        assert [t["symbol"] for t in trades] == ["TSLA", "MSTR"]

    def test_float_round_trip(self, dyn):
        self._log(dyn, price=350.55, stop_loss=330.25, take_profit=400.75)
        t = dyn.get_trades(1)[0]
        assert (t["price"], t["stop_loss"], t["take_profit"]) == (350.55, 330.25, 400.75)
        assert isinstance(t["price"], float)

    def test_protective_prices_optional(self, dyn):
        self._log(dyn)
        t = dyn.get_trades(1)[0]
        assert t["stop_loss"] is None and t["take_profit"] is None

    def test_limit(self, dyn):
        for i in range(5):
            self._log(dyn, f"S{i}")
            time.sleep(0.001)
        assert len(dyn.get_trades(3)) == 3

    def test_empty(self, dyn):
        assert dyn.get_trades(10) == []


class TestPipelineLog:
    def test_full_cycle_round_trip(self, dyn):
        dyn.log_pipeline(
            cycle_id="cycle-1",
            decision_engine="grok",
            grok_input={"market_data": {"MSTR": {"price": 360.0}}},
            grok_output={"significant_change": True, "sentiment": {"overall": 60}},
            grok_latency_ms=120,
            opus_output={"action": "buy", "confidence": 70, "quantity": 7},
            opus_latency_ms=800,
            opus_adjustments=[{"field": "position_size_pct", "original": 90, "adjusted": 50}],
            rg_passed=True,
            rg_adjustments=[],
            order_submitted=True,
            alpaca_order_id="order-1",
            execution_result={"status": "accepted"},
        )
        log = dyn.get_pipeline_logs(1)[0]
        assert log["cycle_id"] == "cycle-1"
        assert log["decision_engine"] == "grok"
        assert log["grok_output"]["sentiment"]["overall"] == 60
        assert log["opus_output"]["action"] == "buy"
        assert log["opus_adjustments"][0]["adjusted"] == 50
        assert log["order_submitted"] is True
        assert log["execution_result"]["status"] == "accepted"

    def test_blocked_cycle(self, dyn):
        dyn.log_pipeline(
            cycle_id="cycle-2",
            decision_engine="grok",
            rg_passed=False,
            rg_reason="confidence_too_low: 30",
        )
        log = dyn.get_pipeline_logs(1)[0]
        assert log["risk_guard_passed"] is False
        assert "confidence_too_low" in log["risk_guard_reason"]
        assert log["order_submitted"] is False

    def test_skipped_cycle(self, dyn):
        dyn.log_pipeline(cycle_id="cycle-3", opus_skipped=True)
        log = dyn.get_pipeline_logs(1)[0]
        assert log["opus_skipped"] is True
        assert log["opus_output"] is None

    def test_decisions_view_is_a_summary(self, dyn):
        """/decisions returns the summary fields, not the full Grok payload."""
        dyn.log_pipeline(
            cycle_id="cycle-4",
            decision_engine="grok",
            grok_output={"sentiment": {"overall": 10}},
            opus_output={"action": "hold"},
            rg_passed=True,
        )
        d = dyn.get_decisions(1)[0]
        assert d["opus_output"]["action"] == "hold"
        assert "grok_output" not in d

    def test_newest_first(self, dyn):
        dyn.log_pipeline(cycle_id="old")
        time.sleep(0.002)
        dyn.log_pipeline(cycle_id="new")
        assert dyn.get_pipeline_logs(10)[0]["cycle_id"] == "new"


class TestLocks:
    """Slot locks that dedupe a retried/duplicate scheduled invocation."""

    def test_first_caller_acquires(self, dyn):
        assert dyn.acquire_lock("trading", "20260101T0930") is True

    def test_second_caller_for_same_slot_is_rejected(self, dyn):
        assert dyn.acquire_lock("trading", "20260101T0930") is True
        assert dyn.acquire_lock("trading", "20260101T0930") is False

    def test_different_slots_both_acquire(self, dyn):
        assert dyn.acquire_lock("trading", "20260101T0930") is True
        assert dyn.acquire_lock("trading", "20260101T1000") is True

    def test_different_kinds_dont_collide(self, dyn):
        """trading#<slot> and emergency#<slot> are independent locks."""
        assert dyn.acquire_lock("trading", "20260101T0930") is True
        assert dyn.acquire_lock("emergency", "20260101T0930") is True

    def test_lock_item_carries_a_ttl(self, dyn):
        dyn.acquire_lock("trading", "20260101T0930")
        import time as _time

        item = dyn._get_table().get_item(Key={"pk": "LOCK", "sk": "trading#20260101T0930"})["Item"]
        assert int(item["ttl"]) > _time.time()


class TestPreviousSentimentSummary:
    """trading_core._previous_sentiment_summary() (Issue #11): Grok has no
    memory across calls, so the prior cycle's sentiment has to be fetched
    from DynamoDB and handed to it explicitly."""

    def test_no_previous_cycle_says_so(self, dynamo_table):
        from grok_client import NO_PREVIOUS_SENTIMENT
        from trading_core import _previous_sentiment_summary

        assert _previous_sentiment_summary() == NO_PREVIOUS_SENTIMENT

    def test_previous_cycle_without_grok_output_says_so(self, dynamo_table):
        import db.dynamo as dyn
        from grok_client import NO_PREVIOUS_SENTIMENT
        from trading_core import _previous_sentiment_summary

        dyn.log_pipeline(cycle_id="c1", opus_skipped=False)  # no grok_output
        assert _previous_sentiment_summary() == NO_PREVIOUS_SENTIMENT

    def test_previous_cycle_summarised(self, dynamo_table):
        import db.dynamo as dyn
        from trading_core import _previous_sentiment_summary

        dyn.log_pipeline(
            cycle_id="c1",
            grok_output={
                "timestamp": "2026-01-01T10:00:00",
                "significant_change": True,
                "sentiment": {"overall": 42},
            },
        )
        summary = _previous_sentiment_summary()
        assert "42" in summary
        assert "2026-01-01T10:00:00" in summary
        assert "True" in summary

    def test_read_failure_does_not_raise(self, dynamo_table, monkeypatch):
        import db.dynamo as dyn
        from trading_core import _previous_sentiment_summary

        def boom(*a, **kw):
            raise RuntimeError("boom")

        monkeypatch.setattr(dyn, "_get_table", boom)
        summary = _previous_sentiment_summary()
        assert "前回データなし" in summary


class TestEmergencyLiquidationRecord:
    """emergency_liquidation_recorded_for()/record_emergency_liquidation()
    (E4): STATE/emergency#<date> marks a day as already having had a
    liquidation attempt."""

    def test_not_recorded_by_default(self, dyn):
        assert dyn.emergency_liquidation_recorded_for("2026-06-01") is False

    def test_recorded_after_write(self, dyn):
        dyn.record_emergency_liquidation("2026-06-01")
        assert dyn.emergency_liquidation_recorded_for("2026-06-01") is True

    def test_different_dates_are_independent(self, dyn):
        dyn.record_emergency_liquidation("2026-06-01")
        assert dyn.emergency_liquidation_recorded_for("2026-06-02") is False

    def test_read_failure_fails_open_returns_false(self, dyn, monkeypatch):
        broken_table = MagicMock()
        broken_table.get_item.side_effect = RuntimeError("boom")
        monkeypatch.setattr(dyn, "_get_table", lambda: broken_table)
        assert dyn.emergency_liquidation_recorded_for("2026-06-01") is False

    def test_write_failure_raises(self, dyn, monkeypatch):
        broken_table = MagicMock()
        broken_table.put_item.side_effect = RuntimeError("boom")
        monkeypatch.setattr(dyn, "_get_table", lambda: broken_table)
        with pytest.raises(RuntimeError):
            dyn.record_emergency_liquidation("2026-06-01")
