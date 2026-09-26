"""Risk Guard unit tests - comprehensive edge cases including new Opus pipeline checks"""
import os
from unittest.mock import patch

import pytest

from risk_guard import RiskCheckResult, RiskGuard


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
        decision = {
            "action": "buy", "confidence": 40, "stop_loss": 100,
            "position_size_pct": 20, "quantity": 10,
        }
        portfolio = {"daily_pnl": 0}
        result = guard.check(decision, portfolio, price=100.0, equity=100000.0)
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
        decision = {
            "action": "buy", "confidence": 80, "stop_loss": 100,
            "position_size_pct": 60, "quantity": 100,
        }
        portfolio = {"daily_pnl": 0}
        result = guard.check(decision, portfolio, price=100.0, equity=100000.0)
        assert result.allowed is True
        # 60% > 50% max → adjusted to 50%, then 50% > 25% single trade → adjusted to 25%
        assert decision["position_size_pct"] == 25.0
        # 100 shares stays within the 25% (=250 share) cap, so only the two
        # position_size_pct adjustments are recorded, not a quantity one.
        assert len(result.adjustments) == 2

    def test_position_size_within_limits(self, guard):
        decision = {
            "action": "buy", "confidence": 80, "stop_loss": 100,
            "position_size_pct": 20, "quantity": 50,
        }
        portfolio = {"daily_pnl": 0}
        result = guard.check(decision, portfolio, price=100.0, equity=100000.0)
        assert result.allowed is True
        assert result.adjustments == []

    def test_position_size_at_single_trade_limit(self, guard):
        """25% は上限ちょうど → 調整なし"""
        decision = {
            "action": "buy", "confidence": 80, "stop_loss": 100,
            "position_size_pct": 25, "quantity": 50,
        }
        portfolio = {"daily_pnl": 0}
        result = guard.check(decision, portfolio, price=100.0, equity=100000.0)
        assert result.allowed is True
        assert result.adjustments == []

    def test_adjustments_logged_correctly(self, guard):
        decision = {
            "action": "buy", "confidence": 80, "stop_loss": 100,
            "position_size_pct": 55, "quantity": 50,
        }
        portfolio = {"daily_pnl": 0}
        result = guard.check(decision, portfolio, price=100.0, equity=100000.0)
        assert result.allowed is True
        assert any(a["field"] == "position_size_pct" for a in result.adjustments)


# ========================
# check(): quantity capped by the confirmed position_size_pct (Issue #2)
# ========================

class TestQuantityCappedByPositionSize:
    @pytest.fixture
    def guard(self):
        with patch.dict(os.environ, {
            "MAX_DAILY_LOSS": "500", "MAX_POSITION_RATIO": "0.5",
            "MAX_SINGLE_TRADE_PCT": "0.25", "MIN_CONFIDENCE": "40",
        }):
            return RiskGuard()

    def _decision(self, **overrides):
        decision = {
            "action": "buy", "confidence": 80, "stop_loss": 100,
            "position_size_pct": 60, "quantity": 100,
        }
        decision.update(overrides)
        return decision

    def test_60pct_100shares_shrinks_to_25pct_and_fewer_shares(self, guard):
        """60%・100株の判断は25%に縮み、株数もその25%相当まで縮む。"""
        decision = self._decision()
        result = guard.check(decision, {"daily_pnl": 0}, price=1000.0, equity=100000.0)

        assert result.allowed is True
        assert decision["position_size_pct"] == 25.0
        # 25% of $100,000 at $1,000/share = 25 shares
        assert decision["quantity"] == 25
        assert any(a["field"] == "quantity" and a["adjusted"] == 25 for a in result.adjustments)

    def test_requested_quantity_never_increased(self, guard):
        """max_qty が大きくても、AIが元々要求した株数より増やしはしない。"""
        decision = self._decision(position_size_pct=10, quantity=5)
        result = guard.check(decision, {"daily_pnl": 0}, price=100.0, equity=100000.0)

        assert result.allowed is True
        assert decision["quantity"] == 5
        assert not any(a["field"] == "quantity" for a in result.adjustments)

    def test_no_price_blocks_buy(self, guard):
        decision = self._decision()
        result = guard.check(decision, {"daily_pnl": 0}, price=None, equity=100000.0)
        assert result.allowed is False
        assert result.reason == "no_price"

    def test_zero_price_blocks_buy(self, guard):
        decision = self._decision()
        result = guard.check(decision, {"daily_pnl": 0}, price=0, equity=100000.0)
        assert result.allowed is False
        assert result.reason == "no_price"

    def test_missing_equity_blocks_buy(self, guard):
        decision = self._decision()
        result = guard.check(decision, {"daily_pnl": 0}, price=1000.0, equity=None)
        assert result.allowed is False
        assert result.reason == "no_price"

    def test_quantity_rounding_to_zero_blocks(self, guard):
        """小さすぎる equity/position_size_pct では株数が0に丸まり、buy自体を拒否する。"""
        decision = self._decision(position_size_pct=1, quantity=5)
        result = guard.check(decision, {"daily_pnl": 0}, price=10000.0, equity=100.0)
        assert result.allowed is False
        assert result.reason == "quantity_rounds_to_zero"

    def test_sell_is_not_sized_by_price(self, guard):
        """sell はポジションサイズによる株数計算の対象外（decision_schema が保有数上限を見る）。"""
        decision = {"action": "sell", "confidence": 80, "quantity": 10}
        result = guard.check(decision, {"daily_pnl": 0})
        assert result.allowed is True
        assert decision["quantity"] == 10


