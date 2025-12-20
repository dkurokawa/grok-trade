"""Risk Guard - 損失上限・ポジション制限チェック"""
import os
from dataclasses import dataclass
from typing import Optional


@dataclass
class RiskCheckResult:
    allowed: bool
    reason: Optional[str] = None


class RiskGuard:
    def __init__(self):
        self.max_daily_loss = float(os.getenv("MAX_DAILY_LOSS", "500"))
        self.max_position_ratio = float(os.getenv("MAX_POSITION_RATIO", "0.5"))

    def check_order(
        self,
        action: str,
        symbol: str,
        quantity: int,
        price: float,
        account_balance: float,
        current_positions: list[dict],
        daily_pnl: float
    ) -> RiskCheckResult:
        """
        注文前のリスクチェック

        Returns:
            RiskCheckResult(allowed=True/False, reason="...")
        """
        # 1. 日次損失上限チェック
        if daily_pnl <= -self.max_daily_loss:
            return RiskCheckResult(
                allowed=False,
                reason=f"Daily loss limit reached: ${daily_pnl:.2f} (limit: -${self.max_daily_loss})"
            )

        # 2. Hold は常に許可
        if action == "hold":
            return RiskCheckResult(allowed=True)

        # 3. Sell は常に許可（ポジション解消は安全）
        if action == "sell":
            return RiskCheckResult(allowed=True)

        # 4. Buy の場合：ポジション比率チェック
        if action == "buy":
            order_value = quantity * price
            total_position_value = sum(
                p.get("market_value", 0) for p in current_positions
            )
            new_total = total_position_value + order_value

            # ポートフォリオ全体に対する比率
            portfolio_value = account_balance + total_position_value
            if portfolio_value > 0:
                new_ratio = new_total / portfolio_value
                if new_ratio > self.max_position_ratio:
                    return RiskCheckResult(
                        allowed=False,
                        reason=f"Position ratio would exceed limit: {new_ratio:.1%} > {self.max_position_ratio:.1%}"
                    )

            # 残高チェック
            if order_value > account_balance:
                return RiskCheckResult(
                    allowed=False,
                    reason=f"Insufficient balance: need ${order_value:.2f}, have ${account_balance:.2f}"
                )

        return RiskCheckResult(allowed=True)

    def check_system_health(
        self,
        daily_pnl: float,
        error_count: int = 0
    ) -> RiskCheckResult:
        """
        システム全体の健全性チェック

        Returns:
            RiskCheckResult - Falseならシステム停止すべき
        """
        # 日次損失上限
        if daily_pnl <= -self.max_daily_loss:
            return RiskCheckResult(
                allowed=False,
                reason=f"Daily loss limit reached: ${daily_pnl:.2f}"
            )

        # エラー多発
        if error_count >= 5:
            return RiskCheckResult(
                allowed=False,
                reason=f"Too many errors: {error_count}"
            )

        return RiskCheckResult(allowed=True)


# テスト用
if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()

    guard = RiskGuard()

    # テスト1: 正常な買い注文
    result = guard.check_order(
        action="buy",
        symbol="MSTR",
        quantity=10,
        price=350,
        account_balance=100000,
        current_positions=[],
        daily_pnl=0
    )
    print(f"Test 1 (normal buy): {result}")

    # テスト2: 損失上限到達
    result = guard.check_order(
        action="buy",
        symbol="MSTR",
        quantity=10,
        price=350,
        account_balance=100000,
        current_positions=[],
        daily_pnl=-600
    )
    print(f"Test 2 (loss limit): {result}")

    # テスト3: ポジション比率超過
    result = guard.check_order(
        action="buy",
        symbol="MSTR",
        quantity=200,
        price=350,  # $70,000 = 70% of portfolio
        account_balance=100000,
        current_positions=[],
        daily_pnl=0
    )
    print(f"Test 3 (position limit): {result}")
