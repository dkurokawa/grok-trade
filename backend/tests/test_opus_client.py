"""Opus Client unit tests"""

import json
import os
from unittest.mock import MagicMock, patch

import pytest

from opus_client import OpusClient, _validate_decision, parse_opus_response


class TestOpusClientInit:
    def test_init_with_api_key(self):
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test_key"}):
            with patch("opus_client.anthropic.Anthropic") as mock_anthropic:
                OpusClient()
                mock_anthropic.assert_called_once_with(api_key="test_key")

    def test_model_setting(self):
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test_key"}):
            with patch("opus_client.anthropic.Anthropic"):
                client = OpusClient()
                assert client.model == "claude-opus-4-6-20260205"


class TestBuildPrompt:
    @pytest.fixture
    def client(self):
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test_key"}):
            with patch("opus_client.anthropic.Anthropic"):
                return OpusClient()

    def test_prompt_includes_balance(self, client):
        prompt = client._build_prompt(
            balance=100000.0,
            positions=[],
            daily_pnl=500.0,
            max_daily_loss=500.0,
            price_data={},
            grok_report={},
        )
        assert "100,000.00" in prompt

    def test_prompt_includes_daily_pnl(self, client):
        prompt = client._build_prompt(
            balance=100000.0,
            positions=[],
            daily_pnl=-200.0,
            max_daily_loss=500.0,
            price_data={},
            grok_report={},
        )
        assert "-200.00" in prompt

    def test_prompt_includes_grok_report(self, client):
        grok_report = {"sentiment": {"overall": 50}, "significant_change": True}
        prompt = client._build_prompt(
            balance=100000.0,
            positions=[],
            daily_pnl=0,
            max_daily_loss=500.0,
            price_data={},
            grok_report=grok_report,
        )
        assert "significant_change" in prompt

    def test_prompt_includes_positions(self, client, mock_positions):
        prompt = client._build_prompt(
            balance=100000.0,
            positions=mock_positions,
            daily_pnl=0,
            max_daily_loss=500.0,
            price_data={},
            grok_report={},
        )
        assert "MSTR" in prompt


class TestParseOpusResponse:
    def test_parse_valid_json(self):
        raw = json.dumps(
            {
                "action": "buy",
                "symbol": "NVDA",
                "quantity": 10,
                "reasoning": "bullish",
                "confidence": 75,
                "stop_loss": 118.5,
                "take_profit": 135.0,
                "position_size_pct": 30,
                "risk_assessment": "medium",
                "order_type": "market",
                "adjustments": [],
            }
        )
        result = parse_opus_response(raw)
        assert result is not None
        assert result["action"] == "buy"
        assert result["symbol"] == "NVDA"
        assert result["quantity"] == 10

    def test_parse_json_code_block(self):
        raw = '```json\n{"action": "hold", "symbol": "SPY", "quantity": 0, "reasoning": "wait", "confidence": 50}\n```'
        result = parse_opus_response(raw)
        assert result is not None
        assert result["action"] == "hold"

    def test_parse_with_preamble(self):
        raw = 'Here is my analysis:\n{"action": "sell", "symbol": "TSLA", "quantity": 5, "reasoning": "overbought", "confidence": 65}'
        result = parse_opus_response(raw)
        assert result is not None
        assert result["action"] == "sell"

    def test_parse_prefill_response(self):
        """prefill `{` を使った場合のレスポンス結合テスト"""
        api_response = '"action": "buy", "symbol": "MSTR", "quantity": 10, "reasoning": "test", "confidence": 80}'
        raw = "{" + api_response
        result = parse_opus_response(raw)
        assert result is not None
        assert result["action"] == "buy"

    def test_parse_invalid_json(self):
        result = parse_opus_response("not json at all")
        assert result is None

    def test_parse_empty_string(self):
        result = parse_opus_response("")
        assert result is None

    def test_parse_missing_required_field(self):
        raw = json.dumps({"action": "buy", "symbol": "MSTR"})
        result = parse_opus_response(raw)
        assert result is None

    def test_parse_invalid_action(self):
        raw = json.dumps(
            {
                "action": "short",
                "symbol": "MSTR",
                "quantity": 10,
                "reasoning": "test",
                "confidence": 75,
            }
        )
        result = parse_opus_response(raw)
        assert result is None


