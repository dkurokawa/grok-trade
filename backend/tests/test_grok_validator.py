"""Grok Validator unit tests"""
from grok_validator import VALID_UNIVERSE, validate_grok_report


class TestValidateGrokReport:
    def test_removes_invalid_tickers(self):
        report = {
            "sentiment": {
                "overall": 50,
                "trending_tickers": [
                    {"symbol": "NVDA", "reason": "earnings"},
                    {"symbol": "FAKE123", "reason": "hallucinated"},
                    {"symbol": "TSLA", "reason": "real"},
                ],
                "notable_signals": [],
            },
            "breaking_news": [],
            "significant_change": True,
        }
        result = validate_grok_report(report)
        symbols = [t["symbol"] for t in result["sentiment"]["trending_tickers"]]
        assert "NVDA" in symbols
        assert "TSLA" in symbols
        assert "FAKE123" not in symbols

    def test_keeps_all_valid_tickers(self):
        report = {
            "sentiment": {
                "overall": 30,
                "trending_tickers": [
                    {"symbol": "AAPL", "reason": "test"},
                    {"symbol": "MSFT", "reason": "test"},
                ],
                "notable_signals": [],
            },
            "breaking_news": [],
            "significant_change": False,
        }
        result = validate_grok_report(report)
        assert len(result["sentiment"]["trending_tickers"]) == 2

    def test_empty_tickers(self):
        report = {
            "sentiment": {
                "overall": 0,
                "trending_tickers": [],
                "notable_signals": [],
            },
            "breaking_news": [],
            "significant_change": False,
        }
        result = validate_grok_report(report)
        assert result["sentiment"]["trending_tickers"] == []

    def test_extreme_positive_sentiment_warning(self):
        report = {
            "sentiment": {
                "overall": 95,
                "trending_tickers": [],
                "notable_signals": [],
            },
            "breaking_news": [],
            "significant_change": True,
        }
        result = validate_grok_report(report)
        assert result.get("_warning") == "extreme_sentiment_may_be_hallucination"

    def test_extreme_negative_sentiment_warning(self):
        report = {
            "sentiment": {
                "overall": -95,
                "trending_tickers": [],
                "notable_signals": [],
            },
            "breaking_news": [],
            "significant_change": True,
        }
        result = validate_grok_report(report)
        assert "_warning" in result

    def test_normal_sentiment_no_warning(self):
        report = {
            "sentiment": {
                "overall": 50,
                "trending_tickers": [],
                "notable_signals": [],
            },
            "breaking_news": [],
            "significant_change": False,
        }
        result = validate_grok_report(report)
        assert "_warning" not in result

    def test_boundary_sentiment_90_no_warning(self):
        report = {
            "sentiment": {
                "overall": 90,
                "trending_tickers": [],
                "notable_signals": [],
            },
            "breaking_news": [],
            "significant_change": True,
        }
        result = validate_grok_report(report)
        assert "_warning" not in result

    def test_valid_universe_contains_expected(self):
        expected = {"SPY", "QQQ", "NVDA", "TSLA", "MSFT", "AAPL", "MSTR"}
        assert expected.issubset(VALID_UNIVERSE)
