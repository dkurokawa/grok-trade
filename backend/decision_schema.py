"""AI output validation - Stage 2 (Grok/Opus どちらの経路も) の判断を額面通り
信じず、必ずこのスキーマで検証してから Risk Guard に渡す。

RiskGuard.check() はルールベースの安全弁だが、そもそも AI が存在しないティッカーを
挙げたり、保有していない銘柄を売ろうとしたり、現在値を無視した損切り価格を出したり
すれば、check() 自身の前提が壊れる。ここで弾くのはそのクラスの不正な入力で、
検証に失敗した判断は例外を投げず hold に倒す（取引はしない）。
"""
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError, ValidationInfo, model_validator


class TradeDecision(BaseModel):
    """Stage 2 の判断が満たすべき形。model_validate(data, context=...) で検証する。"""

    action: Literal["buy", "sell", "hold"]
    symbol: str | None = None
    quantity: int = 0
    order_type: Literal["market", "limit"] = "market"
    limit_price: float | None = None
    stop_loss: float | None = None
    take_profit: float | None = None
    position_size_pct: float = Field(default=0, ge=0, le=100)
    confidence: int = Field(default=0, ge=0, le=100)
    reasoning: str = ""
    risk_assessment: str = "medium"
    adjustments: list = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_action_specific_rules(self, info: ValidationInfo) -> "TradeDecision":
        if self.action == "hold":
            return self

        context = info.context or {}
        watchlist: list = context.get("watchlist") or []
        held_symbols: dict = context.get("held_symbols") or {}
        current_price = context.get("current_price")

        if not self.symbol:
            raise ValueError("symbol is required for buy/sell")

        if self.action == "buy" and watchlist and self.symbol not in watchlist:
            raise ValueError(f"symbol {self.symbol!r} is not in the watchlist")

        if self.action == "sell" and self.symbol not in held_symbols:
            raise ValueError(f"symbol {self.symbol!r} is not held, cannot sell")

        if self.quantity < 1:
            raise ValueError(f"quantity must be >= 1 for {self.action}, got {self.quantity}")

        if self.action == "sell":
            held_qty = held_symbols.get(self.symbol, 0)
            if self.quantity > held_qty:
                raise ValueError(
                    f"quantity {self.quantity} exceeds held quantity {held_qty} for {self.symbol!r}"
                )

        if self.order_type == "limit" and not (self.limit_price and self.limit_price > 0):
            raise ValueError("limit_price must be > 0 for a limit order")

        if self.action == "buy":
            # 現在値を知らなければ stop_loss/take_profit の妥当性を確認できない
            # ため、価格不明を「安全」側ではなく「検証失敗」側に倒す。
            if not current_price or current_price <= 0:
                raise ValueError("current_price is required to validate a buy's stop_loss/take_profit")

            limit_price = self.limit_price if self.order_type == "limit" else None

            # limit_price 自体の妥当性 (±10% バンド) を先に見る。バンド外の
            # limit_price を reference_price に使って stop_loss/take_profit を
            # 検証しても意味がない（現在値からかけ離れた基準で判定してしまう）。
            if limit_price is not None:
                lower_bound = current_price * 0.9
                upper_bound = current_price * 1.1
                if not (lower_bound <= limit_price <= upper_bound):
                    raise ValueError(
                        f"limit_price {limit_price} is outside ±10% of "
                        f"current price ({current_price})"
                    )

            # stop_loss/take_profit の基準は「実際に買うつもりの価格」(E8)。
            # 指値なら limit_price - 指値90・損切り95は、現在値100を基準にすると
            # 見逃すが、実際に90で約定したときは損切りの方が高値という矛盾に
            # なる。成行は現在値がそのまま買うつもりの価格。
            reference_price = limit_price if limit_price is not None else current_price

            if not (self.stop_loss and 0 < self.stop_loss < reference_price):
                raise ValueError(
                    f"stop_loss must be > 0 and < reference price ({reference_price}) for buy, "
                    f"got {self.stop_loss}"
                )
            if self.take_profit is not None and self.take_profit <= reference_price:
                raise ValueError(
                    f"take_profit must be > reference price ({reference_price}) for buy, "
                    f"got {self.take_profit}"
                )

        return self


def _hold_fallback() -> dict:
    return TradeDecision(action="hold").model_dump()


def validate_decision(
    decision: dict[str, Any],
    watchlist: list,
    positions: list,
    current_price: float | None,
) -> tuple[dict, str | None]:
    """Stage 2 の生の判断を検証する純関数。

    Args:
        decision: Grok/Opus の decide()/analyze() が返した dict。
        watchlist: 監視銘柄リスト（buy の symbol 検証に使う）。
        positions: trader.get_positions() の戻り値（sell の保有数検証に使う）。
        current_price: decision["symbol"] の現在値（buy の stop_loss/take_profit 検証に使う）。

    Returns:
        (safe_decision, invalid_reason) — 検証に通れば invalid_reason は None、
        通らなければ safe_decision は hold に強制された判断で、invalid_reason に
        理由文字列が入る。例外は投げない。
    """
    held_symbols = {p["symbol"]: p.get("qty", 0) for p in positions if "symbol" in p}
    try:
        validated = TradeDecision.model_validate(
            decision,
            context={
                "watchlist": watchlist,
                "held_symbols": held_symbols,
                "current_price": current_price,
            },
        )
    except ValidationError as e:
        return _hold_fallback(), str(e)
    return validated.model_dump(), None
