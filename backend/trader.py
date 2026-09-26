"""Trader - Alpaca取引実行モジュール"""

import os
from datetime import datetime, timedelta
from typing import Any

from alpaca.common.exceptions import APIError
from alpaca.data.enums import DataFeed
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderClass, OrderSide, TimeInForce
from alpaca.trading.models import FailedClosePositionDetails
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
    def __init__(self) -> None:
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
        # The SDK's return type is a Union with a raw dict for when the
        # client is constructed with raw_data=True; we never do that, so
        # this always holds at runtime. Narrows the type for mypy.
        assert not isinstance(account, dict)
        # Alpaca's model types these fields as Optional (the API schema allows
        # it), but a real account response always populates them; `or 0`
        # covers a not-really-expected None instead of crashing on it.
        return {
            "cash": float(account.cash or 0),
            "portfolio_value": float(account.portfolio_value or 0),
            "buying_power": float(account.buying_power or 0),
            "equity": float(account.equity or 0),
            "last_equity": float(account.last_equity or 0),
            "daily_pnl": float(account.equity or 0) - float(account.last_equity or 0),
        }

    def is_market_open(self) -> bool:
        """Alpaca の取引カレンダーで現在が開場中か。

        取得失敗は例外として送出する（F8: fail closed - 開場中かどうか
        分からない以上、新規の取引サイクルは動かさない）。
        """
        clock = self.trading_client.get_clock()
        assert not isinstance(clock, dict)  # see get_account()
        return bool(clock.is_open)

    def get_open_buy_orders(self) -> list[dict]:
        """未約定の「買い」注文一覧（symbol・qty・limit_price）。

        総ポジション上限の計算 (RiskGuard.check()) は、これから約定するかも
        しれない未約定の買い注文の想定額も新規発注と同様に「これから保有する
        ことになりうる額」として含める必要がある - でないと、複数サイクルに
        またがって未約定の買い注文を積み増すことで上限をすり抜けられてしまう。

        取得失敗は例外として送出する（E1: fail closed）。以前はここで握り潰して
        空リストを返していたが、それだと「未約定注文は無い」という偽の答えに
        なり、総ポジション上限チェックが実質素通しになってしまっていた。
        呼び出し元 (trading_core) はこの例外を「新規の買いを見送る理由」として
        扱う。売りはこの一覧を使わないので影響しない。
        """
        orders = self.trading_client.get_orders()
        assert not isinstance(orders, dict)  # see get_account()
        return [
            {
                "symbol": o.symbol,
                "qty": float(o.qty) if o.qty else 0.0,
                "limit_price": float(o.limit_price) if o.limit_price else None,
            }
            for o in orders
            if o.side == OrderSide.BUY
        ]

    def get_open_buy_order_symbols(self) -> set:
        """未約定の「買い」注文が出ている銘柄。

        閉場中の注文は約定するまで買付余力を押さえ続けるため、同じ銘柄に重ねて
        発注しても Alpaca に弾かれるだけになる。ブラケット注文の損切り・利確は
        売り注文として残り続けるので、買いだけを対象にする。取得失敗は
        get_open_buy_orders() と同様に例外を送出する。
        """
        return {o["symbol"] for o in self.get_open_buy_orders()}

    def get_positions(self) -> list[dict]:
        """現在のポジション取得"""
        positions = self.trading_client.get_all_positions()
        assert not isinstance(positions, dict)  # see get_account()
        return [
            {
                "symbol": p.symbol,
                "qty": float(p.qty),
                "avg_entry_price": float(p.avg_entry_price),
                "market_value": float(p.market_value or 0),
                "unrealized_pl": float(p.unrealized_pl or 0),
                "unrealized_plpc": float(p.unrealized_plpc or 0),
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
            assert not isinstance(bars, dict)  # see get_account()
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

        client_order_id を渡すと、発注前に get_order_by_client_id() で同じ ID の
        注文が既に存在しないか確認する（二重発注対策の二段目。一段目は
        trading_core の DynamoDB ロック）。既存の注文が見つかったら None を
        返さず DuplicateOrderError を送出する - None は「発注失敗」として
        trading_core が失敗通知を出すが、重複は失敗ではない（既に発注済み
        という意味）ため区別する。

        確認自体（get_order_by_client_id 呼び出し）が 404 以外の理由で失敗
        したら、buy は発注しない（E5・E1 と同じ fail closed: 重複かどうか
        分からない新規の買いは見送る）。sell はそのまま発注する（保有解消は
        fail open: 確認できないからといって手仕舞いを止めない）。

        事前確認が 404 (not_found) だった直後に、別の呼び出しが先に同じ
        client_order_id で発注してしまうレースは残る。その場合 submit_order()
        自体が 422 で拒否するので、その例外を捕まえてもう一度
        get_order_by_client_id() で確認し、見つかれば同じく DuplicateOrderError
        にする（F6。メッセージ文字列には頼らない）。
        """
        if action not in ["buy", "sell"]:
            print(f"[Trader] Invalid action: {action}")
            return None

        if quantity <= 0:
            print(f"[Trader] Invalid quantity: {quantity}")
            return None

        if client_order_id:
            try:
                existing = self.trading_client.get_order_by_client_id(client_order_id)
            except Exception as e:
                not_found = isinstance(e, APIError) and e.status_code == 404
                if not not_found:
                    if action == "buy":
                        print(
                            f"[Trader] Could not confirm duplicate for buy {symbol} "
                            f"(client_order_id={client_order_id}): {e} - skipping buy"
                        )
                        return None
                    print(
                        f"[Trader] Could not confirm duplicate for sell {symbol} "
                        f"(client_order_id={client_order_id}): {e} - proceeding anyway"
                    )
                # 404 (not_found): 既存注文なし、通常どおり発注へ進む。
            else:
                assert not isinstance(existing, dict)  # see get_account()
                raise DuplicateOrderError(
                    f"Order for {symbol} already submitted (client_order_id={client_order_id}, "
                    f"existing order_id={existing.id})"
                )

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
            assert not isinstance(order, dict)  # see get_account()
            # side/type/qty are typed Optional in the SDK (the API schema
            # allows it), but Alpaca always echoes back what we just
            # submitted with an explicit side/type/qty.
            assert order.side is not None
            assert order.type is not None

            result = {
                "order_id": str(order.id),
                "symbol": order.symbol,
                "side": order.side.value,
                "qty": float(order.qty or 0),
                "type": order.type.value,
                "status": order.status.value,
                "submitted_at": str(order.submitted_at),
            }
            if protective:
                print(
                    f"[Trader] {protective['order_class'].value} order: stop_loss={stop_loss} take_profit={take_profit}"
                )

            return result

        except Exception as e:
            # F6: 発注前の事前確認 (get_order_by_client_id, 上) と
            # submit_order() のこの呼び出しの間に別の呼び出しが先に同じ
            # client_order_id で発注しているレースだと、Alpaca は 422 で
            # 拒否する。メッセージ文字列には頼らず、もう一度
            # get_order_by_client_id() で実在を確認できた場合だけ
            # DuplicateOrderError にする（それ以外の 422 や理由不明のエラーは
            # 従来どおり「発注失敗」として None を返す）。
            if client_order_id and isinstance(e, APIError) and e.status_code == 422:
                post_submit_existing = None
                try:
                    post_submit_existing = self.trading_client.get_order_by_client_id(client_order_id)
                except Exception:
                    pass
                if post_submit_existing is not None:
                    assert not isinstance(post_submit_existing, dict)  # see get_account()
                    raise DuplicateOrderError(
                        f"Order for {symbol} already submitted (client_order_id={client_order_id}, "
                        f"existing order_id={post_submit_existing.id})"
                    ) from e

            print(f"[Trader] Order error: {e}")
            return None

    def execute_emergency_liquidation(self) -> list[dict]:
        """緊急全ポジション清算

        ブラケット注文の損切り・利確が保有株を押さえているため、先に未約定注文を
        取り消してから清算する（取り消さないと売り注文が拒否される）。

        銘柄ごとの成否 (`ok`) をそのまま返す。close_all_positions() 自体の
        呼び出しが失敗したら、その例外は握り潰さずそのまま呼び出し元
        (trading_core.emergency_check) に伝える - ドローダウンを検知した後に
        「清算したつもり」で空配列を返すのが最悪のシナリオなので、失敗を
        隠さない。
        """
        closed = self.trading_client.close_all_positions(cancel_orders=True)
        assert not isinstance(closed, dict)  # see get_account()

        results = []
        for r in closed:
            ok = r.status is not None and 200 <= r.status < 300
            entry: dict[str, Any] = {"symbol": r.symbol, "status": r.status, "ok": ok}
            if not ok and isinstance(r.body, FailedClosePositionDetails):
                entry["error"] = r.body.message
            results.append(entry)
            if ok:
                print(f"[Trader] Emergency close: {r.symbol} (status {r.status})")
            else:
                print(
                    f"[Trader] Emergency close FAILED: {r.symbol} (status {r.status}): {entry.get('error', 'unknown')}"
                )
        return results

    def cancel_all_orders(self) -> None:
        """すべての未約定注文を取り消す（ポジションのクローズは行わない）。

        ドローダウン検知時、保有はゼロでも未約定の買い注文が残っている
        ことがある（F4）。清算する保有が無いだけで、それが約定して新規に
        ポジションを持ってしまう事態は防ぐ必要がある。失敗は例外として
        呼び出し元 (trading_core.emergency_check) に伝える。
        """
        self.trading_client.cancel_orders()

    def cancel_order(self, order_id: str) -> None:
        """Cancel one order by its Alpaca id. Failures propagate to the caller."""
        self.trading_client.cancel_order_by_id(order_id)

    def get_order_status(self, order_id: str) -> dict | None:
        """注文ステータス確認"""
        try:
            order = self.trading_client.get_order_by_id(order_id)
            assert not isinstance(order, dict)  # see get_account()
            return {
                "order_id": str(order.id),
                "status": order.status.value,
                "filled_qty": float(order.filled_qty) if order.filled_qty else 0,
                "filled_avg_price": float(order.filled_avg_price) if order.filled_avg_price else None,
            }
        except Exception as e:
            print(f"[Trader] Order status error: {e}")
            return None
