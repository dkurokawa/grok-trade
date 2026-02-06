"""Risk Guard unit tests - comprehensive edge cases including new Opus pipeline checks"""
import pytest
import os
from unittest.mock import patch

from risk_guard import RiskGuard, RiskCheckResult


class TestRiskCheckResult:
    def test_allowed_result(self):
        result = RiskCheckResult(allowed=True)
        assert result.allowed is True
        assert result.reason is None
        assert result.adjustments == []

    def test_blocked_result(self):
        result = RiskCheckResult(allowed=False, reason="Test reason")
        assert result.allowed is False
        assert result.reason == "Test reason"

    def test_result_with_adjustments(self):
        adj = [{"field": "pct", "original": 60, "adjusted": 50, "reason": "limit"}]
        result = RiskCheckResult(allowed=True, adjustments=adj)
        assert len(result.adjustments) == 1


class TestRiskGuardInit:
    def test_default_values(self):
        with patch.dict(os.environ, {}, clear=True):
            guard = RiskGuard()
            assert guard.max_daily_loss == 500.0
            assert guard.max_position_pct == 50.0
            assert guard.max_single_trade_pct == 25.0
            assert guard.min_confidence == 40

    def test_custom_env_values(self):
        with patch.dict(os.environ, {
            "MAX_DAILY_LOSS": "1000",
            "MAX_POSITION_RATIO": "0.7",
            "MAX_SINGLE_TRADE_PCT": "0.3",
            "MIN_CONFIDENCE": "50",
        }):
            guard = RiskGuard()
            assert guard.max_daily_loss == 1000.0
            assert guard.max_position_pct == 70.0
            assert guard.max_single_trade_pct == 30.0
            assert guard.min_confidence == 50

    def test_invalid_env_values(self):
        with patch.dict(os.environ, {"MAX_DAILY_LOSS": "not_a_number"}):
            with pytest.raises(ValueError):
                RiskGuard()


# ========================
# New: check() for Opus pipeline
# ========================

class TestCheckOpusPipeline:
    @pytest.fixture
    def guard(self):
        with patch.dict(os.environ, {
            "MAX_DAILY_LOSS": "500",
            "MAX_POSITION_RATIO": "0.5",
            "MAX_SINGLE_TRADE_PCT": "0.25",
            "MIN_CONFIDENCE": "40",
        }):
            return RiskGuard()

    def test_hold_always_passes(self, guard):
        decision = {"action": "hold", "confidence": 10}
        portfolio = {"daily_pnl": 0}
        result = guard.check(decision, portfolio)
        assert result.allowed is True

    def test_daily_loss_blocks(self, guard):
        decision = {"action": "buy", "confidence": 80, "stop_loss": 100}
        portfolio = {"daily_pnl": -600}
        result = guard.check(decision, portfolio)
        assert result.allowed is False
        assert result.reason == "daily_loss_limit_reached"

    def test_low_confidence_blocks(self, guard):
        decision = {"action": "buy", "confidence": 30, "stop_loss": 100}
        portfolio = {"daily_pnl": 0}
        result = guard.check(decision, portfolio)
        assert result.allowed is False
        assert "confidence_too_low" in result.reason

    def test_confidence_boundary_39_blocks(self, guard):
        decision = {"action": "buy", "confidence": 39, "stop_loss": 100}
        portfolio = {"daily_pnl": 0}
        result = guard.check(decision, portfolio)
        assert result.allowed is False

    def test_confidence_boundary_40_passes(self, guard):
        decision = {"action": "buy", "confidence": 40, "stop_loss": 100, "position_size_pct": 20}
        portfolio = {"daily_pnl": 0}
        result = guard.check(decision, portfolio)
        assert result.allowed is True

    def test_missing_stop_loss_blocks(self, guard):
        decision = {"action": "buy", "confidence": 80, "position_size_pct": 20}
        portfolio = {"daily_pnl": 0}
        result = guard.check(decision, portfolio)
        assert result.allowed is False
        assert result.reason == "stop_loss_required"

    def test_stop_loss_none_blocks(self, guard):
        decision = {"action": "buy", "confidence": 80, "stop_loss": None, "position_size_pct": 20}
        portfolio = {"daily_pnl": 0}
        result = guard.check(decision, portfolio)
        assert result.allowed is False
        assert result.reason == "stop_loss_required"

    def test_sell_without_stop_loss_passes(self, guard):
        """sell は既存ポジション決済なので stop_loss 不要"""
        decision = {"action": "sell", "confidence": 80}
        portfolio = {"daily_pnl": 0}
        result = guard.check(decision, portfolio)
        assert result.allowed is True

    def test_position_size_adjusted_to_max(self, guard):
        decision = {"action": "buy", "confidence": 80, "stop_loss": 100, "position_size_pct": 60}
        portfolio = {"daily_pnl": 0}
        result = guard.check(decision, portfolio)
        assert result.allowed is True
        # 60% > 50% max → adjusted to 50%, then 50% > 25% single trade → adjusted to 25%
        assert decision["position_size_pct"] == 25.0
        assert len(result.adjustments) == 2

    def test_position_size_within_limits(self, guard):
        decision = {"action": "buy", "confidence": 80, "stop_loss": 100, "position_size_pct": 20}
        portfolio = {"daily_pnl": 0}
        result = guard.check(decision, portfolio)
        assert result.allowed is True
        assert result.adjustments == []

    def test_position_size_at_single_trade_limit(self, guard):
        """25% は上限ちょうど → 調整なし"""
        decision = {"action": "buy", "confidence": 80, "stop_loss": 100, "position_size_pct": 25}
        portfolio = {"daily_pnl": 0}
        result = guard.check(decision, portfolio)
        assert result.allowed is True
        assert result.adjustments == []

    def test_adjustments_logged_correctly(self, guard):
        decision = {"action": "buy", "confidence": 80, "stop_loss": 100, "position_size_pct": 55}
        portfolio = {"daily_pnl": 0}
        result = guard.check(decision, portfolio)
        assert result.allowed is True
        assert any(a["field"] == "position_size_pct" for a in result.adjustments)


