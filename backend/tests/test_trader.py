"""Trader unit tests - comprehensive edge cases and error handling"""
import os
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from trader import DuplicateOrderError, Trader


class TestTraderInit:
    """Trader initialization tests"""

    def test_init_paper_trading(self):
        """Test initialization in paper trading mode"""
        with patch.dict(os.environ, {
            "ALPACA_API_KEY": "test_key",
            "ALPACA_SECRET_KEY": "test_secret",
            "ALPACA_PAPER": "true"
        }):
            with patch("trader.TradingClient") as mock_trading:
                with patch("trader.StockHistoricalDataClient"):
                    Trader()
                    mock_trading.assert_called_once_with("test_key", "test_secret", paper=True)

    def test_init_live_trading(self):
        """Test initialization in live trading mode"""
        with patch.dict(os.environ, {
            "ALPACA_API_KEY": "test_key",
            "ALPACA_SECRET_KEY": "test_secret",
            "ALPACA_PAPER": "false"
        }):
            with patch("trader.TradingClient") as mock_trading:
                with patch("trader.StockHistoricalDataClient"):
                    Trader()
                    mock_trading.assert_called_once_with("test_key", "test_secret", paper=False)

    def test_init_default_paper(self):
        """Test default is paper trading when env var not set"""
        with patch.dict(os.environ, {
            "ALPACA_API_KEY": "test_key",
            "ALPACA_SECRET_KEY": "test_secret"
        }, clear=True):
            with patch("trader.TradingClient") as mock_trading:
                with patch("trader.StockHistoricalDataClient"):
                    Trader()
                    mock_trading.assert_called_once_with("test_key", "test_secret", paper=True)


class TestGetAccount:
    """Get account tests"""

    @pytest.fixture
    def trader(self):
        with patch.dict(os.environ, {
            "ALPACA_API_KEY": "test_key",
            "ALPACA_SECRET_KEY": "test_secret",
            "ALPACA_PAPER": "true"
        }):
            with patch("trader.TradingClient") as mock_trading:
                with patch("trader.StockHistoricalDataClient"):
                    t = Trader()
                    return t, mock_trading.return_value

    def test_get_account_success(self, trader):
        """Test successful account retrieval"""
        t, mock_client = trader

        mock_account = MagicMock()
        mock_account.cash = "100000.00"
        mock_account.portfolio_value = "100000.00"
        mock_account.buying_power = "200000.00"
        mock_account.equity = "100000.00"
        mock_account.last_equity = "99500.00"
        mock_client.get_account.return_value = mock_account

        result = t.get_account()

        assert result["cash"] == 100000.0
        assert result["portfolio_value"] == 100000.0
        assert result["buying_power"] == 200000.0
        assert result["daily_pnl"] == 500.0

    def test_get_account_negative_pnl(self, trader):
        """Test account with negative daily P&L"""
        t, mock_client = trader

        mock_account = MagicMock()
        mock_account.cash = "95000.00"
        mock_account.portfolio_value = "95000.00"
        mock_account.buying_power = "190000.00"
        mock_account.equity = "95000.00"
        mock_account.last_equity = "100000.00"
        mock_client.get_account.return_value = mock_account

        result = t.get_account()

        assert result["daily_pnl"] == -5000.0

    def test_get_account_zero_values(self, trader):
        """Test account with zero values"""
        t, mock_client = trader

        mock_account = MagicMock()
        mock_account.cash = "0"
        mock_account.portfolio_value = "0"
        mock_account.buying_power = "0"
        mock_account.equity = "0"
        mock_account.last_equity = "0"
        mock_client.get_account.return_value = mock_account

        result = t.get_account()

        assert result["cash"] == 0.0
        assert result["daily_pnl"] == 0.0


