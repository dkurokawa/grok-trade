"""Grok API クライアント - 市場情報収集 / 売買判断（DECISION_ENGINE=grok 時）"""
import json
import os
import time
from typing import Optional

from openai import OpenAI

# 売買判断は Opus と同じプロンプト・同じ TradeDecision 形式を使う。
# 形式が揃っていれば Risk Guard 以降の処理はエンジンに依存しない。
from opus_client import OPUS_PROMPT_TEMPLATE, OPUS_SYSTEM, parse_opus_response

GROK_SYSTEM = """あなたは市場情報アナリストです。
事実の報告のみ行ってください。売買の推奨は絶対にしないでください。"""

GROK_PROMPT_TEMPLATE = """以下のJSON形式で市場状況を報告してください。

{{
  "timestamp": "EST時刻",
  "significant_change": true/false,
  "sentiment": {{
    "overall": -100〜+100,
    "trending_tickers": [
      {{"symbol": "NVDA", "reason": "決算発表後の反応"}}
    ],
    "notable_signals": ["影響力のある動き・投稿の要約"]
  }},
  "breaking_news": ["直近の重要ニュース（なければ空配列）"],
  "market_context": {{
    "spy_trend": "bullish/bearish/neutral",
    "vix_level": "low/moderate/high/extreme",
    "sector_rotation": "どのセクターに資金が動いているか"
  }}
}}

重要: significant_change は以下の場合のみ true にしてください:
- センチメントが前回から大きく変動（±30以上）
- 重要ニュースがある
- VIXが急変
それ以外は false にしてください。

== 現在の市場データ ==
{market_data}

== 監視銘柄のポジション情報 ==
{positions}
"""


class GrokClient:
    def __init__(self):
        self.client = OpenAI(
            api_key=os.getenv("GROK_API_KEY"),
            base_url="https://api.x.ai/v1"
        )
        self.model = os.getenv("GROK_MODEL", "grok-3-mini")

    def collect_market_report(
        self,
        market_data: dict,
        positions: list[dict],
    ) -> tuple[Optional[dict], int]:
        """
        市場情報を収集してMarketReportを返す。判断はしない。

        Returns:
            (report_dict or None, latency_ms)
        """
        prompt = self._build_prompt(market_data, positions)

        start = time.time()
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": GROK_SYSTEM},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=800,
                temperature=0.3,
            )

            latency_ms = int((time.time() - start) * 1000)
            raw_response = response.choices[0].message.content
            report = self._parse_response(raw_response)
            return report, latency_ms

        except Exception as e:
            latency_ms = int((time.time() - start) * 1000)
            print(f"[Grok] API error: {e}")
            return None, latency_ms

    def decide(
        self,
        balance: float,
        positions: list[dict],
        daily_pnl: float,
        max_daily_loss: float,
        price_data: dict,
        grok_report: dict,
    ) -> tuple[Optional[dict], int]:
        """
        市場データと自身のレポートから売買判断を返す（OpusClient.analyze と同じ契約）。

        Returns:
            (decision_dict or None, latency_ms)
        """
        prompt = OPUS_PROMPT_TEMPLATE.format(
            balance=f"{balance:,.2f}",
            positions=json.dumps(positions, indent=2) if positions else "None",
            daily_pnl=f"{daily_pnl:+,.2f}",
            max_daily_loss=f"{max_daily_loss:,.2f}",
            price_data=json.dumps(price_data, indent=2),
            grok_report=json.dumps(grok_report, indent=2),
        )

        start = time.time()
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": OPUS_SYSTEM},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=1024,
                temperature=0.3,
            )
            latency_ms = int((time.time() - start) * 1000)
            return parse_opus_response(response.choices[0].message.content), latency_ms

        except Exception as e:
            latency_ms = int((time.time() - start) * 1000)
            print(f"[Grok] Decision API error: {e}")
            return None, latency_ms

    def _build_prompt(
        self,
        market_data: dict,
        positions: list[dict],
    ) -> str:
        market_str = json.dumps(market_data, indent=2)
        positions_str = json.dumps(positions, indent=2) if positions else "None"

        return GROK_PROMPT_TEMPLATE.format(
            market_data=market_str,
            positions=positions_str,
        )

    def _parse_response(self, raw: str) -> Optional[dict]:
        """レスポンスをパースしてMarketReportを返す"""
        try:
            # JSONブロック抽出（```json ... ``` 対応）
            if "```" in raw:
                start = raw.find("{")
                end = raw.rfind("}") + 1
                raw = raw[start:end]

            data = json.loads(raw)

            # 必須フィールド検証
            if "significant_change" not in data:
                data["significant_change"] = False

            if "sentiment" not in data:
                data["sentiment"] = {"overall": 0, "trending_tickers": [], "notable_signals": []}

            if "breaking_news" not in data:
                data["breaking_news"] = []

            if "market_context" not in data:
                data["market_context"] = {
                    "spy_trend": "neutral",
                    "vix_level": "moderate",
                    "sector_rotation": "unknown",
                }

            # 型の正規化
            data["significant_change"] = bool(data["significant_change"])
            data["sentiment"]["overall"] = int(data["sentiment"].get("overall", 0))

            return data

        except json.JSONDecodeError as e:
            print(f"[Grok] JSON parse error: {e}")
            print(f"[Grok] Raw response: {raw[:300]}")
            return None