class TestTotalPositionRatioCap:
    """保有合計 + 新規発注が総資産の上限(max_position_pct)を超えないか(旧 check_order 相当)。"""

    @pytest.fixture
    def guard(self):
        with patch.dict(os.environ, {
            "MAX_DAILY_LOSS": "500", "MAX_POSITION_RATIO": "0.5",
            "MAX_SINGLE_TRADE_PCT": "0.25", "MIN_CONFIDENCE": "40",
        }):
            return RiskGuard()

    def test_within_ratio_allowed(self, guard, empty_positions):
        decision = {
            "action": "buy", "confidence": 80, "stop_loss": 100,
            "position_size_pct": 20, "quantity": 10,
        }
        portfolio = {"daily_pnl": 0, "positions": empty_positions}
        result = guard.check(decision, portfolio, price=350.0, equity=100000.0)
        assert result.allowed is True

    def test_existing_positions_push_total_over_limit(self, guard, large_position):
        """large_position の時価だけで既に総資産の40%。新規発注を足すと50%上限を超える。"""
        decision = {
            "action": "buy", "confidence": 80, "stop_loss": 100,
            "position_size_pct": 25, "quantity": 100,
        }
        portfolio = {"daily_pnl": 0, "positions": large_position}
        result = guard.check(decision, portfolio, price=350.0, equity=100000.0)
        assert result.allowed is False
        assert "position_ratio_exceeds_limit" in result.reason

    def test_sell_ignores_total_ratio(self, guard, large_position):
        decision = {"action": "sell", "confidence": 80, "quantity": 10}
        portfolio = {"daily_pnl": 0, "positions": large_position}
        result = guard.check(decision, portfolio)
        assert result.allowed is True

    def test_open_buy_order_value_counts_toward_the_limit(self, guard, empty_positions):
        """未約定の買い注文の想定額も総ポジション上限に含める(M2)。別銘柄の
        未約定注文だけで、新規発注と合わせて上限を超えること。"""
        decision = {
            "action": "buy", "confidence": 80, "stop_loss": 100,
            "position_size_pct": 20, "quantity": 10,
        }
        # 別銘柄 (TSLA) への未約定注文で $49,000 相当が既に確保されている。
        portfolio = {
            "daily_pnl": 0,
            "positions": empty_positions,
            "open_buy_order_value": 49000.0,
        }
        # quantity=10 @ price=350 は $3,500 - 単独なら余裕で上限内だが、
        # $49,000 の未約定注文と合わせると $52,500 > 50% of $100,000。
        result = guard.check(decision, portfolio, price=350.0, equity=100000.0)
        assert result.allowed is False
        assert "position_ratio_exceeds_limit" in result.reason

    def test_open_buy_order_value_defaults_to_zero(self, guard, empty_positions):
        """portfolio に open_buy_order_value が無くても従来どおり動く。"""
        decision = {
            "action": "buy", "confidence": 80, "stop_loss": 100,
            "position_size_pct": 20, "quantity": 10,
        }
        portfolio = {"daily_pnl": 0, "positions": empty_positions}
        result = guard.check(decision, portfolio, price=350.0, equity=100000.0)
        assert result.allowed is True