class TestGetPositions:
    """Get positions tests"""

    @pytest.fixture
    def trader(self):
        with patch.dict(os.environ, {
            "ALPACA_API_KEY": "test_key",
            "ALPACA_SECRET_KEY": "test_secret",
            "ALPACA_PAPER": "true"
        }):
            with patch("trader.TradingClient") as mock_trading:
                with patch("trader.StockHistoricalDataClient"):
                    t = Trader()
                    return t, mock_trading.return_value

    def test_get_positions_success(self, trader):
        """Test successful positions retrieval"""
        t, mock_client = trader

        mock_position = MagicMock()
        mock_position.symbol = "MSTR"
        mock_position.qty = "10"
        mock_position.avg_entry_price = "350.00"
        mock_position.market_value = "3600.00"
        mock_position.unrealized_pl = "100.00"
        mock_position.unrealized_plpc = "0.028"
        mock_client.get_all_positions.return_value = [mock_position]

        result = t.get_positions()

        assert len(result) == 1
        assert result[0]["symbol"] == "MSTR"
        assert result[0]["qty"] == 10.0
        assert result[0]["market_value"] == 3600.0

    def test_get_positions_empty(self, trader):
        """Test empty positions"""
        t, mock_client = trader
        mock_client.get_all_positions.return_value = []

        result = t.get_positions()

        assert result == []

    def test_get_positions_multiple(self, trader):
        """Test multiple positions"""
        t, mock_client = trader

        mock_pos1 = MagicMock()
        mock_pos1.symbol = "MSTR"
        mock_pos1.qty = "10"
        mock_pos1.avg_entry_price = "350.00"
        mock_pos1.market_value = "3600.00"
        mock_pos1.unrealized_pl = "100.00"
        mock_pos1.unrealized_plpc = "0.028"

        mock_pos2 = MagicMock()
        mock_pos2.symbol = "TSLA"
        mock_pos2.qty = "5"
        mock_pos2.avg_entry_price = "240.00"
        mock_pos2.market_value = "1250.00"
        mock_pos2.unrealized_pl = "-50.00"
        mock_pos2.unrealized_plpc = "-0.04"

        mock_client.get_all_positions.return_value = [mock_pos1, mock_pos2]

        result = t.get_positions()

        assert len(result) == 2
        assert result[0]["symbol"] == "MSTR"
        assert result[1]["symbol"] == "TSLA"
        assert result[1]["unrealized_pl"] == -50.0


class TestGetMarketData:
    """Get market data tests"""

    @pytest.fixture
    def trader(self):
        with patch.dict(os.environ, {
            "ALPACA_API_KEY": "test_key",
            "ALPACA_SECRET_KEY": "test_secret",
            "ALPACA_PAPER": "true"
        }):
            with patch("trader.TradingClient"):
                with patch("trader.StockHistoricalDataClient") as mock_data:
                    t = Trader()
                    return t, mock_data.return_value

    def test_get_market_data_success(self, trader):
        """Test successful market data retrieval"""
        t, mock_data_client = trader

        mock_bar1 = MagicMock()
        mock_bar1.close = 350.0
        mock_bar1.volume = 1000000

        mock_bar2 = MagicMock()
        mock_bar2.close = 360.0
        mock_bar2.volume = 1200000

        mock_bars = MagicMock()
        mock_bars.data = {"MSTR": [mock_bar1, mock_bar2]}
        mock_data_client.get_stock_bars.return_value = mock_bars

        result = t.get_market_data(["MSTR"])

        assert "MSTR" in result
        assert result["MSTR"]["price"] == 360.0
        assert "+2.9%" in result["MSTR"]["change_5d"]

    def test_get_market_data_multiple_symbols(self, trader):
        """Test market data for multiple symbols"""
        t, mock_data_client = trader

        mock_bar_mstr1 = MagicMock(close=350.0, volume=1000000)
        mock_bar_mstr2 = MagicMock(close=360.0, volume=1200000)
        mock_bar_tsla1 = MagicMock(close=260.0, volume=5000000)
        mock_bar_tsla2 = MagicMock(close=250.0, volume=4500000)

        mock_bars = MagicMock()
        mock_bars.data = {
            "MSTR": [mock_bar_mstr1, mock_bar_mstr2],
            "TSLA": [mock_bar_tsla1, mock_bar_tsla2]
        }
        mock_data_client.get_stock_bars.return_value = mock_bars

        result = t.get_market_data(["MSTR", "TSLA"])

        assert "MSTR" in result
        assert "TSLA" in result
        assert "-" in result["TSLA"]["change_5d"]  # Negative change

    def test_get_market_data_empty_symbols(self, trader):
        """Test with empty symbol list"""
        t, mock_data_client = trader

        mock_bars = MagicMock()
        mock_bars.data = {}
        mock_data_client.get_stock_bars.return_value = mock_bars

        result = t.get_market_data([])

        assert result == {}

    def test_get_market_data_symbol_not_found(self, trader):
        """Test when symbol has no data"""
        t, mock_data_client = trader

        mock_bars = MagicMock()
        mock_bars.data = {}  # INVALID not in data
        mock_data_client.get_stock_bars.return_value = mock_bars

        result = t.get_market_data(["INVALID"])

        assert result == {}

    def test_get_market_data_insufficient_bars(self, trader):
        """Test when symbol has insufficient bar data"""
        t, mock_data_client = trader

        mock_bar = MagicMock(close=350.0, volume=1000000)
        mock_bars = MagicMock()
        mock_bars.data = {"MSTR": [mock_bar]}  # Only 1 bar, need 2
        mock_data_client.get_stock_bars.return_value = mock_bars

        result = t.get_market_data(["MSTR"])

        assert "MSTR" not in result

    def test_get_market_data_api_error(self, trader):
        """Test API error handling"""
        t, mock_data_client = trader
        mock_data_client.get_stock_bars.side_effect = Exception("API Error")

        result = t.get_market_data(["MSTR"])

        assert result == {}


