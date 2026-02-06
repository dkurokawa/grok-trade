"""Risk Guard - 損失上限・ポジション制限・Opus判断の安全弁"""
import os
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class RiskCheckResult:
    allowed: bool
    reason: Optional[str] = None
    adjustments: list[dict] = field(default_factory=list)


class RiskGuard:
    def __init__(self):
        self.max_daily_loss = float(os.getenv("MAX_DAILY_LOSS", "500"))
        self.max_position_pct = float(os.getenv("MAX_POSITION_RATIO", "0.5")) * 100  # 50%
        self.max_single_trade_pct = float(os.getenv("MAX_SINGLE_TRADE_PCT", "0.25")) * 100  # 25%
        self.min_confidence = int(os.getenv("MIN_CONFIDENCE", "40"))

    def check(self, decision: dict, portfolio: dict) -> RiskCheckResult:
        """
        Opus判断をルールベースでチェック。
        adjustments: Opusの提案を安全側に調整した記録。
        """
        adjustments = []

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
            old = pct
            decision["position_size_pct"] = self.max_position_pct
            adjustments.append({
                "field": "position_size_pct",
                "original": old,
                "adjusted": self.max_position_pct,
                "reason": f"Risk Guard: 上限{self.max_position_pct}%に制限",
            })

        # 5. 単一トレード上限
        if pct > self.max_single_trade_pct and decision["position_size_pct"] > self.max_single_trade_pct:
            old = decision["position_size_pct"]
            decision["position_size_pct"] = self.max_single_trade_pct
            adjustments.append({
                "field": "position_size_pct",
                "original": old,
                "adjusted": self.max_single_trade_pct,
                "reason": f"Risk Guard: 単一トレード上限{self.max_single_trade_pct}%に制限",
            })

        return RiskCheckResult(allowed=True, adjustments=adjustments)

    def check_order(
        self,
        action: str,
        symbol: str,
        quantity: int,
        price: float,
        account_balance: float,
        current_positions: list[dict],
        daily_pnl: float,
    ) -> RiskCheckResult:
        """
        既存互換: 注文前のリスクチェック（Grok単体時代のAPI）
        """
        # 日次損失上限
        if daily_pnl <= -self.max_daily_loss:
            return RiskCheckResult(
                allowed=False,
                reason=f"Daily loss limit reached: ${daily_pnl:.2f} (limit: -${self.max_daily_loss})",
            )

        if action == "hold":
            return RiskCheckResult(allowed=True)

        if action == "sell":
            return RiskCheckResult(allowed=True)

        if action == "buy":
            order_value = quantity * price
            total_position_value = sum(
                p.get("market_value", 0) for p in current_positions
            )
            new_total = total_position_value + order_value

            portfolio_value = account_balance + total_position_value
            if portfolio_value > 0:
                new_ratio = new_total / portfolio_value
                max_ratio = self.max_position_pct / 100
                if new_ratio > max_ratio:
                    return RiskCheckResult(
                        allowed=False,
                        reason=f"Position ratio would exceed limit: {new_ratio:.1%} > {max_ratio:.1%}",
                    )

            if order_value > account_balance:
                return RiskCheckResult(
                    allowed=False,
                    reason=f"Insufficient balance: need ${order_value:.2f}, have ${account_balance:.2f}",
                )

        return RiskCheckResult(allowed=True)

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