# ========================
# Legacy: check_order() backward compatibility
# ========================

class TestCheckOrderDailyLoss:
    @pytest.fixture
    def guard(self):
        with patch.dict(os.environ, {"MAX_DAILY_LOSS": "500", "MAX_POSITION_RATIO": "0.5"}):
            return RiskGuard()

    def test_within_loss_limit(self, guard, empty_positions):
        result = guard.check_order(
            action="buy", symbol="MSTR", quantity=10, price=350.0,
            account_balance=100000.0, current_positions=empty_positions, daily_pnl=-400.0,
        )
        assert result.allowed is True

    def test_at_exact_loss_limit(self, guard, empty_positions):
        result = guard.check_order(
            action="buy", symbol="MSTR", quantity=10, price=350.0,
            account_balance=100000.0, current_positions=empty_positions, daily_pnl=-500.0,
        )
        assert result.allowed is False
        assert "Daily loss limit" in result.reason

    def test_beyond_loss_limit(self, guard, empty_positions):
        result = guard.check_order(
            action="buy", symbol="MSTR", quantity=10, price=350.0,
            account_balance=100000.0, current_positions=empty_positions, daily_pnl=-750.0,
        )
        assert result.allowed is False

    def test_positive_pnl_allowed(self, guard, empty_positions):
        result = guard.check_order(
            action="buy", symbol="MSTR", quantity=10, price=350.0,
            account_balance=100000.0, current_positions=empty_positions, daily_pnl=1000.0,
        )
        assert result.allowed is True


class TestCheckOrderHold:
    @pytest.fixture
    def guard(self):
        with patch.dict(os.environ, {"MAX_DAILY_LOSS": "500", "MAX_POSITION_RATIO": "0.5"}):
            return RiskGuard()

    def test_hold_always_allowed(self, guard, empty_positions):
        result = guard.check_order(
            action="hold", symbol="MSTR", quantity=0, price=350.0,
            account_balance=100000.0, current_positions=empty_positions, daily_pnl=0.0,
        )
        assert result.allowed is True

    def test_hold_blocked_by_loss_limit(self, guard, empty_positions):
        result = guard.check_order(
            action="hold", symbol="MSTR", quantity=0, price=350.0,
            account_balance=100000.0, current_positions=empty_positions, daily_pnl=-600.0,
        )
        assert result.allowed is False