class TestExecuteOrder:
    """Execute order tests"""

    @pytest.fixture
    def trader(self):
        with patch.dict(os.environ, {
            "ALPACA_API_KEY": "test_key",
            "ALPACA_SECRET_KEY": "test_secret",
            "ALPACA_PAPER": "true"
        }):
            with patch("trader.TradingClient") as mock_trading:
                with patch("trader.StockHistoricalDataClient"):
                    t = Trader()
                    return t, mock_trading.return_value

    def test_execute_buy_order_success(self, trader):
        """Test successful buy order"""
        t, mock_client = trader

        mock_order = MagicMock()
        mock_order.id = "order-123"
        mock_order.symbol = "MSTR"
        mock_order.side.value = "buy"
        mock_order.qty = "10"
        mock_order.type.value = "market"
        mock_order.status.value = "filled"
        mock_order.submitted_at = datetime.now()
        mock_client.submit_order.return_value = mock_order

        result = t.execute_order(
            symbol="MSTR",
            action="buy",
            quantity=10
        )

        assert result is not None
        assert result["order_id"] == "order-123"
        assert result["symbol"] == "MSTR"
        assert result["side"] == "buy"
        assert result["status"] == "filled"

    def test_execute_sell_order_success(self, trader):
        """Test successful sell order"""
        t, mock_client = trader

        mock_order = MagicMock()
        mock_order.id = "order-456"
        mock_order.symbol = "MSTR"
        mock_order.side.value = "sell"
        mock_order.qty = "5"
        mock_order.type.value = "market"
        mock_order.status.value = "filled"
        mock_order.submitted_at = datetime.now()
        mock_client.submit_order.return_value = mock_order

        result = t.execute_order(
            symbol="MSTR",
            action="sell",
            quantity=5
        )

        assert result is not None
        assert result["side"] == "sell"

    def test_execute_limit_order(self, trader):
        """Test limit order execution"""
        t, mock_client = trader

        mock_order = MagicMock()
        mock_order.id = "order-789"
        mock_order.symbol = "MSTR"
        mock_order.side.value = "buy"
        mock_order.qty = "10"
        mock_order.type.value = "limit"
        mock_order.status.value = "new"
        mock_order.submitted_at = datetime.now()
        mock_client.submit_order.return_value = mock_order

        result = t.execute_order(
            symbol="MSTR",
            action="buy",
            quantity=10,
            order_type="limit",
            limit_price=350.0
        )

        assert result is not None
        assert result["type"] == "limit"

    def test_execute_order_invalid_action(self, trader):
        """Test order with invalid action"""
        t, mock_client = trader

        result = t.execute_order(
            symbol="MSTR",
            action="hold",
            quantity=10
        )

        assert result is None

    def test_execute_order_zero_quantity(self, trader):
        """Test order with zero quantity"""
        t, mock_client = trader

        result = t.execute_order(
            symbol="MSTR",
            action="buy",
            quantity=0
        )

        assert result is None

    def test_execute_order_negative_quantity(self, trader):
        """Test order with negative quantity"""
        t, mock_client = trader

        result = t.execute_order(
            symbol="MSTR",
            action="buy",
            quantity=-10
        )

        assert result is None

    def test_execute_order_api_error(self, trader):
        """Test API error during order execution"""
        t, mock_client = trader
        mock_client.submit_order.side_effect = Exception("Insufficient buying power")

        result = t.execute_order(
            symbol="MSTR",
            action="buy",
            quantity=1000000
        )

        assert result is None

    def test_execute_order_rejected(self, trader):
        """Test rejected order"""
        t, mock_client = trader

        mock_order = MagicMock()
        mock_order.id = "order-rej"
        mock_order.symbol = "MSTR"
        mock_order.side.value = "buy"
        mock_order.qty = "10"
        mock_order.type.value = "market"
        mock_order.status.value = "rejected"
        mock_order.submitted_at = datetime.now()
        mock_client.submit_order.return_value = mock_order

        result = t.execute_order(
            symbol="MSTR",
            action="buy",
            quantity=10
        )

        # Order returned but with rejected status
        assert result is not None
        assert result["status"] == "rejected"


