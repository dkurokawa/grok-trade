"""decision_schema.validate_decision - each violation must force hold, never raise."""
from decision_schema import validate_decision

WATCHLIST = ["MSTR", "TSLA", "QQQ", "SPY"]


def _buy(**overrides):
    decision = {
        "action": "buy", "symbol": "MSTR", "quantity": 10,
        "order_type": "market", "limit_price": None,
        "stop_loss": 330.0, "take_profit": 400.0,
        "position_size_pct": 20, "confidence": 80,
        "reasoning": "test", "risk_assessment": "medium", "adjustments": [],
    }
    decision.update(overrides)
    return decision


def _sell(**overrides):
    decision = {
        "action": "sell", "symbol": "MSTR", "quantity": 5,
        "order_type": "market", "limit_price": None,
        "stop_loss": None, "take_profit": None,
        "position_size_pct": 0, "confidence": 80,
        "reasoning": "test", "risk_assessment": "medium", "adjustments": [],
    }
    decision.update(overrides)
    return decision


HELD_MSTR = [{"symbol": "MSTR", "qty": 10}]


class TestValidBuySell:
    def test_valid_buy_passes_through(self):
        decision, reason = validate_decision(_buy(), WATCHLIST, [], current_price=350.0)
        assert reason is None
        assert decision["action"] == "buy"
        assert decision["symbol"] == "MSTR"
        assert decision["quantity"] == 10

    def test_valid_sell_passes_through(self):
        decision, reason = validate_decision(_sell(), WATCHLIST, HELD_MSTR, current_price=350.0)
        assert reason is None
        assert decision["action"] == "sell"

    def test_hold_needs_no_context(self):
        decision, reason = validate_decision({"action": "hold", "confidence": 10}, WATCHLIST, [], None)
        assert reason is None
        assert decision["action"] == "hold"


class TestActionAndOrderType:
    def test_invalid_action_forces_hold(self):
        decision, reason = validate_decision(_buy(action="short"), WATCHLIST, [], 350.0)
        assert decision["action"] == "hold"
        assert reason is not None

    def test_invalid_order_type_forces_hold(self):
        decision, reason = validate_decision(_buy(order_type="stop"), WATCHLIST, [], 350.0)
        assert decision["action"] == "hold"
        assert reason is not None

    def test_limit_without_limit_price_forces_hold(self):
        decision, reason = validate_decision(
            _buy(order_type="limit", limit_price=None), WATCHLIST, [], 350.0
        )
        assert decision["action"] == "hold"
        assert "limit_price" in reason

    def test_limit_with_zero_limit_price_forces_hold(self):
        decision, reason = validate_decision(
            _buy(order_type="limit", limit_price=0), WATCHLIST, [], 350.0
        )
        assert decision["action"] == "hold"

    def test_limit_with_positive_limit_price_passes(self):
        decision, reason = validate_decision(
            _buy(order_type="limit", limit_price=345.0), WATCHLIST, [], 350.0
        )
        assert reason is None
        assert decision["action"] == "buy"


class TestBuyLimitPriceBand:
    """buy の limit_price は現在値の ±10% を外れたら hold (H1)。

    current_price=100.0 に合わせて stop_loss/take_profit も張り直す
    （_buy() のデフォルトは current_price=350 前提の値のため）。
    """

    def _buy_at_100(self, **overrides):
        return _buy(stop_loss=90.0, take_profit=120.0, **overrides)

    def test_within_10pct_above_passes(self):
        decision, reason = validate_decision(
            self._buy_at_100(order_type="limit", limit_price=110.0), WATCHLIST, [], 100.0
        )
        assert reason is None
        assert decision["action"] == "buy"

    def test_within_10pct_below_passes(self):
        decision, reason = validate_decision(
            self._buy_at_100(order_type="limit", limit_price=95.0), WATCHLIST, [], 100.0
        )
        assert reason is None
        assert decision["action"] == "buy"

    def test_exactly_10pct_above_passes(self):
        """境界ちょうど(+10%)は許可する。"""
        decision, reason = validate_decision(
            self._buy_at_100(order_type="limit", limit_price=110.0), WATCHLIST, [], 100.0
        )
        assert reason is None

    def test_exactly_10pct_below_passes(self):
        """境界ちょうど(-10%)は許可する。"""
        decision, reason = validate_decision(
            self._buy_at_100(order_type="limit", limit_price=90.0), WATCHLIST, [], 100.0
        )
        assert reason is None

    def test_just_above_10pct_forces_hold(self):
        decision, reason = validate_decision(
            self._buy_at_100(order_type="limit", limit_price=110.01), WATCHLIST, [], 100.0
        )
        assert decision["action"] == "hold"
        assert "±10%" in reason

    def test_just_below_10pct_forces_hold(self):
        decision, reason = validate_decision(
            self._buy_at_100(order_type="limit", limit_price=89.99), WATCHLIST, [], 100.0
        )
        assert decision["action"] == "hold"
        assert "±10%" in reason

    def test_wildly_above_current_price_forces_hold(self):
        """現在値 $100・指値 $500 のケース。"""
        decision, reason = validate_decision(
            self._buy_at_100(order_type="limit", limit_price=500.0), WATCHLIST, [], 100.0
        )
        assert decision["action"] == "hold"
        assert "±10%" in reason

    def test_market_order_is_not_subject_to_the_band(self):
        """成行注文には limit_price 自体が無いので、このチェックの対象外。"""
        decision, reason = validate_decision(
            self._buy_at_100(order_type="market", limit_price=None), WATCHLIST, [], 100.0
        )
        assert reason is None
        assert decision["action"] == "buy"

    def test_sell_is_not_subject_to_the_band(self):
        decision, reason = validate_decision(
            _sell(order_type="limit", limit_price=500.0), WATCHLIST,
            [{"symbol": "MSTR", "qty": 10}], current_price=100.0,
        )
        assert reason is None
        assert decision["action"] == "sell"


