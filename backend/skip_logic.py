"""Opus スキップ判定ロジック"""


def should_skip_opus(grok_report: dict) -> bool:
    """Grokが変化なしと判断 → Opus呼び出しをスキップ"""
    if not grok_report.get("significant_change", False):
        return True

    sentiment = grok_report.get("sentiment", {})
    if abs(sentiment.get("overall", 0)) < 20 and len(grok_report.get("breaking_news", [])) == 0:
        return True

    return False