class TestGetOrderStatus:
    """Get order status tests"""

    @pytest.fixture
    def trader(self):
        with patch.dict(os.environ, {
            "ALPACA_API_KEY": "test_key",
            "ALPACA_SECRET_KEY": "test_secret",
            "ALPACA_PAPER": "true"
        }):
            with patch("trader.TradingClient") as mock_trading:
                with patch("trader.StockHistoricalDataClient"):
                    t = Trader()
                    return t, mock_trading.return_value

    def test_get_order_status_filled(self, trader):
        """Test getting filled order status"""
        t, mock_client = trader

        mock_order = MagicMock()
        mock_order.id = "order-123"
        mock_order.status.value = "filled"
        mock_order.filled_qty = "10"
        mock_order.filled_avg_price = "355.50"
        mock_client.get_order_by_id.return_value = mock_order

        result = t.get_order_status("order-123")

        assert result is not None
        assert result["status"] == "filled"
        assert result["filled_qty"] == 10.0
        assert result["filled_avg_price"] == 355.50

    def test_get_order_status_pending(self, trader):
        """Test getting pending order status"""
        t, mock_client = trader

        mock_order = MagicMock()
        mock_order.id = "order-456"
        mock_order.status.value = "new"
        mock_order.filled_qty = None
        mock_order.filled_avg_price = None
        mock_client.get_order_by_id.return_value = mock_order

        result = t.get_order_status("order-456")

        assert result is not None
        assert result["status"] == "new"
        assert result["filled_qty"] == 0
        assert result["filled_avg_price"] is None

    def test_get_order_status_not_found(self, trader):
        """Test order not found"""
        t, mock_client = trader
        mock_client.get_order_by_id.side_effect = Exception("Order not found")

        result = t.get_order_status("invalid-order-id")

        assert result is None

    def test_get_order_status_partial_fill(self, trader):
        """Test partially filled order"""
        t, mock_client = trader

        mock_order = MagicMock()
        mock_order.id = "order-789"
        mock_order.status.value = "partially_filled"
        mock_order.filled_qty = "5"
        mock_order.filled_avg_price = "352.00"
        mock_client.get_order_by_id.return_value = mock_order

        result = t.get_order_status("order-789")

        assert result is not None
        assert result["status"] == "partially_filled"
        assert result["filled_qty"] == 5.0


