"""Risk Guard - 損失上限・ポジション制限・Opus判断の安全弁"""
import math
import os
from dataclasses import dataclass, field


@dataclass
class RiskCheckResult:
    allowed: bool
    reason: str | None = None
    adjustments: list[dict] = field(default_factory=list)


def effective_buy_price(price: float, order_type: str, limit_price: float | None) -> float:
    """買いの株数・総ポジション額の計算に使う「実効価格」。

    成行なら現在値。指値なら max(現在値, limit_price) - 指値が現在値より高ければ
    その価格で約定し得るのでその価格を、現在値より低ければ現在値のほうが保守的
    （高い方が exposure を大きく見積もる）なのでそちらを使う。指値が現在値より
    低くても現在値を下限にすることで、株数上限・総ポジション上限を過小評価しない。
    """
    if order_type == "limit" and limit_price and limit_price > 0:
        return max(price, limit_price)
    return price


class RiskGuard:
    def __init__(self) -> None:
        self.max_daily_loss = float(os.getenv("MAX_DAILY_LOSS", "500"))
        self.max_position_pct = float(os.getenv("MAX_POSITION_RATIO", "0.5")) * 100  # 50%
        self.max_single_trade_pct = float(os.getenv("MAX_SINGLE_TRADE_PCT", "0.25")) * 100  # 25%
        self.min_confidence = int(os.getenv("MIN_CONFIDENCE", "40"))

    def check(
        self,
        decision: dict,
        portfolio: dict,
        price: float | None = None,
        equity: float | None = None,
    ) -> RiskCheckResult:
        """
        Opus/Grok の判断をルールベースでチェックする唯一の入口。

        portfolio: {"daily_pnl": float, "positions": list[dict] (optional)}
        price / equity: buy のときだけ使う。position_size_pct から株数を割り出し、
        保有中ポジション + 新規発注が総資産の上限を超えないかも合わせて見る
        （旧 check_order() が別に行っていたチェックをここに統合した）。
        adjustments: 判断を安全側に調整した記録。
        """
        adjustments: list[dict] = []

        # 1. 日次損失上限
        if portfolio.get("daily_pnl", 0) <= -self.max_daily_loss:
            return RiskCheckResult(
                allowed=False,
                reason="daily_loss_limit_reached",
            )

        action = decision.get("action", "hold")
        if action == "hold":
            return RiskCheckResult(allowed=True)

        # 2. confidence 閾値
        confidence = decision.get("confidence", 0)
        if confidence < self.min_confidence:
            return RiskCheckResult(
                allowed=False,
                reason=f"confidence_too_low: {confidence}",
            )

        # 3. ストップロス必須（buy のみ。sell は既存ポジション決済なので不要）
        if action == "buy" and not decision.get("stop_loss"):
            return RiskCheckResult(
                allowed=False,
                reason="stop_loss_required",
            )

        # 4. ポジションサイズ上限
        pct = decision.get("position_size_pct", 0)
        if pct > self.max_position_pct:
            adjustments.append({
                "field": "position_size_pct",
                "original": pct,
                "adjusted": self.max_position_pct,
                "reason": f"Risk Guard: 上限{self.max_position_pct}%に制限",
            })
            pct = self.max_position_pct

        # 5. 単一トレード上限
        if pct > self.max_single_trade_pct:
            adjustments.append({
                "field": "position_size_pct",
                "original": pct,
                "adjusted": self.max_single_trade_pct,
                "reason": f"Risk Guard: 単一トレード上限{self.max_single_trade_pct}%に制限",
            })
            pct = self.max_single_trade_pct

        decision["position_size_pct"] = pct

        if action != "buy":
            return RiskCheckResult(allowed=True, adjustments=adjustments)

        # 6. 確定した position_size_pct から株数を計算し直す（buy のみ）。
        # AIの言う"25%"を額面通り信じず、実際の価格・残高から株数を割り出す。
        if not price or price <= 0 or not equity or equity <= 0:
            return RiskCheckResult(allowed=False, reason="no_price", adjustments=adjustments)

        # 指値なら現在値と指値の高い方を使う（低く見積もって株数上限を
        # すり抜けさせない）。成行ならそのまま現在値。
        effective_price = effective_buy_price(
            price, decision.get("order_type", "market"), decision.get("limit_price")
        )

        max_qty = math.floor(equity * pct / 100 / effective_price)
        requested_qty = decision.get("quantity", 0) or 0
        quantity = min(requested_qty, max_qty)
        if quantity < requested_qty:
            adjustments.append({
                "field": "quantity",
                "original": requested_qty,
                "adjusted": quantity,
                "reason": f"Risk Guard: ポジションサイズ{pct}%相当の{max_qty}株に制限",
            })
        decision["quantity"] = quantity

        if quantity == 0:
            return RiskCheckResult(allowed=False, reason="quantity_rounds_to_zero", adjustments=adjustments)

        # 7. 保有合計 + 未約定の買い注文 + 新規発注が総資産の上限を超えないか
        # （旧 check_order() 相当。未約定注文分は M2 で追加 - これを含めないと、
        # 複数サイクルにまたがって未約定の買い注文を積み増すことで上限を
        # すり抜けられる）。
        current_positions = portfolio.get("positions", [])
        open_buy_order_value = portfolio.get("open_buy_order_value", 0)
        order_value = quantity * effective_price
        total_position_value = (
            sum(p.get("market_value", 0) for p in current_positions) + open_buy_order_value
        )
        new_ratio = (total_position_value + order_value) / equity
        max_ratio = self.max_position_pct / 100
        if new_ratio > max_ratio:
            return RiskCheckResult(
                allowed=False,
                reason=f"position_ratio_exceeds_limit: {new_ratio:.1%} > {max_ratio:.1%}",
                adjustments=adjustments,
            )

        return RiskCheckResult(allowed=True, adjustments=adjustments)

    def check_system_health(
        self,
        daily_pnl: float,
        error_count: int = 0,
    ) -> RiskCheckResult:
        """システム全体の健全性チェック"""
        if daily_pnl <= -self.max_daily_loss:
            return RiskCheckResult(
                allowed=False,
                reason=f"Daily loss limit reached: ${daily_pnl:.2f}",
            )

        if error_count >= 5:
            return RiskCheckResult(
                allowed=False,
                reason=f"Too many errors: {error_count}",
            )

        return RiskCheckResult(allowed=True)
