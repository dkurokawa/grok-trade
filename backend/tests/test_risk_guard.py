"""Risk Guard unit tests - comprehensive edge cases and error handling"""
import pytest
import os
from unittest.mock import patch

# Import after setting env vars in conftest
from risk_guard import RiskGuard, RiskCheckResult


class TestRiskCheckResult:
    """RiskCheckResult dataclass tests"""

    def test_allowed_result(self):
        """Test allowed result with no reason"""
        result = RiskCheckResult(allowed=True)
        assert result.allowed is True
        assert result.reason is None

    def test_blocked_result(self):
        """Test blocked result with reason"""
        result = RiskCheckResult(allowed=False, reason="Test reason")
        assert result.allowed is False
        assert result.reason == "Test reason"

    def test_blocked_without_reason(self):
        """Test blocked result can exist without reason (edge case)"""
        result = RiskCheckResult(allowed=False)
        assert result.allowed is False
        assert result.reason is None


class TestRiskGuardInit:
    """RiskGuard initialization tests"""

    def test_default_values(self):
        """Test default values when env vars not set"""
        with patch.dict(os.environ, {}, clear=True):
            guard = RiskGuard()
            assert guard.max_daily_loss == 500.0
            assert guard.max_position_ratio == 0.5

    def test_custom_env_values(self):
        """Test custom values from environment"""
        with patch.dict(os.environ, {"MAX_DAILY_LOSS": "1000", "MAX_POSITION_RATIO": "0.7"}):
            guard = RiskGuard()
            assert guard.max_daily_loss == 1000.0
            assert guard.max_position_ratio == 0.7

    def test_invalid_env_values(self):
        """Test that invalid env values raise errors"""
        with patch.dict(os.environ, {"MAX_DAILY_LOSS": "not_a_number"}):
            with pytest.raises(ValueError):
                RiskGuard()

    def test_zero_loss_limit(self):
        """Test zero daily loss limit (strict mode)"""
        with patch.dict(os.environ, {"MAX_DAILY_LOSS": "0"}):
            guard = RiskGuard()
            assert guard.max_daily_loss == 0.0

    def test_negative_env_value(self):
        """Test negative values are accepted (unusual but valid config)"""
        with patch.dict(os.environ, {"MAX_DAILY_LOSS": "-100"}):
            guard = RiskGuard()
            assert guard.max_daily_loss == -100.0


class TestCheckOrderDailyLoss:
    """Daily loss limit tests"""

    @pytest.fixture
    def guard(self):
        with patch.dict(os.environ, {"MAX_DAILY_LOSS": "500", "MAX_POSITION_RATIO": "0.5"}):
            return RiskGuard()

    def test_within_loss_limit(self, guard, empty_positions):
        """Test order allowed when within loss limit"""
        result = guard.check_order(
            action="buy",
            symbol="MSTR",
            quantity=10,
            price=350.0,
            account_balance=100000.0,
            current_positions=empty_positions,
            daily_pnl=-400.0
        )
        assert result.allowed is True

    def test_at_exact_loss_limit(self, guard, empty_positions):
        """Test order blocked at exactly the loss limit"""
        result = guard.check_order(
            action="buy",
            symbol="MSTR",
            quantity=10,
            price=350.0,
            account_balance=100000.0,
            current_positions=empty_positions,
            daily_pnl=-500.0
        )
        assert result.allowed is False
        assert "Daily loss limit" in result.reason

    def test_beyond_loss_limit(self, guard, empty_positions):
        """Test order blocked beyond loss limit"""
        result = guard.check_order(
            action="buy",
            symbol="MSTR",
            quantity=10,
            price=350.0,
            account_balance=100000.0,
            current_positions=empty_positions,
            daily_pnl=-750.0
        )
        assert result.allowed is False
        assert "-$750.00" in result.reason or "-750" in result.reason

    def test_positive_pnl_allowed(self, guard, empty_positions):
        """Test order allowed when in profit"""
        result = guard.check_order(
            action="buy",
            symbol="MSTR",
            quantity=10,
            price=350.0,
            account_balance=100000.0,
            current_positions=empty_positions,
            daily_pnl=1000.0
        )
        assert result.allowed is True

    def test_zero_pnl_allowed(self, guard, empty_positions):
        """Test order allowed at zero P&L"""
        result = guard.check_order(
            action="buy",
            symbol="MSTR",
            quantity=10,
            price=350.0,
            account_balance=100000.0,
            current_positions=empty_positions,
            daily_pnl=0.0
        )
        assert result.allowed is True

    def test_loss_limit_blocks_all_actions(self, guard, empty_positions):
        """Test loss limit blocks buy but also sell when at limit"""
        # Even sell should be blocked when loss limit reached
        result = guard.check_order(
            action="sell",
            symbol="MSTR",
            quantity=10,
            price=350.0,
            account_balance=100000.0,
            current_positions=empty_positions,
            daily_pnl=-600.0
        )
        # Actually in current implementation, the daily loss check comes first
        # so even sell is blocked - this tests the implementation
        assert result.allowed is False


