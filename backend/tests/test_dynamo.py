"""DynamoDB data-layer tests (moto-backed)."""
import time

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
            cycle_id="cycle-2", decision_engine="grok",
            rg_passed=False, rg_reason="confidence_too_low: 30",
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
            cycle_id="cycle-4", decision_engine="grok",
            grok_output={"sentiment": {"overall": 10}},
            opus_output={"action": "hold"}, rg_passed=True,
        )
        d = dyn.get_decisions(1)[0]
        assert d["opus_output"]["action"] == "hold"
        assert "grok_output" not in d

    def test_newest_first(self, dyn):
        dyn.log_pipeline(cycle_id="old")
        time.sleep(0.002)
        dyn.log_pipeline(cycle_id="new")
        assert dyn.get_pipeline_logs(10)[0]["cycle_id"] == "new"