class TestValidateDecision:
    def test_valid_buy(self):
        data = {
            "action": "buy",
            "symbol": "NVDA",
            "quantity": 10,
            "reasoning": "bullish",
            "confidence": 75,
        }
        result = _validate_decision(data)
        assert result is not None
        assert result["order_type"] == "market"
        assert result["position_size_pct"] == 0.0
        assert result["adjustments"] == []

    def test_normalizes_action_case(self):
        data = {
            "action": "BUY",
            "symbol": "MSTR",
            "quantity": 5,
            "reasoning": "test",
            "confidence": 60,
        }
        result = _validate_decision(data)
        assert result["action"] == "buy"

    def test_normalizes_order_type_case(self):
        data = {
            "action": "buy",
            "symbol": "MSTR",
            "quantity": 5,
            "reasoning": "test",
            "confidence": 60,
            "order_type": "LIMIT",
        }
        result = _validate_decision(data)
        assert result["order_type"] == "limit"

    def test_preserves_adjustments(self):
        adj = [{"field": "position_size_pct", "original": 50, "adjusted": 30, "reason": "VIX high"}]
        data = {
            "action": "buy",
            "symbol": "MSTR",
            "quantity": 5,
            "reasoning": "test",
            "confidence": 60,
            "adjustments": adj,
        }
        result = _validate_decision(data)
        assert len(result["adjustments"]) == 1
        assert result["adjustments"][0]["field"] == "position_size_pct"

    def test_missing_field_returns_none(self):
        data = {"action": "buy", "symbol": "MSTR"}
        result = _validate_decision(data)
        assert result is None

    def test_invalid_action_returns_none(self):
        data = {
            "action": "short",
            "symbol": "MSTR",
            "quantity": 5,
            "reasoning": "test",
            "confidence": 60,
        }
        result = _validate_decision(data)
        assert result is None

    def test_stop_loss_preserved(self):
        data = {
            "action": "buy",
            "symbol": "MSTR",
            "quantity": 5,
            "reasoning": "test",
            "confidence": 60,
            "stop_loss": 118.5,
            "take_profit": 135.0,
        }
        result = _validate_decision(data)
        assert result["stop_loss"] == 118.5
        assert result["take_profit"] == 135.0


class TestAnalyze:
    @pytest.fixture
    def client(self):
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test_key"}):
            with patch("opus_client.anthropic.Anthropic"):
                c = OpusClient()
                return c

    def test_analyze_success(self, client, mock_market_data):
        mock_response = MagicMock()
        mock_response.content = [MagicMock()]
        mock_response.content[0].text = (
            '"action": "buy", "symbol": "MSTR", "quantity": 10, '
            '"reasoning": "bullish", "confidence": 75, '
            '"stop_loss": 340, "take_profit": 400, '
            '"position_size_pct": 25, "risk_assessment": "medium", '
            '"order_type": "market", "adjustments": []}'
        )
        client.client.messages.create = MagicMock(return_value=mock_response)

        grok_report = {"sentiment": {"overall": 50}, "significant_change": True}
        decision, latency = client.analyze(
            balance=100000,
            positions=[],
            daily_pnl=0,
            max_daily_loss=500,
            price_data=mock_market_data,
            grok_report=grok_report,
        )
        assert decision is not None
        assert decision["action"] == "buy"
        assert latency >= 0

    def test_analyze_api_error(self, client, mock_market_data):
        client.client.messages.create = MagicMock(side_effect=Exception("API Error"))
        decision, latency = client.analyze(
            balance=100000,
            positions=[],
            daily_pnl=0,
            max_daily_loss=500,
            price_data=mock_market_data,
            grok_report={},
        )
        assert decision is None
        assert latency >= 0

    def test_analyze_returns_latency(self, client, mock_market_data):
        mock_response = MagicMock()
        mock_response.content = [MagicMock()]
        mock_response.content[
            0
        ].text = '"action": "hold", "symbol": "SPY", "quantity": 0, "reasoning": "wait", "confidence": 50}'
        client.client.messages.create = MagicMock(return_value=mock_response)

        _, latency = client.analyze(
            balance=100000,
            positions=[],
            daily_pnl=0,
            max_daily_loss=500,
            price_data=mock_market_data,
            grok_report={},
        )
        assert isinstance(latency, int)