class TestCheckOrderHold:
    """Hold action tests"""

    @pytest.fixture
    def guard(self):
        with patch.dict(os.environ, {"MAX_DAILY_LOSS": "500", "MAX_POSITION_RATIO": "0.5"}):
            return RiskGuard()

    def test_hold_always_allowed(self, guard, empty_positions):
        """Test hold action is always allowed"""
        result = guard.check_order(
            action="hold",
            symbol="MSTR",
            quantity=0,
            price=350.0,
            account_balance=100000.0,
            current_positions=empty_positions,
            daily_pnl=0.0
        )
        assert result.allowed is True

    def test_hold_with_quantity(self, guard, empty_positions):
        """Test hold is allowed even with non-zero quantity (edge case)"""
        result = guard.check_order(
            action="hold",
            symbol="MSTR",
            quantity=100,
            price=350.0,
            account_balance=100000.0,
            current_positions=empty_positions,
            daily_pnl=0.0
        )
        assert result.allowed is True

    def test_hold_blocked_by_loss_limit(self, guard, empty_positions):
        """Test hold is blocked when at loss limit (loss check comes first)"""
        result = guard.check_order(
            action="hold",
            symbol="MSTR",
            quantity=0,
            price=350.0,
            account_balance=100000.0,
            current_positions=empty_positions,
            daily_pnl=-600.0
        )
        # Loss limit check happens before action-specific checks
        assert result.allowed is False


class TestCheckOrderSell:
    """Sell action tests"""

    @pytest.fixture
    def guard(self):
        with patch.dict(os.environ, {"MAX_DAILY_LOSS": "500", "MAX_POSITION_RATIO": "0.5"}):
            return RiskGuard()

    def test_sell_always_allowed(self, guard, mock_positions):
        """Test sell is always allowed (position reduction is safe)"""
        result = guard.check_order(
            action="sell",
            symbol="MSTR",
            quantity=10,
            price=350.0,
            account_balance=100000.0,
            current_positions=mock_positions,
            daily_pnl=-200.0
        )
        assert result.allowed is True

    def test_sell_without_position(self, guard, empty_positions):
        """Test sell allowed even without position (short selling scenario)"""
        result = guard.check_order(
            action="sell",
            symbol="MSTR",
            quantity=10,
            price=350.0,
            account_balance=100000.0,
            current_positions=empty_positions,
            daily_pnl=0.0
        )
        assert result.allowed is True

    def test_sell_large_quantity(self, guard, mock_positions):
        """Test sell allowed for large quantity"""
        result = guard.check_order(
            action="sell",
            symbol="MSTR",
            quantity=1000,
            price=350.0,
            account_balance=100000.0,
            current_positions=mock_positions,
            daily_pnl=0.0
        )
        assert result.allowed is True