class TestTraderEdgeCases:
    """Edge case tests"""

    @pytest.fixture
    def trader(self):
        with patch.dict(os.environ, {
            "ALPACA_API_KEY": "test_key",
            "ALPACA_SECRET_KEY": "test_secret",
            "ALPACA_PAPER": "true"
        }):
            with patch("trader.TradingClient") as mock_trading:
                with patch("trader.StockHistoricalDataClient") as mock_data:
                    t = Trader()
                    return t, mock_trading.return_value, mock_data.return_value

    def test_execute_order_with_special_symbol(self, trader):
        """Test order with special characters in symbol"""
        t, mock_client, _ = trader

        mock_order = MagicMock()
        mock_order.id = "order-123"
        mock_order.symbol = "BRK.B"
        mock_order.side.value = "buy"
        mock_order.qty = "1"
        mock_order.type.value = "market"
        mock_order.status.value = "filled"
        mock_order.submitted_at = datetime.now()
        mock_client.submit_order.return_value = mock_order

        result = t.execute_order(
            symbol="BRK.B",
            action="buy",
            quantity=1
        )

        assert result is not None
        assert result["symbol"] == "BRK.B"

    def test_market_data_with_weekend(self, trader):
        """Test market data request over weekend (no trading days)"""
        t, _, mock_data_client = trader

        # Empty data due to weekend
        mock_bars = MagicMock()
        mock_bars.data = {"MSTR": []}
        mock_data_client.get_stock_bars.return_value = mock_bars

        result = t.get_market_data(["MSTR"])

        assert "MSTR" not in result

    def test_very_large_order(self, trader):
        """Test very large order quantity"""
        t, mock_client, _ = trader

        mock_order = MagicMock()
        mock_order.id = "order-big"
        mock_order.symbol = "MSTR"
        mock_order.side.value = "buy"
        mock_order.qty = "1000000"
        mock_order.type.value = "market"
        mock_order.status.value = "new"
        mock_order.submitted_at = datetime.now()
        mock_client.submit_order.return_value = mock_order

        result = t.execute_order(
            symbol="MSTR",
            action="buy",
            quantity=1000000
        )

        assert result is not None
        assert result["qty"] == 1000000.0

    def test_fractional_quantity(self, trader):
        """Test fractional share quantity (if supported)"""
        t, mock_client, _ = trader

        # Note: Alpaca supports fractional shares for some accounts
        result = t.execute_order(
            symbol="MSTR",
            action="buy",
            quantity=0.5
        )

        # Current implementation checks quantity > 0, and 0.5 > 0 is True
        # so the order gets submitted (Alpaca may handle fractional shares)
        assert result is not None


class TestDataFeed:
    """Alpaca's free plan only serves IEX; defaulting to SIP made every
    market-data call fail with 'subscription does not permit querying recent
    SIP data', which aborted the pipeline before Stage 1."""

    @pytest.fixture
    def make_trader(self):
        def _make():
            with patch("trader.TradingClient"), patch("trader.StockHistoricalDataClient"):
                return Trader()
        return _make

    def test_defaults_to_iex(self, make_trader, monkeypatch):
        from alpaca.data.enums import DataFeed
        monkeypatch.delenv("ALPACA_DATA_FEED", raising=False)
        assert make_trader().data_feed == DataFeed.IEX

    def test_feed_is_overridable(self, make_trader, monkeypatch):
        from alpaca.data.enums import DataFeed
        monkeypatch.setenv("ALPACA_DATA_FEED", "sip")
        assert make_trader().data_feed == DataFeed.SIP

    def test_request_carries_the_feed(self, make_trader, monkeypatch):
        from alpaca.data.enums import DataFeed
        monkeypatch.delenv("ALPACA_DATA_FEED", raising=False)
        t = make_trader()

        captured = {}

        def capture(req):
            captured["feed"] = req.feed
            raise RuntimeError("stop after capture")

        t.data_client = MagicMock()
        t.data_client.get_stock_bars.side_effect = capture
        t.get_market_data(["SPY"])

        assert captured["feed"] == DataFeed.IEX


