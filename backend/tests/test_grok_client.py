"""Grok Client unit tests - updated for market report collection (no trading decisions)"""
import pytest
import json
import os
from unittest.mock import patch, MagicMock

from grok_client import GrokClient


class TestGrokClientInit:
    def test_init_with_api_key(self):
        with patch.dict(os.environ, {"GROK_API_KEY": "test_key"}):
            with patch("grok_client.OpenAI") as mock_openai:
                client = GrokClient()
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
