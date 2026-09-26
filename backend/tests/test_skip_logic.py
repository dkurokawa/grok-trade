"""Skip Logic unit tests"""
from skip_logic import should_skip_opus


class TestShouldSkipOpus:
    def test_skip_when_no_significant_change(self):
        report = {
            "significant_change": False,
            "sentiment": {"overall": 10},
            "breaking_news": [],
        }
        assert should_skip_opus(report) is True

    def test_skip_when_significant_change_missing(self):
        report = {"sentiment": {"overall": 10}, "breaking_news": []}
        assert should_skip_opus(report) is True

    def test_no_skip_when_significant_change_true_and_strong_sentiment(self):
        report = {
            "significant_change": True,
            "sentiment": {"overall": 50},
            "breaking_news": [],
        }
        assert should_skip_opus(report) is False

    def test_skip_when_significant_but_weak_sentiment_no_news(self):
        """significant_change=True でもセンチメント弱く & ニュースなし → スキップ"""
        report = {
            "significant_change": True,
            "sentiment": {"overall": 15},
            "breaking_news": [],
        }
        assert should_skip_opus(report) is True

    def test_no_skip_when_breaking_news_present(self):
        report = {
            "significant_change": True,
            "sentiment": {"overall": 10},
            "breaking_news": ["Fed rate decision"],
        }
        assert should_skip_opus(report) is False

    def test_no_skip_when_strong_negative_sentiment(self):
        report = {
            "significant_change": True,
            "sentiment": {"overall": -50},
            "breaking_news": [],
        }
        assert should_skip_opus(report) is False

    def test_boundary_sentiment_20_skips(self):
        """abs(sentiment) < 20 → スキップ（19はスキップ）"""
        report = {
            "significant_change": True,
            "sentiment": {"overall": 19},
            "breaking_news": [],
        }
        assert should_skip_opus(report) is True

    def test_boundary_sentiment_20_no_skip(self):
        """abs(sentiment) == 20 → スキップしない"""
        report = {
            "significant_change": True,
            "sentiment": {"overall": 20},
            "breaking_news": [],
        }
        assert should_skip_opus(report) is False

    def test_empty_report(self):
        assert should_skip_opus({}) is True

    def test_missing_sentiment(self):
        report = {"significant_change": True, "breaking_news": []}
        # sentiment missing → overall defaults to 0, abs(0) < 20 → skip
        assert should_skip_opus(report) is True
