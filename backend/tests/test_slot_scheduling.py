"""Slot id / scheduled_time handling (Issue M3).

EventBridge Scheduler passes its own <aws.scheduler.scheduled-time> (the
intended fire time) through lambda_trading.handler into
trading_cycle()/emergency_check(). The slot must be keyed off that, not
whenever the Lambda actually started, so a cold-start/queueing delay that
straddles a slot boundary can't split one scheduled firing into two slots
(and two different client_order_ids).
"""
from datetime import datetime

from trading_core import NY_TZ, _resolve_now, _slot_id


class TestResolveNow:
    def test_parses_scheduled_time(self):
        resolved = _resolve_now("2026-06-01T13:30:00Z")
        assert resolved.astimezone(NY_TZ).strftime("%Y-%m-%d %H:%M") == "2026-06-01 09:30"

    def test_falls_back_to_wall_clock_when_absent(self):
        resolved = _resolve_now(None)
        assert resolved.tzinfo is not None

    def test_falls_back_to_wall_clock_when_empty_string(self):
        resolved = _resolve_now("")
        assert resolved.tzinfo is not None

    def test_falls_back_to_wall_clock_on_unparseable_value(self):
        resolved = _resolve_now("not-a-timestamp")
        assert resolved.tzinfo is not None


class TestSlotId:
    def test_same_scheduled_time_gives_same_slot(self):
        """Two invocations for the same scheduled firing (e.g. one retried
        after the first crashed) must resolve to the same slot regardless of
        when each one actually happened to run."""
        scheduled_time = "2026-06-01T13:59:50Z"  # 09:59:50 ET
        slot_a = _slot_id(30, _resolve_now(scheduled_time))
        slot_b = _slot_id(30, _resolve_now(scheduled_time))
        assert slot_a == slot_b

    def test_scheduled_time_on_a_slot_boundary(self):
        scheduled_time = "2026-06-01T13:30:00Z"  # 09:30:00 ET, exactly a slot start
        assert _slot_id(30, _resolve_now(scheduled_time)) == "20260601T0930"

    def test_scheduled_time_mid_slot_floors_down(self):
        scheduled_time = "2026-06-01T13:44:59Z"  # 09:44:59 ET, still the 09:30 slot
        assert _slot_id(30, _resolve_now(scheduled_time)) == "20260601T0930"

    def test_without_scheduled_time_a_boundary_crossing_wall_clock_differs(self):
        """Sanity check on the fallback path: a manual invocation (no
        scheduled_time) is keyed by the actual current time, so two different
        times straddling a boundary DO produce different slots - unlike the
        scheduled_time-driven path above, which is immune to this."""
        before = NY_TZ.localize(datetime(2026, 6, 1, 9, 29, 59))
        after = NY_TZ.localize(datetime(2026, 6, 1, 9, 30, 1))
        assert _slot_id(30, before) != _slot_id(30, after)
        assert _slot_id(30, before) == "20260601T0900"
        assert _slot_id(30, after) == "20260601T0930"