class TestEffectiveBuyPrice:
    """買いの株数・総ポジション額の計算に使う実効価格 (H1): 成行は現在値、
    指値は max(現在値, limit_price) - 指値が現在値より高くても、その価格で
    約定し得る前提でサイズを決める（低い方を使うと exposure を過小評価する）。"""

    @pytest.fixture
    def guard(self):
        with patch.dict(os.environ, {
            "MAX_DAILY_LOSS": "500", "MAX_POSITION_RATIO": "0.5",
            "MAX_SINGLE_TRADE_PCT": "0.25", "MIN_CONFIDENCE": "40",
        }):
            return RiskGuard()

    def test_market_order_uses_current_price(self, guard):
        from risk_guard import effective_buy_price
        assert effective_buy_price(100.0, "market", None) == 100.0
        assert effective_buy_price(100.0, "market", 500.0) == 100.0  # market には limit_price が無関係

    def test_limit_above_current_price_uses_limit_price(self, guard):
        from risk_guard import effective_buy_price
        assert effective_buy_price(100.0, "limit", 500.0) == 500.0

    def test_limit_below_current_price_uses_current_price(self, guard):
        from risk_guard import effective_buy_price
        assert effective_buy_price(100.0, "limit", 90.0) == 100.0

    def test_limit_order_far_above_current_price_is_capped_by_the_high_price(self, guard):
        """現在値 $100・指値 $500 のケース: 実効価格に指値 $500 を使うので、
        最悪 $500 で約定しても総ポジション上限(50%)・単一トレード上限(25%)を
        超えない株数まで自動的に縮む。"""
        decision = {
            "action": "buy", "confidence": 80, "stop_loss": 90,
            "order_type": "limit", "limit_price": 500.0,
            "position_size_pct": 25, "quantity": 100,
        }
        portfolio = {"daily_pnl": 0, "positions": []}
        result = guard.check(decision, portfolio, price=100.0, equity=10000.0)

        assert result.allowed is True
        # 25% of $10,000 = $2,500; at the effective price of $500 that's 5 shares.
        # (100 が price=$100 で計算されていたら 25 株になってしまい、$500 で
        # 約定すれば $12,500 と上限を大幅に超えていた。)
        assert decision["quantity"] == 5
        worst_case_exposure = decision["quantity"] * 500.0
        assert worst_case_exposure <= 10000.0 * 0.25

    def test_limit_order_total_ratio_uses_effective_price(self, guard, large_position):
        """total position ratio (Stage 7) も実効価格を使う。price のままでは
        見逃してしまう超過を、指値の実効価格を使うことで検出できることを確認する。"""
        decision = {
            "action": "buy", "confidence": 80, "stop_loss": 90,
            "order_type": "limit", "limit_price": 500.0,
            "position_size_pct": 25, "quantity": 25,
        }
        portfolio = {"daily_pnl": 0, "positions": large_position}
        # large_position の時価が $40,000 (40% of equity)。実効価格 ($500) で
        # 25株買うと +$12,500 = 52.5% > 50%上限 → ブロックされるはず。
        # price=$100 のまま計算していたら +$2,500 = 42.5% で通ってしまっていた
        # （H1 で直したバグそのもの）。
        result = guard.check(decision, portfolio, price=100.0, equity=100000.0)
        assert result.allowed is False
        assert "position_ratio_exceeds_limit" in result.reason


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
