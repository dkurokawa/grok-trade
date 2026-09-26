"""Trader - Alpaca取引実行モジュール"""
import os
from datetime import datetime, timedelta
from typing import Any

from alpaca.data.enums import DataFeed
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderClass, OrderSide, TimeInForce
from alpaca.trading.requests import (
    LimitOrderRequest,
    MarketOrderRequest,
    OrderRequest,
    StopLossRequest,
    TakeProfitRequest,
)


class DuplicateOrderError(Exception):
    """Alpaca rejected the order because its client_order_id was already used.

    Not a failure: it means some invocation (very likely a retried Lambda
    invocation of the same trading cycle, since client_order_id is derived
    from the cycle's DynamoDB-locked time slot - see
    trading_core.trading_cycle()) already placed this exact order. Callers
    should treat this as "already submitted", not alert on it.
    """


class Trader:
    def __init__(self):
        api_key = os.getenv("ALPACA_API_KEY")
        secret_key = os.getenv("ALPACA_SECRET_KEY")
        paper = os.getenv("ALPACA_PAPER", "true").lower() == "true"

        self.trading_client = TradingClient(api_key, secret_key, paper=paper)
        self.data_client = StockHistoricalDataClient(api_key, secret_key)

        # Alpaca's free (Basic) plan only serves the IEX feed; requesting the
        # default SIP feed fails with "subscription does not permit querying
        # recent SIP data". Override with ALPACA_DATA_FEED=sip on a paid plan.
        self.data_feed = DataFeed(os.getenv("ALPACA_DATA_FEED", "iex").lower())

    def get_account(self) -> dict:
        """アカウント情報取得"""
        account = self.trading_client.get_account()
        return {
            "cash": float(account.cash),
            "portfolio_value": float(account.portfolio_value),
            "buying_power": float(account.buying_power),
            "equity": float(account.equity),
            "last_equity": float(account.last_equity),
            "daily_pnl": float(account.equity) - float(account.last_equity),
        }

    def get_open_buy_order_symbols(self) -> set:
        """未約定の「買い」注文が出ている銘柄。

        閉場中の注文は約定するまで買付余力を押さえ続けるため、同じ銘柄に重ねて
        発注しても Alpaca に弾かれるだけになる。ブラケット注文の損切り・利確は
        売り注文として残り続けるので、買いだけを対象にする。
        """
        try:
            return {
                o.symbol for o in self.trading_client.get_orders()
                if o.side == OrderSide.BUY
            }
        except Exception as e:
            print(f"[Trader] Open orders error: {e}")
            return set()

    def get_positions(self) -> list[dict]:
        """現在のポジション取得"""
        positions = self.trading_client.get_all_positions()
        return [
            {
                "symbol": p.symbol,
                "qty": float(p.qty),
                "avg_entry_price": float(p.avg_entry_price),
                "market_value": float(p.market_value),
                "unrealized_pl": float(p.unrealized_pl),
                "unrealized_plpc": float(p.unrealized_plpc),
            }
            for p in positions
        ]

    def get_market_data(self, symbols: list[str], days: int = 5) -> dict:
        """市場データ取得（直近N日）"""
        end = datetime.now()
        start = end - timedelta(days=days + 3)  # 週末考慮

        request = StockBarsRequest(
            symbol_or_symbols=symbols,
            timeframe=TimeFrame.Day,
            start=start,
            end=end,
            feed=self.data_feed,
        )

        try:
            bars = self.data_client.get_stock_bars(request)
            result = {}

            for symbol in symbols:
                if symbol in bars.data:
                    symbol_bars = bars.data[symbol]
                    if len(symbol_bars) >= 2:
                        latest = symbol_bars[-1]
                        oldest = symbol_bars[0]
                        change = ((latest.close - oldest.close) / oldest.close) * 100
                        result[symbol] = {
                            "price": latest.close,
                            "change_5d": f"{change:+.1f}%",
                            "volume": latest.volume,
                        }

            return result
        except Exception as e:
            print(f"[Trader] Market data error: {e}")
            return {}

    def execute_order(
        self,
        symbol: str,
        action: str,
        quantity: int,
        order_type: str = "market",
        limit_price: float | None = None,
        stop_loss: float | None = None,
        take_profit: float | None = None,
        client_order_id: str | None = None,
    ) -> dict | None:
        """注文実行（stop_loss / take_profit 対応）

        client_order_id を渡すと Alpaca 側でも同じ ID の重複発注を拒否させられる
        （二重発注対策の二段目。一段目は trading_core の DynamoDB ロック）。
        重複を検知したら None を返さず DuplicateOrderError を送出する。
        None は「発注失敗」として trading_core が失敗通知を出すが、重複は
        失敗ではない（既に発注済みという意味）ため区別する。
        """
        if action not in ["buy", "sell"]:
            print(f"[Trader] Invalid action: {action}")
            return None

        if quantity <= 0:
            print(f"[Trader] Invalid quantity: {quantity}")
            return None

        side = OrderSide.BUY if action == "buy" else OrderSide.SELL

        # 損切り・利確は親注文に紐付けて出す（買いのみ）。
        # 以前は別々の売り注文として出していたが、同じ株数を2つの売り注文が
        # 取り合うため2つ目が拒否され、親注文が未約定の間は保有株も無い。
        # ブラケット/OTO なら約定後に有効化され、片方が約定すればもう片方は取消される。
        # StopLossRequest / TakeProfitRequest / OrderClass are deliberately
        # mixed in one dict (unpacked as **protective below), so it's typed
        # loosely rather than narrowed to whichever key happens to be set first.
        protective: dict[str, Any] = {}
        if action == "buy":
            if stop_loss:
                protective["stop_loss"] = StopLossRequest(stop_price=round(float(stop_loss), 2))
            if take_profit:
                protective["take_profit"] = TakeProfitRequest(limit_price=round(float(take_profit), 2))
        if len(protective) == 2:
            protective["order_class"] = OrderClass.BRACKET
        elif protective:
            protective["order_class"] = OrderClass.OTO

        try:
            order_request: OrderRequest
            if order_type == "limit" and limit_price:
                order_request = LimitOrderRequest(
                    symbol=symbol,
                    qty=quantity,
                    side=side,
                    time_in_force=TimeInForce.GTC,
                    limit_price=limit_price,
                    client_order_id=client_order_id,
                    **protective,
                )
            else:
                order_request = MarketOrderRequest(
                    symbol=symbol,
                    qty=quantity,
                    side=side,
                    time_in_force=TimeInForce.GTC,
                    client_order_id=client_order_id,
                    **protective,
                )

            order = self.trading_client.submit_order(order_request)

            result = {
                "order_id": str(order.id),
                "symbol": order.symbol,
                "side": order.side.value,
                "qty": float(order.qty),
                "type": order.type.value,
                "status": order.status.value,
                "submitted_at": str(order.submitted_at),
            }
            if protective:
                print(f"[Trader] {protective['order_class'].value} order: "
                      f"stop_loss={stop_loss} take_profit={take_profit}")

            return result

        except Exception as e:
            message = str(e).lower()
            if client_order_id and "client_order_id" in message and (
                "already" in message or "duplicate" in message or "exists" in message
            ):
                raise DuplicateOrderError(
                    f"Order for {symbol} already submitted (client_order_id={client_order_id})"
                ) from e
            print(f"[Trader] Order error: {e}")
            return None

    def execute_emergency_liquidation(self) -> list[dict]:
        """緊急全ポジション清算

        ブラケット注文の損切り・利確が保有株を押さえているため、先に未約定注文を
        取り消してから清算する（取り消さないと売り注文が拒否される）。
        """
        results = []
        try:
            for r in self.trading_client.close_all_positions(cancel_orders=True):
                results.append({"symbol": r.symbol, "status": r.status})
                print(f"[Trader] Emergency close: {r.symbol} (status {r.status})")
        except Exception as e:
            print(f"[Trader] Emergency liquidation error: {e}")
        return results

    def get_order_status(self, order_id: str) -> dict | None:
        """注文ステータス確認"""
        try:
            order = self.trading_client.get_order_by_id(order_id)
            return {
                "order_id": str(order.id),
                "status": order.status.value,
                "filled_qty": float(order.filled_qty) if order.filled_qty else 0,
                "filled_avg_price": float(order.filled_avg_price) if order.filled_avg_price else None,
            }
        except Exception as e:
            print(f"[Trader] Order status error: {e}")
            return None
