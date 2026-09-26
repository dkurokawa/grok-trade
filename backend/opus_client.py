"""Opus 4.6 クライアント - 最終売買判断エンジン"""
import json
import os
import re
import time

import anthropic

OPUS_SYSTEM = """あなたはリスク管理を重視するポートフォリオマネージャーです。
必ずアクションをJSON形式で返してください。
「判断できない」「もっと情報が必要」は禁止です。
buy/sell/holdのいずれかを必ず選択してください。"""

OPUS_PROMPT_TEMPLATE = """== 現在の状態 ==
残高: ${balance}
ポジション: {positions}
本日のP&L: ${daily_pnl}
最大許容損失: ${max_daily_loss}

== 市場データ ==
{price_data}

== Grokセンチメントレポート ==
{grok_report}

以下のJSON形式で判断してください:
{{
  "action": "buy|sell|hold",
  "symbol": "ティッカー",
  "quantity": 数値,
  "order_type": "market|limit",
  "limit_price": null or 数値,
  "stop_loss": 数値,
  "take_profit": 数値,
  "position_size_pct": 資金の何%を使うか (上限50%),
  "reasoning": "判断根拠（3文以内）",
  "risk_assessment": "low|medium|high",
  "confidence": 0-100,
  "adjustments": [
    {{
      "field": "調整した項目名",
      "original": "当初検討した値",
      "adjusted": "最終的な値",
      "reason": "調整理由"
    }}
  ]
}}

adjustments配列: ポジションサイズ縮小・ストップロス追加・レバレッジ抑制など
リスク管理のために調整を行った場合は必ず記録してください。
調整がなければ空配列 [] で構いません。"""


class OpusClient:
    def __init__(self):
        self.client = anthropic.Anthropic(
            api_key=os.getenv("ANTHROPIC_API_KEY")
        )
        self.model = "claude-opus-4-6-20260205"

    def analyze(
        self,
        balance: float,
        positions: list[dict],
        daily_pnl: float,
        max_daily_loss: float,
        price_data: dict,
        grok_report: dict,
    ) -> tuple[dict | None, int]:
        """
        市場データとGrokレポートから売買判断を返す。

        Returns:
            (decision_dict or None, latency_ms)
        """
        prompt = self._build_prompt(
            balance, positions, daily_pnl, max_daily_loss, price_data, grok_report
        )

        start = time.time()
        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=1024,
                system=OPUS_SYSTEM,
                messages=[
                    {"role": "user", "content": prompt},
                    {"role": "assistant", "content": "{"},  # prefill
                ],
            )

            latency_ms = int((time.time() - start) * 1000)
            raw = "{" + response.content[0].text
            decision = parse_opus_response(raw)
            return decision, latency_ms

        except Exception as e:
            latency_ms = int((time.time() - start) * 1000)
            print(f"[Opus] API error: {e}")
            return None, latency_ms

    def _build_prompt(
        self,
        balance: float,
        positions: list[dict],
        daily_pnl: float,
        max_daily_loss: float,
        price_data: dict,
        grok_report: dict,
    ) -> str:
        positions_str = json.dumps(positions, indent=2) if positions else "None"
        price_str = json.dumps(price_data, indent=2)
        grok_str = json.dumps(grok_report, indent=2)

        return OPUS_PROMPT_TEMPLATE.format(
            balance=f"{balance:,.2f}",
            positions=positions_str,
            daily_pnl=f"{daily_pnl:+,.2f}",
            max_daily_loss=f"{max_daily_loss:,.2f}",
            price_data=price_str,
            grok_report=grok_str,
        )


def parse_opus_response(raw: str) -> dict | None:
    """Opusの応答からJSONを抽出"""
    # 方法1: ```json ブロック
    match = re.search(r"```json\s*(.*?)\s*```", raw, re.DOTALL)
    if match:
        try:
            return _validate_decision(json.loads(match.group(1)))
        except json.JSONDecodeError:
            pass

    # 方法2: 最初の { から最後の } まで
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if match:
        try:
            return _validate_decision(json.loads(match.group(0)))
        except json.JSONDecodeError:
            pass

    print(f"[Opus] JSON parse failed: {raw[:200]}")
    return None


REQUIRED_FIELDS = [
    "action", "symbol", "quantity", "reasoning", "confidence",
]


def _validate_decision(data: dict) -> dict | None:
    """判断結果のバリデーションと正規化"""
    for field in REQUIRED_FIELDS:
        if field not in data:
            print(f"[Opus] Missing field: {field}")
            return None

    data["action"] = str(data["action"]).lower()
    if data["action"] not in ("buy", "sell", "hold"):
        print(f"[Opus] Invalid action: {data['action']}")
        return None

    data["quantity"] = int(data["quantity"])
    data["confidence"] = int(data["confidence"])
    data["order_type"] = str(data.get("order_type", "market")).lower()
    data["position_size_pct"] = float(data.get("position_size_pct", 0))
    data["risk_assessment"] = str(data.get("risk_assessment", "medium")).lower()
    data["stop_loss"] = data.get("stop_loss")
    data["take_profit"] = data.get("take_profit")
    data["limit_price"] = data.get("limit_price")
    data["adjustments"] = data.get("adjustments", [])

    return data
