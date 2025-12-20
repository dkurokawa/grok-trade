"""Grok API クライアント - 市場分析と売買判断"""
import os
import json
from openai import OpenAI
from typing import Optional


class GrokClient:
    def __init__(self):
        self.client = OpenAI(
            api_key=os.getenv("GROK_API_KEY"),
            base_url="https://api.x.ai/v1"
        )
        self.model = "grok-3-mini"  # 高速・低コスト

    def analyze_market(
        self,
        balance: float,
        positions: list[dict],
        market_data: dict
    ) -> Optional[dict]:
        """
        市場分析して売買判断を返す

        Returns:
            {
                "action": "buy" | "sell" | "hold",
                "symbol": "MSTR",
                "quantity": 10,
                "order_type": "market" | "limit",
                "reasoning": "理由",
                "confidence": 0-100
            }
        """
        prompt = self._build_prompt(balance, positions, market_data)

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are a trading analyst. "
                            "Always respond with valid JSON only. "
                            "No markdown, no explanation outside JSON."
                        )
                    },
                    {"role": "user", "content": prompt}
                ],
                max_tokens=500,
                temperature=0.3  # 一貫性重視
            )

            raw_response = response.choices[0].message.content
            return self._parse_response(raw_response)

        except Exception as e:
            print(f"[Grok] API error: {e}")
            return None

    def _build_prompt(
        self,
        balance: float,
        positions: list[dict],
        market_data: dict
    ) -> str:
        """プロンプト構築"""
        positions_str = json.dumps(positions, indent=2) if positions else "None"
        market_str = json.dumps(market_data, indent=2)

        return f"""
Current Portfolio:
- Cash Balance: ${balance:,.2f}
- Positions: {positions_str}

Market Data (last 5 days):
{market_str}

Trading Strategy:
- Focus on MSTR, TSLA, QQQ, SPY
- BTC bullish bias (MSTR preferred)
- Risk tolerance: HIGH
- Hold period: Days to weeks

Analyze and provide trading decision in this exact JSON format:
{{
  "action": "buy" | "sell" | "hold",
  "symbol": "SYMBOL",
  "quantity": number,
  "order_type": "market",
  "reasoning": "brief reason",
  "confidence": 0-100
}}

If no action needed, use "action": "hold" with quantity: 0.
"""

    def _parse_response(self, raw: str) -> Optional[dict]:
        """レスポンスをパース"""
        try:
            # JSONブロックを抽出（```json ... ``` 対応）
            if "```" in raw:
                start = raw.find("{")
                end = raw.rfind("}") + 1
                raw = raw[start:end]

            data = json.loads(raw)

            # 必須フィールド検証
            required = ["action", "symbol", "quantity", "reasoning", "confidence"]
            for field in required:
                if field not in data:
                    print(f"[Grok] Missing field: {field}")
                    return None

            # 値の正規化
            data["action"] = data["action"].lower()
            data["quantity"] = int(data["quantity"])
            data["confidence"] = int(data["confidence"])
            data["order_type"] = data.get("order_type", "market").lower()

            return data

        except json.JSONDecodeError as e:
            print(f"[Grok] JSON parse error: {e}")
            print(f"[Grok] Raw response: {raw}")
            return None


# テスト用
if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()

    client = GrokClient()

    # テストデータ
    result = client.analyze_market(
        balance=100000,
        positions=[],
        market_data={
            "MSTR": {"price": 350, "change_5d": "+5%"},
            "TSLA": {"price": 250, "change_5d": "-2%"},
            "QQQ": {"price": 400, "change_5d": "+1%"}
        }
    )

    print("Analysis Result:")
    print(json.dumps(result, indent=2))