class TestProtectiveOrders:
    """stop_loss and take_profit must ride on the entry order.

    Submitting them as two separate sell orders cannot work: the shares are not
    owned until the entry fills, and once they are, the first sell order holds
    them so the second is rejected.
    """

    @pytest.fixture
    def bracket_trader(self):
        with patch.dict(os.environ, {
            "ALPACA_API_KEY": "k", "ALPACA_SECRET_KEY": "s", "ALPACA_PAPER": "true",
        }):
            with patch("trader.TradingClient") as mock_trading:
                with patch("trader.StockHistoricalDataClient"):
                    t = Trader()
                    order = MagicMock()
                    order.id = "order-1"
                    order.symbol = "MSTR"
                    order.side.value = "buy"
                    order.qty = "7"
                    order.type.value = "market"
                    order.status.value = "accepted"
                    order.submitted_at = datetime.now()
                    mock_trading.return_value.submit_order.return_value = order
                    return t, mock_trading.return_value

    def _request(self, mock_client):
        return mock_client.submit_order.call_args[0][0]

    def test_both_legs_make_a_bracket(self, bracket_trader):
        from alpaca.trading.enums import OrderClass
        t, mock_client = bracket_trader
        t.execute_order("MSTR", "buy", 7, stop_loss=120.0, take_profit=150.0)

        req = self._request(mock_client)
        assert req.order_class == OrderClass.BRACKET
        assert req.stop_loss.stop_price == 120.0
        assert req.take_profit.limit_price == 150.0
        assert mock_client.submit_order.call_count == 1  # one order, not three

    def test_stop_loss_only_makes_an_oto(self, bracket_trader):
        from alpaca.trading.enums import OrderClass
        t, mock_client = bracket_trader
        t.execute_order("MSTR", "buy", 7, stop_loss=120.0)

        req = self._request(mock_client)
        assert req.order_class == OrderClass.OTO
        assert req.stop_loss.stop_price == 120.0

    def test_plain_buy_has_no_order_class(self, bracket_trader):
        t, mock_client = bracket_trader
        t.execute_order("MSTR", "buy", 7)
        assert self._request(mock_client).order_class is None

    def test_prices_rounded_to_cents(self, bracket_trader):
        """Alpaca rejects sub-penny prices on stocks over $1."""
        t, mock_client = bracket_trader
        t.execute_order("MSTR", "buy", 7, stop_loss=120.456, take_profit=150.994)

        req = self._request(mock_client)
        assert req.stop_loss.stop_price == 120.46
        assert req.take_profit.limit_price == 150.99

    def test_sell_ignores_protective_legs(self, bracket_trader):
        """A sell closes a position; attaching a stop to it would open a short."""
        t, mock_client = bracket_trader
        t.execute_order("MSTR", "sell", 7, stop_loss=120.0, take_profit=150.0)
        assert self._request(mock_client).order_class is None


class TestOpenBuyOrders:
    @pytest.fixture
    def order_trader(self):
        with patch("trader.TradingClient") as mock_trading:
            with patch("trader.StockHistoricalDataClient"):
                return Trader(), mock_trading.return_value

    def _order(self, symbol, side, qty=1.0, limit_price=None):
        o = MagicMock()
        o.symbol, o.side, o.qty, o.limit_price = symbol, side, qty, limit_price
        return o

    def test_returns_only_buy_side_symbols(self, order_trader):
        """Protective sells linger for held symbols; they must not look like a
        pending entry and block the next buy forever."""
        from alpaca.trading.enums import OrderSide
        t, mock_client = order_trader

        buy = self._order("MSTR", OrderSide.BUY)
        sell = self._order("TSLA", OrderSide.SELL)
        mock_client.get_orders.return_value = [buy, sell]

        assert t.get_open_buy_order_symbols() == {"MSTR"}

    def test_returns_empty_set_on_error(self, order_trader):
        t, mock_client = order_trader
        mock_client.get_orders.side_effect = RuntimeError("boom")
        assert t.get_open_buy_order_symbols() == set()


class TestGetOpenBuyOrders:
    """get_open_buy_orders() (Issue M2): symbol/qty/limit_price for each
    open buy order, used to fold pending buys into the total position ratio."""

    @pytest.fixture
    def order_trader(self):
        with patch("trader.TradingClient") as mock_trading:
            with patch("trader.StockHistoricalDataClient"):
                return Trader(), mock_trading.return_value

    def _order(self, symbol, side, qty=1.0, limit_price=None):
        o = MagicMock()
        o.symbol, o.side, o.qty, o.limit_price = symbol, side, qty, limit_price
        return o

    def test_returns_symbol_qty_limit_price_for_buys_only(self, order_trader):
        from alpaca.trading.enums import OrderSide
        t, mock_client = order_trader

        buy = self._order("MSTR", OrderSide.BUY, qty=10, limit_price=350.5)
        sell = self._order("TSLA", OrderSide.SELL, qty=5)
        mock_client.get_orders.return_value = [buy, sell]

        assert t.get_open_buy_orders() == [
            {"symbol": "MSTR", "qty": 10.0, "limit_price": 350.5},
        ]

    def test_market_buy_order_has_no_limit_price(self, order_trader):
        from alpaca.trading.enums import OrderSide
        t, mock_client = order_trader

        buy = self._order("QQQ", OrderSide.BUY, qty=3, limit_price=None)
        mock_client.get_orders.return_value = [buy]

        assert t.get_open_buy_orders() == [{"symbol": "QQQ", "qty": 3.0, "limit_price": None}]

    def test_returns_empty_list_on_error(self, order_trader):
        t, mock_client = order_trader
        mock_client.get_orders.side_effect = RuntimeError("boom")
        assert t.get_open_buy_orders() == []


