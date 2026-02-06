"""Trader - Alpaca取引実行モジュール"""
import os
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest, LimitOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame
from datetime import datetime, timedelta
from typing import Optional


class Trader:
    def __init__(self):
        api_key = os.getenv("ALPACA_API_KEY")
        secret_key = os.getenv("ALPACA_SECRET_KEY")
        paper = os.getenv("ALPACA_PAPER", "true").lower() == "true"

        self.trading_client = TradingClient(api_key, secret_key, paper=paper)
        self.data_client = StockHistoricalDataClient(api_key, secret_key)

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
        limit_price: Optional[float] = None,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
    ) -> Optional[dict]:
        """注文実行（stop_loss / take_profit 対応）"""
        if action not in ["buy", "sell"]:
            print(f"[Trader] Invalid action: {action}")
            return None

        if quantity <= 0:
            print(f"[Trader] Invalid quantity: {quantity}")
            return None

        side = OrderSide.BUY if action == "buy" else OrderSide.SELL

        try:
            if order_type == "limit" and limit_price:
                order_request = LimitOrderRequest(
                    symbol=symbol,
                    qty=quantity,
                    side=side,
                    time_in_force=TimeInForce.GTC,
                    limit_price=limit_price,
                )
            else:
                order_request = MarketOrderRequest(
                    symbol=symbol,
                    qty=quantity,
                    side=side,
                    time_in_force=TimeInForce.GTC,
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

            # stop_loss / take_profit は別注文で発行（bracket order 簡易版）
            if stop_loss and action == "buy":
                self._submit_stop_loss(symbol, quantity, stop_loss)
            if take_profit and action == "buy":
                self._submit_take_profit(symbol, quantity, take_profit)

            return result

        except Exception as e:
            print(f"[Trader] Order error: {e}")
            return None

    def _submit_stop_loss(self, symbol: str, quantity: int, stop_price: float):
        """ストップロス注文（成行のsell stop）"""
        try:
            from alpaca.trading.requests import StopOrderRequest
            order_request = StopOrderRequest(
                symbol=symbol,
                qty=quantity,
                side=OrderSide.SELL,
                time_in_force=TimeInForce.GTC,
                stop_price=stop_price,
            )
            self.trading_client.submit_order(order_request)
            print(f"[Trader] Stop loss set: {symbol} @ ${stop_price}")
        except Exception as e:
            print(f"[Trader] Stop loss order error: {e}")

    def _submit_take_profit(self, symbol: str, quantity: int, limit_price: float):
        """テイクプロフィット注文（指値のsell limit）"""
        try:
            order_request = LimitOrderRequest(
                symbol=symbol,
                qty=quantity,
                side=OrderSide.SELL,
                time_in_force=TimeInForce.GTC,
                limit_price=limit_price,
            )
            self.trading_client.submit_order(order_request)
            print(f"[Trader] Take profit set: {symbol} @ ${limit_price}")
        except Exception as e:
            print(f"[Trader] Take profit order error: {e}")

    def execute_emergency_liquidation(self) -> list[dict]:
        """緊急全ポジション清算"""
        results = []
        try:
            positions = self.get_positions()
            for pos in positions:
                result = self.execute_order(
                    symbol=pos["symbol"],
                    action="sell",
                    quantity=int(pos["qty"]),
                    order_type="market",
                )
                if result:
                    results.append(result)
                    print(f"[Trader] Emergency sell: {pos['symbol']} x{int(pos['qty'])}")
        except Exception as e:
            print(f"[Trader] Emergency liquidation error: {e}")
        return results

    def get_order_status(self, order_id: str) -> Optional[dict]:
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
