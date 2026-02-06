"""Grok レポートバリデーション - ハルシネーション対策"""

VALID_UNIVERSE = {
    "SPY", "QQQ", "NVDA", "TSLA", "MSFT", "AAPL",
    "AMZN", "GOOGL", "META", "PLTR", "MSTR", "AMD",
    "NFLX", "AVGO", "CRM", "ORCL", "ADBE", "INTC",
    "COIN", "SQ", "PYPL", "UBER", "ABNB", "SNAP",
    "RIVN", "LCID", "NIO", "BABA", "JD", "PDD",
    "BA", "JPM", "GS", "V", "MA", "DIS",
    "XOM", "CVX", "LLY", "UNH", "JNJ", "PFE",
    "IWM", "DIA", "VTI", "ARKK",
}


def validate_grok_report(report: dict) -> dict:
    """Grokレポートのバリデーション。不明ティッカー除外＋極端値警告。"""
    # 不明なティッカーを除外
    tickers = report.get("sentiment", {}).get("trending_tickers", [])
    report["sentiment"]["trending_tickers"] = [
        t for t in tickers if t.get("symbol") in VALID_UNIVERSE
    ]

    # 極端なセンチメントに警告フラグ
    overall = report.get("sentiment", {}).get("overall", 0)
    if abs(overall) > 90:
        report["_warning"] = "extreme_sentiment_may_be_hallucination"

    return report