class TestCheckOrderSell:
    @pytest.fixture
    def guard(self):
        with patch.dict(os.environ, {"MAX_DAILY_LOSS": "500", "MAX_POSITION_RATIO": "0.5"}):
            return RiskGuard()

    def test_sell_always_allowed(self, guard, mock_positions):
        result = guard.check_order(
            action="sell", symbol="MSTR", quantity=10, price=350.0,
            account_balance=100000.0, current_positions=mock_positions, daily_pnl=-200.0,
        )
        assert result.allowed is True

    def test_sell_blocked_by_loss_limit(self, guard, empty_positions):
        result = guard.check_order(
            action="sell", symbol="MSTR", quantity=10, price=350.0,
            account_balance=100000.0, current_positions=empty_positions, daily_pnl=-600.0,
        )
        assert result.allowed is False


class TestCheckOrderBuyPositionRatio:
    @pytest.fixture
    def guard(self):
        with patch.dict(os.environ, {"MAX_DAILY_LOSS": "500", "MAX_POSITION_RATIO": "0.5"}):
            return RiskGuard()

    def test_buy_within_ratio(self, guard, empty_positions):
        result = guard.check_order(
            action="buy", symbol="MSTR", quantity=100, price=350.0,
            account_balance=100000.0, current_positions=empty_positions, daily_pnl=0.0,
        )
        assert result.allowed is True

    def test_buy_exceeds_ratio(self, guard, empty_positions):
        result = guard.check_order(
            action="buy", symbol="MSTR", quantity=200, price=350.0,
            account_balance=100000.0, current_positions=empty_positions, daily_pnl=0.0,
        )
        assert result.allowed is False
        assert "Position ratio" in result.reason

    def test_buy_with_existing_positions(self, guard, large_position):
        result = guard.check_order(
            action="buy", symbol="TSLA", quantity=100, price=350.0,
            account_balance=60000.0, current_positions=large_position, daily_pnl=0.0,
        )
        assert result.allowed is False


class TestCheckOrderBuyBalance:
    @pytest.fixture
    def guard(self):
        with patch.dict(os.environ, {"MAX_DAILY_LOSS": "500", "MAX_POSITION_RATIO": "0.5"}):
            return RiskGuard()

    def test_buy_within_balance(self, guard, empty_positions):
        result = guard.check_order(
            action="buy", symbol="MSTR", quantity=10, price=350.0,
            account_balance=100000.0, current_positions=empty_positions, daily_pnl=0.0,
        )
        assert result.allowed is True

    def test_buy_exceeds_balance(self, guard, empty_positions):
        result = guard.check_order(
            action="buy", symbol="MSTR", quantity=100, price=350.0,
            account_balance=10000.0, current_positions=empty_positions, daily_pnl=0.0,
        )
        assert result.allowed is False

    def test_buy_zero_balance(self, guard, empty_positions):
        result = guard.check_order(
            action="buy", symbol="MSTR", quantity=1, price=350.0,
            account_balance=0.0, current_positions=empty_positions, daily_pnl=0.0,
        )
        assert result.allowed is False


class TestCheckSystemHealth:
    @pytest.fixture
    def guard(self):
        with patch.dict(os.environ, {"MAX_DAILY_LOSS": "500", "MAX_POSITION_RATIO": "0.5"}):
            return RiskGuard()

    def test_healthy_system(self, guard):
        result = guard.check_system_health(daily_pnl=0.0, error_count=0)
        assert result.allowed is True

    def test_unhealthy_loss_limit(self, guard):
        result = guard.check_system_health(daily_pnl=-500.0, error_count=0)
        assert result.allowed is False

    def test_unhealthy_error_count(self, guard):
        result = guard.check_system_health(daily_pnl=0.0, error_count=5)
        assert result.allowed is False
        assert "Too many errors" in result.reason

    def test_healthy_under_error_threshold(self, guard):
        result = guard.check_system_health(daily_pnl=0.0, error_count=4)
        assert result.allowed is True

    def test_default_error_count(self, guard):
        result = guard.check_system_health(daily_pnl=0.0)
        assert result.allowed is True