class TestEmergencyLiquidation:
    @pytest.fixture
    def liquidation_trader(self):
        with patch("trader.TradingClient") as mock_trading:
            with patch("trader.StockHistoricalDataClient"):
                return Trader(), mock_trading.return_value

    def test_cancels_open_orders_before_closing(self, liquidation_trader):
        """Bracket legs hold the shares, so a plain sell would be rejected."""
        t, mock_client = liquidation_trader
        closed = MagicMock()
        closed.symbol, closed.status = "MSTR", 200
        mock_client.close_all_positions.return_value = [closed]

        results = t.execute_emergency_liquidation()

        mock_client.close_all_positions.assert_called_once_with(cancel_orders=True)
        assert results == [{"symbol": "MSTR", "status": 200, "ok": True}]

    def test_reports_per_symbol_failure_not_ok(self, liquidation_trader):
        """A per-symbol failure (e.g. status 500) must be visible to the
        caller (Issue M1) - not silently reported as if it succeeded."""
        from alpaca.trading.models import FailedClosePositionDetails

        t, mock_client = liquidation_trader
        failed = MagicMock()
        failed.symbol, failed.status = "TSLA", 500
        failed.body = FailedClosePositionDetails(code=40310000, message="insufficient qty available")
        mock_client.close_all_positions.return_value = [failed]

        results = t.execute_emergency_liquidation()

        assert results == [{
            "symbol": "TSLA", "status": 500, "ok": False,
            "error": "insufficient qty available",
        }]

    def test_mixed_success_and_failure(self, liquidation_trader):
        from alpaca.trading.models import FailedClosePositionDetails

        t, mock_client = liquidation_trader
        ok = MagicMock()
        ok.symbol, ok.status = "MSTR", 200
        bad = MagicMock()
        bad.symbol, bad.status = "TSLA", 500
        bad.body = FailedClosePositionDetails(code=40310000, message="boom")
        mock_client.close_all_positions.return_value = [ok, bad]

        results = t.execute_emergency_liquidation()

        assert [r["ok"] for r in results] == [True, False]

    def test_close_all_positions_exception_propagates(self, liquidation_trader):
        """execute_emergency_liquidation() must not swallow a failure to even
        attempt closing - a silent [] would look identical to "nothing was
        held", which is indistinguishable from "liquidation never ran"."""
        t, mock_client = liquidation_trader
        mock_client.close_all_positions.side_effect = RuntimeError("boom")
        with pytest.raises(RuntimeError, match="boom"):
            t.execute_emergency_liquidation()


class TestClientOrderIdDedup:
    """client_order_id lets Alpaca itself reject a duplicate order - the
    second line of defense behind trading_core's DynamoDB slot lock."""

    @pytest.fixture
    def trader(self):
        with patch("trader.TradingClient") as mock_trading:
            with patch("trader.StockHistoricalDataClient"):
                return Trader(), mock_trading.return_value

    def _request(self, mock_client):
        return mock_client.submit_order.call_args[0][0]

    def test_client_order_id_is_sent_on_the_request(self, trader):
        t, mock_client = trader
        order = MagicMock()
        order.id, order.symbol = "order-1", "MSTR"
        order.side.value, order.qty, order.type.value = "buy", "7", "market"
        order.status.value, order.submitted_at = "accepted", datetime.now()
        mock_client.submit_order.return_value = order

        t.execute_order("MSTR", "buy", 7, client_order_id="gt-20260101T0930-MSTR-buy")

        assert self._request(mock_client).client_order_id == "gt-20260101T0930-MSTR-buy"

    def test_duplicate_client_order_id_raises_not_returns_none(self, trader):
        t, mock_client = trader
        mock_client.submit_order.side_effect = Exception(
            "client_order_id must be unique - an order with this client_order_id already exists"
        )

        with pytest.raises(DuplicateOrderError):
            t.execute_order("MSTR", "buy", 7, client_order_id="gt-20260101T0930-MSTR-buy")

    def test_unrelated_error_still_returns_none(self, trader):
        """Only a duplicate client_order_id should raise; every other submit
        failure keeps the existing "return None" contract."""
        t, mock_client = trader
        mock_client.submit_order.side_effect = Exception("insufficient buying power")

        result = t.execute_order("MSTR", "buy", 7, client_order_id="gt-20260101T0930-MSTR-buy")
        assert result is None
