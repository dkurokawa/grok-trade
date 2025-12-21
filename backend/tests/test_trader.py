"""Trader unit tests - comprehensive edge cases and error handling"""
import pytest
import os
from unittest.mock import patch, MagicMock
from datetime import datetime

from trader import Trader


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
                with patch("trader.StockHistoricalDataClient") as mock_data:
                    trader = Trader()
                    mock_trading.assert_called_once_with("test_key", "test_secret", paper=True)

    def test_init_live_trading(self):
        """Test initialization in live trading mode"""
        with patch.dict(os.environ, {
            "ALPACA_API_KEY": "test_key",
            "ALPACA_SECRET_KEY": "test_secret",
            "ALPACA_PAPER": "false"
        }):
            with patch("trader.TradingClient") as mock_trading:
                with patch("trader.StockHistoricalDataClient") as mock_data:
                    trader = Trader()
                    mock_trading.assert_called_once_with("test_key", "test_secret", paper=False)

    def test_init_default_paper(self):
        """Test default is paper trading when env var not set"""
        with patch.dict(os.environ, {
            "ALPACA_API_KEY": "test_key",
            "ALPACA_SECRET_KEY": "test_secret"
        }, clear=True):
            with patch("trader.TradingClient") as mock_trading:
                with patch("trader.StockHistoricalDataClient") as mock_data:
                    trader = Trader()
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
