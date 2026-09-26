"""Grok Client unit tests - updated for market report collection (no trading decisions)"""
import json
import os
from unittest.mock import MagicMock, patch

import pytest

from grok_client import GrokClient


class TestGrokClientInit:
    def test_init_with_api_key(self):
        with patch.dict(os.environ, {"GROK_API_KEY": "test_key"}):
            with patch("grok_client.OpenAI") as mock_openai:
                GrokClient()
                mock_openai.assert_called_once_with(
                    api_key="test_key",
                    base_url="https://api.x.ai/v1"
                )

    def test_default_model(self):
        with patch.dict(os.environ, {"GROK_API_KEY": "test_key"}, clear=False):
            with patch("grok_client.OpenAI"):
                client = GrokClient()
                assert client.model == "grok-3-mini"

    def test_custom_model_from_env(self):
        with patch.dict(os.environ, {"GROK_API_KEY": "test_key", "GROK_MODEL": "grok-turbo"}):
            with patch("grok_client.OpenAI"):
                client = GrokClient()
                assert client.model == "grok-turbo"


class TestBuildPrompt:
    @pytest.fixture
    def client(self):
        with patch.dict(os.environ, {"GROK_API_KEY": "test_key"}):
            with patch("grok_client.OpenAI"):
                return GrokClient()

    def test_prompt_includes_market_data(self, client, mock_market_data):
        prompt = client._build_prompt(market_data=mock_market_data, positions=[])
        assert "MSTR" in prompt
        assert "360" in prompt or "360.0" in prompt

    def test_prompt_includes_positions(self, client, mock_positions):
        prompt = client._build_prompt(market_data={}, positions=mock_positions)
        assert "MSTR" in prompt

    def test_prompt_empty_positions(self, client):
        prompt = client._build_prompt(market_data={}, positions=[])
        assert "None" in prompt

    def test_prompt_requests_json(self, client):
        prompt = client._build_prompt(market_data={}, positions=[])
        assert "JSON" in prompt or "json" in prompt

    def test_prompt_mentions_significant_change(self, client):
        prompt = client._build_prompt(market_data={}, positions=[])
        assert "significant_change" in prompt

    def test_prompt_defaults_to_no_previous_data(self, client):
        """Without an explicit previous_sentiment, the prompt must say so
        rather than silently omit the comparison Grok is asked to make."""
        prompt = client._build_prompt(market_data={}, positions=[])
        from grok_client import NO_PREVIOUS_SENTIMENT
        assert NO_PREVIOUS_SENTIMENT in prompt

    def test_prompt_includes_previous_sentiment_when_given(self, client):
        prompt = client._build_prompt(
            market_data={}, positions=[],
            previous_sentiment="時刻: 2026-01-01T10:00:00 / センチメント: 42 / 重要な変化と判定されたか: False",
        )
        assert "センチメント: 42" in prompt

    def test_prompt_prohibits_recommendations(self, client):
        """system promptに売買推奨禁止が含まれていることを確認"""
        from grok_client import GROK_SYSTEM
        assert "売買の推奨は絶対にしないでください" in GROK_SYSTEM


class TestParseResponse:
    @pytest.fixture
    def client(self):
        with patch.dict(os.environ, {"GROK_API_KEY": "test_key"}):
            with patch("grok_client.OpenAI"):
                return GrokClient()

    def test_parse_valid_market_report(self, client):
        raw = json.dumps({
            "timestamp": "2026-02-06 10:00 EST",
            "significant_change": True,
            "sentiment": {
                "overall": 45,
                "trending_tickers": [{"symbol": "NVDA", "reason": "earnings"}],
                "notable_signals": ["tech rally"],
            },
            "breaking_news": ["Fed holds rates steady"],
            "market_context": {
                "spy_trend": "bullish",
                "vix_level": "low",
                "sector_rotation": "tech",
            },
        })
        result = client._parse_response(raw)
        assert result is not None
        assert result["significant_change"] is True
        assert result["sentiment"]["overall"] == 45
        assert len(result["breaking_news"]) == 1

    def test_parse_with_markdown_block(self, client):
        inner = json.dumps({
            "significant_change": False,
            "sentiment": {"overall": 0, "trending_tickers": [], "notable_signals": []},
            "breaking_news": [],
            "market_context": {"spy_trend": "neutral", "vix_level": "moderate", "sector_rotation": "none"},
        })
        raw = f"```json\n{inner}\n```"
        result = client._parse_response(raw)
        assert result is not None
        assert result["significant_change"] is False

    def test_parse_defaults_missing_fields(self, client):
        raw = json.dumps({"timestamp": "2026-02-06 10:00 EST"})
        result = client._parse_response(raw)
        assert result is not None
        assert result["significant_change"] is False
        assert result["sentiment"]["overall"] == 0
        assert result["breaking_news"] == []

    def test_parse_normalizes_sentiment_to_int(self, client):
        raw = json.dumps({
            "significant_change": True,
            "sentiment": {"overall": 45.7, "trending_tickers": [], "notable_signals": []},
            "breaking_news": [],
        })
        result = client._parse_response(raw)
        assert isinstance(result["sentiment"]["overall"], int)
        assert result["sentiment"]["overall"] == 45

    def test_parse_invalid_json(self, client):
        result = client._parse_response("not json")
        assert result is None

    def test_parse_empty_string(self, client):
        result = client._parse_response("")
        assert result is None

    def test_parse_unicode(self, client):
        raw = json.dumps({
            "significant_change": True,
            "sentiment": {"overall": 30, "trending_tickers": [], "notable_signals": ["日銀利上げ観測"]},
            "breaking_news": ["日銀金融政策決定"],
        })
        result = client._parse_response(raw)
        assert result is not None
        assert "日銀" in result["breaking_news"][0]