class TestCheckOrderBuyPositionRatio:
    """Buy action position ratio tests"""

    @pytest.fixture
    def guard(self):
        with patch.dict(os.environ, {"MAX_DAILY_LOSS": "500", "MAX_POSITION_RATIO": "0.5"}):
            return RiskGuard()

    def test_buy_within_ratio(self, guard, empty_positions):
        """Test buy allowed within position ratio"""
        # $35,000 order on $100,000 portfolio = 35%
        result = guard.check_order(
            action="buy",
            symbol="MSTR",
            quantity=100,
            price=350.0,
            account_balance=100000.0,
            current_positions=empty_positions,
            daily_pnl=0.0
        )
        assert result.allowed is True

    def test_buy_at_exact_ratio(self, guard, empty_positions):
        """Test buy at exactly 50% ratio"""
        # $50,000 order on $100,000 portfolio = 50%
        result = guard.check_order(
            action="buy",
            symbol="MSTR",
            quantity=100,
            price=500.0,
            account_balance=100000.0,
            current_positions=empty_positions,
            daily_pnl=0.0
        )
        assert result.allowed is True

    def test_buy_exceeds_ratio(self, guard, empty_positions):
        """Test buy blocked when exceeding ratio"""
        # $70,000 order on $100,000 portfolio = 70%
        result = guard.check_order(
            action="buy",
            symbol="MSTR",
            quantity=200,
            price=350.0,
            account_balance=100000.0,
            current_positions=empty_positions,
            daily_pnl=0.0
        )
        assert result.allowed is False
        assert "Position ratio" in result.reason

    def test_buy_with_existing_positions(self, guard, large_position):
        """Test buy with existing large position"""
        # Existing: $40,000, New order: $35,000 = $75,000 total = 75%
        result = guard.check_order(
            action="buy",
            symbol="TSLA",
            quantity=100,
            price=350.0,
            account_balance=60000.0,  # Cash remaining
            current_positions=large_position,
            daily_pnl=0.0
        )
        assert result.allowed is False
        assert "Position ratio" in result.reason

    def test_buy_small_order_with_large_position(self, guard, large_position):
        """Test small buy allowed with large existing position"""
        # Existing: $40,000, New order: $350 = $40,350 total on $60k+$40k = 40.35%
        result = guard.check_order(
            action="buy",
            symbol="TSLA",
            quantity=1,
            price=350.0,
            account_balance=60000.0,
            current_positions=large_position,
            daily_pnl=0.0
        )
        assert result.allowed is True


class TestCheckOrderBuyBalance:
    """Buy action balance tests"""

    @pytest.fixture
    def guard(self):
        with patch.dict(os.environ, {"MAX_DAILY_LOSS": "500", "MAX_POSITION_RATIO": "0.5"}):
            return RiskGuard()

    def test_buy_within_balance(self, guard, empty_positions):
        """Test buy allowed within balance"""
        result = guard.check_order(
            action="buy",
            symbol="MSTR",
            quantity=10,
            price=350.0,
            account_balance=100000.0,
            current_positions=empty_positions,
            daily_pnl=0.0
        )
        assert result.allowed is True

    def test_buy_exceeds_balance(self, guard, empty_positions):
        """Test buy blocked when exceeding balance - position ratio check runs first"""
        result = guard.check_order(
            action="buy",
            symbol="MSTR",
            quantity=100,
            price=350.0,
            account_balance=10000.0,  # Only $10k, order is $35k (350% > 50% ratio)
            current_positions=empty_positions,
            daily_pnl=0.0
        )
        assert result.allowed is False
        # Position ratio check happens first: 35000/10000 = 350% > 50%
        assert "Position ratio" in result.reason or "Insufficient balance" in result.reason

    def test_buy_exactly_at_balance(self, guard, empty_positions):
        """Test buy at exactly available balance"""
        # Note: position ratio check might block this before balance check
        result = guard.check_order(
            action="buy",
            symbol="MSTR",
            quantity=10,
            price=100.0,
            account_balance=1000.0,  # $1000 balance, $1000 order
            current_positions=empty_positions,
            daily_pnl=0.0
        )
        # This would be 100% position ratio, so it's blocked by ratio first
        assert result.allowed is False

    def test_buy_zero_balance(self, guard, empty_positions):
        """Test buy blocked with zero balance"""
        result = guard.check_order(
            action="buy",
            symbol="MSTR",
            quantity=1,
            price=350.0,
            account_balance=0.0,
            current_positions=empty_positions,
            daily_pnl=0.0
        )
        assert result.allowed is False

    def test_buy_negative_balance(self, guard, empty_positions):
        """Test buy blocked with negative balance (edge case)"""
        result = guard.check_order(
            action="buy",
            symbol="MSTR",
            quantity=1,
            price=350.0,
            account_balance=-1000.0,
            current_positions=empty_positions,
            daily_pnl=0.0
        )
        assert result.allowed is False


