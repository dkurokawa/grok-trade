"""Pytest fixtures and configuration"""
import os
import sys
import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime

# Add backend to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Set test environment variables before importing modules
os.environ.setdefault("ALPACA_API_KEY", "test_key")
os.environ.setdefault("ALPACA_SECRET_KEY", "test_secret")
os.environ.setdefault("ALPACA_PAPER", "true")
os.environ.setdefault("GROK_API_KEY", "test_grok_key")
os.environ.setdefault("MAX_DAILY_LOSS", "500")
os.environ.setdefault("MAX_POSITION_RATIO", "0.5")
os.environ.setdefault("ANTHROPIC_API_KEY", "test_anthropic_key")
os.environ.setdefault("API_SHARED_SECRET", "test_shared_secret")
os.environ.setdefault("DDB_TABLE", "grok-trade-test")
# Presence of every secret key lets config.load_secrets() short-circuit without SSM.
os.environ.setdefault("DISCORD_WEBHOOK_ALERTS", "")
os.environ.setdefault("DISCORD_WEBHOOK_TRADES", "")
os.environ.setdefault("SENTRY_DSN", "")
os.environ.setdefault("AWS_DEFAULT_REGION", "ap-northeast-1")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")


@pytest.fixture
def dynamo_table():
    """Moto-backed DynamoDB table matching the SAM template schema."""
    from moto import mock_aws

    with mock_aws():
        import boto3

        res = boto3.resource("dynamodb", region_name="ap-northeast-1")
        res.create_table(
            TableName=os.environ["DDB_TABLE"],
            BillingMode="PAY_PER_REQUEST",
            AttributeDefinitions=[
                {"AttributeName": "pk", "AttributeType": "S"},
                {"AttributeName": "sk", "AttributeType": "S"},
            ],
            KeySchema=[
                {"AttributeName": "pk", "KeyType": "HASH"},
                {"AttributeName": "sk", "KeyType": "RANGE"},
            ],
        )
        # Reset the module-level cached table so it binds to the mocked resource.
        import db.dynamo as dyn

        dyn._table = None
        yield res.Table(os.environ["DDB_TABLE"])
        dyn._table = None


@pytest.fixture
def mock_account():
    """Mock Alpaca account data"""
    return {
        "cash": 100000.0,
        "portfolio_value": 100000.0,
        "buying_power": 200000.0,
        "equity": 100000.0,
        "last_equity": 99500.0,
        "daily_pnl": 500.0
    }


@pytest.fixture
def mock_positions():
    """Mock positions list"""
    return [
        {
            "symbol": "MSTR",
            "qty": 10.0,
            "avg_entry_price": 350.0,
            "market_value": 3600.0,
            "unrealized_pl": 100.0,
            "unrealized_plpc": 0.028
        },
        {
            "symbol": "TSLA",
            "qty": 5.0,
            "avg_entry_price": 240.0,
            "market_value": 1250.0,
            "unrealized_pl": 50.0,
            "unrealized_plpc": 0.04
        }
    ]


@pytest.fixture
def mock_market_data():
    """Mock market data"""
    return {
        "MSTR": {"price": 360.0, "change_5d": "+5.2%", "volume": 1000000},
        "TSLA": {"price": 250.0, "change_5d": "-2.1%", "volume": 5000000},
        "QQQ": {"price": 420.0, "change_5d": "+1.5%", "volume": 3000000},
        "SPY": {"price": 485.0, "change_5d": "+0.8%", "volume": 4000000}
    }


@pytest.fixture
def mock_grok_response_buy():
    """Mock Grok API buy response"""
    return {
        "action": "buy",
        "symbol": "MSTR",
        "quantity": 10,
        "order_type": "market",
        "reasoning": "BTC bullish momentum, MSTR correlation strong",
        "confidence": 75
    }


@pytest.fixture
def mock_grok_response_hold():
    """Mock Grok API hold response"""
    return {
        "action": "hold",
        "symbol": "MSTR",
        "quantity": 0,
        "order_type": "market",
        "reasoning": "Market uncertainty, waiting for clearer signals",
        "confidence": 60
    }


@pytest.fixture
def mock_grok_response_sell():
    """Mock Grok API sell response"""
    return {
        "action": "sell",
        "symbol": "TSLA",
        "quantity": 5,
        "order_type": "market",
        "reasoning": "Taking profits on position",
        "confidence": 80
    }


@pytest.fixture
def mock_order_result():
    """Mock successful order result"""
    return {
        "order_id": "test-order-123",
        "symbol": "MSTR",
        "side": "buy",
        "qty": 10.0,
        "type": "market",
        "status": "filled",
        "submitted_at": datetime.now().isoformat()
    }


@pytest.fixture
def empty_positions():
    """Empty positions list"""
    return []


@pytest.fixture
def large_position():
    """Large position for testing limits"""
    return [
        {
            "symbol": "MSTR",
            "qty": 100.0,
            "avg_entry_price": 350.0,
            "market_value": 40000.0,
            "unrealized_pl": 5000.0,
            "unrealized_plpc": 0.14
        }
    ]