class TestSymbolRules:
    def test_buy_outside_watchlist_forces_hold(self):
        decision, reason = validate_decision(_buy(symbol="NVDA"), WATCHLIST, [], 350.0)
        assert decision["action"] == "hold"
        assert "watchlist" in reason

    def test_sell_of_unheld_symbol_forces_hold(self):
        decision, reason = validate_decision(_sell(), WATCHLIST, [], current_price=350.0)
        assert decision["action"] == "hold"
        assert "not held" in reason

    def test_buy_missing_symbol_forces_hold(self):
        decision, reason = validate_decision(_buy(symbol=None), WATCHLIST, [], 350.0)
        assert decision["action"] == "hold"

    def test_empty_watchlist_skips_the_membership_check(self):
        """An empty watchlist arg means "no restriction configured", not
        "everything is rejected"."""
        decision, reason = validate_decision(_buy(symbol="NVDA"), [], [], 350.0)
        assert reason is None
        assert decision["action"] == "buy"


class TestQuantityRules:
    def test_zero_quantity_buy_forces_hold(self):
        decision, reason = validate_decision(_buy(quantity=0), WATCHLIST, [], 350.0)
        assert decision["action"] == "hold"
        assert "quantity" in reason

    def test_negative_quantity_buy_forces_hold(self):
        decision, reason = validate_decision(_buy(quantity=-5), WATCHLIST, [], 350.0)
        assert decision["action"] == "hold"

    def test_sell_quantity_exceeding_holdings_forces_hold(self):
        decision, reason = validate_decision(
            _sell(quantity=999), WATCHLIST, HELD_MSTR, current_price=350.0
        )
        assert decision["action"] == "hold"
        assert "exceeds held" in reason

    def test_sell_quantity_equal_to_holdings_passes(self):
        decision, reason = validate_decision(
            _sell(quantity=10), WATCHLIST, HELD_MSTR, current_price=350.0
        )
        assert reason is None
        assert decision["action"] == "sell"


class TestConfidenceAndPositionSizeRange:
    def test_confidence_above_100_forces_hold(self):
        decision, reason = validate_decision(_buy(confidence=150), WATCHLIST, [], 350.0)
        assert decision["action"] == "hold"

    def test_confidence_negative_forces_hold(self):
        decision, reason = validate_decision(_buy(confidence=-1), WATCHLIST, [], 350.0)
        assert decision["action"] == "hold"

    def test_position_size_pct_above_100_forces_hold(self):
        decision, reason = validate_decision(_buy(position_size_pct=150), WATCHLIST, [], 350.0)
        assert decision["action"] == "hold"

    def test_position_size_pct_negative_forces_hold(self):
        decision, reason = validate_decision(_buy(position_size_pct=-10), WATCHLIST, [], 350.0)
        assert decision["action"] == "hold"


class TestBuyPriceBounds:
    def test_missing_current_price_forces_hold(self):
        decision, reason = validate_decision(_buy(), WATCHLIST, [], current_price=None)
        assert decision["action"] == "hold"
        assert "current_price" in reason

    def test_zero_current_price_forces_hold(self):
        decision, reason = validate_decision(_buy(), WATCHLIST, [], current_price=0)
        assert decision["action"] == "hold"

    def test_missing_stop_loss_forces_hold(self):
        decision, reason = validate_decision(_buy(stop_loss=None), WATCHLIST, [], 350.0)
        assert decision["action"] == "hold"
        assert "stop_loss" in reason

    def test_stop_loss_above_current_price_forces_hold(self):
        decision, reason = validate_decision(_buy(stop_loss=360.0), WATCHLIST, [], current_price=350.0)
        assert decision["action"] == "hold"

    def test_stop_loss_equal_to_current_price_forces_hold(self):
        decision, reason = validate_decision(_buy(stop_loss=350.0), WATCHLIST, [], current_price=350.0)
        assert decision["action"] == "hold"

    def test_stop_loss_zero_forces_hold(self):
        decision, reason = validate_decision(_buy(stop_loss=0), WATCHLIST, [], current_price=350.0)
        assert decision["action"] == "hold"

    def test_take_profit_below_current_price_forces_hold(self):
        decision, reason = validate_decision(_buy(take_profit=340.0), WATCHLIST, [], current_price=350.0)
        assert decision["action"] == "hold"
        assert "take_profit" in reason

    def test_take_profit_equal_to_current_price_forces_hold(self):
        decision, reason = validate_decision(_buy(take_profit=350.0), WATCHLIST, [], current_price=350.0)
        assert decision["action"] == "hold"

    def test_missing_take_profit_is_allowed(self):
        """take_profit is optional; only stop_loss is required for buy."""
        decision, reason = validate_decision(_buy(take_profit=None), WATCHLIST, [], current_price=350.0)
        assert reason is None
        assert decision["action"] == "buy"

    def test_sell_does_not_need_current_price(self):
        decision, reason = validate_decision(_sell(), WATCHLIST, HELD_MSTR, current_price=None)
        assert reason is None
        assert decision["action"] == "sell"