class TestCheckOrderEdgeCases:
    """Edge case tests"""

    @pytest.fixture
    def guard(self):
        with patch.dict(os.environ, {"MAX_DAILY_LOSS": "500", "MAX_POSITION_RATIO": "0.5"}):
            return RiskGuard()

    def test_zero_price(self, guard, empty_positions):
        """Test with zero price (edge case)"""
        result = guard.check_order(
            action="buy",
            symbol="MSTR",
            quantity=10,
            price=0.0,
            account_balance=100000.0,
            current_positions=empty_positions,
            daily_pnl=0.0
        )
        # Zero price means $0 order value, which is within limits
        assert result.allowed is True

    def test_zero_quantity(self, guard, empty_positions):
        """Test with zero quantity (edge case)"""
        result = guard.check_order(
            action="buy",
            symbol="MSTR",
            quantity=0,
            price=350.0,
            account_balance=100000.0,
            current_positions=empty_positions,
            daily_pnl=0.0
        )
        assert result.allowed is True

    def test_negative_price(self, guard, empty_positions):
        """Test with negative price (invalid but should handle)"""
        result = guard.check_order(
            action="buy",
            symbol="MSTR",
            quantity=10,
            price=-350.0,
            account_balance=100000.0,
            current_positions=empty_positions,
            daily_pnl=0.0
        )
        # Negative order value, code might not handle this well
        # Test current behavior
        assert isinstance(result, RiskCheckResult)

    def test_invalid_action(self, guard, empty_positions):
        """Test with invalid action"""
        result = guard.check_order(
            action="invalid",
            symbol="MSTR",
            quantity=10,
            price=350.0,
            account_balance=100000.0,
            current_positions=empty_positions,
            daily_pnl=0.0
        )
        # Should pass through (no specific handling for invalid actions)
        assert result.allowed is True

    def test_empty_symbol(self, guard, empty_positions):
        """Test with empty symbol"""
        result = guard.check_order(
            action="buy",
            symbol="",
            quantity=10,
            price=350.0,
            account_balance=100000.0,
            current_positions=empty_positions,
            daily_pnl=0.0
        )
        # Symbol is not validated in risk_guard
        assert isinstance(result, RiskCheckResult)

    def test_very_large_values(self, guard, empty_positions):
        """Test with very large values"""
        result = guard.check_order(
            action="buy",
            symbol="MSTR",
            quantity=1000000,
            price=1000000.0,
            account_balance=1e15,  # Very large balance
            current_positions=empty_positions,
            daily_pnl=0.0
        )
        # Should handle without overflow
        assert isinstance(result, RiskCheckResult)

    def test_position_with_missing_market_value(self, guard):
        """Test with position missing market_value field"""
        positions = [{"symbol": "MSTR", "qty": 10}]  # No market_value
        result = guard.check_order(
            action="buy",
            symbol="TSLA",
            quantity=10,
            price=350.0,
            account_balance=100000.0,
            current_positions=positions,
            daily_pnl=0.0
        )
        # Should use default 0 for missing market_value
        assert result.allowed is True


class TestCheckSystemHealth:
    """System health check tests"""

    @pytest.fixture
    def guard(self):
        with patch.dict(os.environ, {"MAX_DAILY_LOSS": "500", "MAX_POSITION_RATIO": "0.5"}):
            return RiskGuard()

    def test_healthy_system(self, guard):
        """Test healthy system passes"""
        result = guard.check_system_health(daily_pnl=0.0, error_count=0)
        assert result.allowed is True

    def test_healthy_with_profit(self, guard):
        """Test system healthy when in profit"""
        result = guard.check_system_health(daily_pnl=1000.0, error_count=0)
        assert result.allowed is True

    def test_unhealthy_loss_limit(self, guard):
        """Test system unhealthy at loss limit"""
        result = guard.check_system_health(daily_pnl=-500.0, error_count=0)
        assert result.allowed is False
        assert "Daily loss limit" in result.reason

    def test_unhealthy_beyond_loss_limit(self, guard):
        """Test system unhealthy beyond loss limit"""
        result = guard.check_system_health(daily_pnl=-750.0, error_count=0)
        assert result.allowed is False

    def test_unhealthy_error_count(self, guard):
        """Test system unhealthy with many errors"""
        result = guard.check_system_health(daily_pnl=0.0, error_count=5)
        assert result.allowed is False
        assert "Too many errors" in result.reason

    def test_healthy_under_error_threshold(self, guard):
        """Test system healthy under error threshold"""
        result = guard.check_system_health(daily_pnl=0.0, error_count=4)
        assert result.allowed is True

    def test_unhealthy_both_conditions(self, guard):
        """Test system unhealthy with both conditions met"""
        result = guard.check_system_health(daily_pnl=-600.0, error_count=10)
        assert result.allowed is False
        # Loss limit check comes first
        assert "Daily loss limit" in result.reason

    def test_default_error_count(self, guard):
        """Test default error count of 0"""
        result = guard.check_system_health(daily_pnl=0.0)
        assert result.allowed is True

    def test_negative_error_count(self, guard):
        """Test negative error count (edge case)"""
        result = guard.check_system_health(daily_pnl=0.0, error_count=-1)
        assert result.allowed is True