class TestCollectMarketReport:
    @pytest.fixture
    def client(self):
        with patch.dict(os.environ, {"GROK_API_KEY": "test_key"}):
            with patch("grok_client.OpenAI"):
                return GrokClient()

    def test_collect_success(self, client, mock_market_data):
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = json.dumps({
            "significant_change": True,
            "sentiment": {"overall": 50, "trending_tickers": [], "notable_signals": []},
            "breaking_news": ["Big news"],
            "market_context": {"spy_trend": "bullish", "vix_level": "low", "sector_rotation": "tech"},
        })
        client.client.chat.completions.create = MagicMock(return_value=mock_response)

        report, latency = client.collect_market_report(
            market_data=mock_market_data, positions=[],
        )
        assert report is not None
        assert report["significant_change"] is True
        assert latency >= 0

    def test_collect_forwards_previous_sentiment_to_the_prompt(self, client, mock_market_data):
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = json.dumps({
            "significant_change": False,
            "sentiment": {"overall": 0, "trending_tickers": [], "notable_signals": []},
            "breaking_news": [],
        })
        client.client.chat.completions.create = MagicMock(return_value=mock_response)

        client.collect_market_report(
            market_data=mock_market_data, positions=[],
            previous_sentiment="センチメント: 77",
        )

        sent_prompt = client.client.chat.completions.create.call_args.kwargs["messages"][1]["content"]
        assert "センチメント: 77" in sent_prompt

    def test_collect_api_error(self, client, mock_market_data):
        client.client.chat.completions.create = MagicMock(side_effect=Exception("API Error"))
        report, latency = client.collect_market_report(
            market_data=mock_market_data, positions=[],
        )
        assert report is None
        assert latency >= 0

    def test_collect_returns_latency(self, client, mock_market_data):
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = json.dumps({
            "significant_change": False,
            "sentiment": {"overall": 0, "trending_tickers": [], "notable_signals": []},
            "breaking_news": [],
        })
        client.client.chat.completions.create = MagicMock(return_value=mock_response)

        _, latency = client.collect_market_report(
            market_data=mock_market_data, positions=[],
        )
        assert isinstance(latency, int)

    def test_collect_empty_response(self, client, mock_market_data):
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = ""
        client.client.chat.completions.create = MagicMock(return_value=mock_response)

        report, _ = client.collect_market_report(
            market_data=mock_market_data, positions=[],
        )
        assert report is None

    def test_collect_no_choices(self, client, mock_market_data):
        mock_response = MagicMock()
        mock_response.choices = []
        client.client.chat.completions.create = MagicMock(return_value=mock_response)

        report, _ = client.collect_market_report(
            market_data=mock_market_data, positions=[],
        )
        assert report is None


class TestDecide:
    """DECISION_ENGINE=grok: Grok returns the same TradeDecision shape as Opus,
    so Risk Guard and execution stay engine-agnostic."""

    @pytest.fixture
    def client(self):
        with patch.dict(os.environ, {"GROK_API_KEY": "test_key"}):
            return GrokClient()

    def _response(self, payload):
        resp = MagicMock()
        resp.choices = [MagicMock()]
        resp.choices[0].message.content = payload
        return resp

    DECISION = json.dumps({
        "action": "buy", "symbol": "MSTR", "quantity": 7,
        "order_type": "market", "limit_price": None,
        "stop_loss": 120.0, "take_profit": 150.0,
        "position_size_pct": 30, "reasoning": "dip buy",
        "risk_assessment": "medium", "confidence": 70, "adjustments": [],
    })

    def test_returns_trade_decision(self, client, mock_market_data):
        client.client.chat.completions.create = MagicMock(
            return_value=self._response(self.DECISION)
        )

        decision, latency = client.decide(
            balance=1000.0, positions=[], daily_pnl=0.0, max_daily_loss=500.0,
            price_data=mock_market_data, grok_report={"sentiment": {"overall": 60}},
        )

        assert decision["action"] == "buy"
        assert decision["symbol"] == "MSTR"
        assert decision["quantity"] == 7
        assert decision["stop_loss"] == 120.0
        assert decision["confidence"] == 70
        assert latency >= 0

    def test_prompt_carries_balance_and_report(self, client, mock_market_data):
        create = MagicMock(return_value=self._response(self.DECISION))
        client.client.chat.completions.create = create

        client.decide(
            balance=1234.0, positions=[], daily_pnl=-10.0, max_daily_loss=500.0,
            price_data=mock_market_data, grok_report={"sentiment": {"overall": 42}},
        )

        prompt = create.call_args[1]["messages"][1]["content"]
        assert "1,234.00" in prompt
        assert "42" in prompt

    def test_api_error_returns_none(self, client, mock_market_data):
        client.client.chat.completions.create = MagicMock(side_effect=Exception("API down"))

        decision, latency = client.decide(
            balance=1000.0, positions=[], daily_pnl=0.0, max_daily_loss=500.0,
            price_data=mock_market_data, grok_report={},
        )

        assert decision is None
        assert latency >= 0

    def test_unparseable_response_returns_none(self, client, mock_market_data):
        client.client.chat.completions.create = MagicMock(
            return_value=self._response("no json here")
        )

        decision, _ = client.decide(
            balance=1000.0, positions=[], daily_pnl=0.0, max_daily_loss=500.0,
            price_data=mock_market_data, grok_report={},
        )

        assert decision is None
