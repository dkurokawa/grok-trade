# Grok → Opus 4.6 → Alpaca 自動売買パイプライン 設計書

## 確定事項

| 項目 | 内容 |
|------|------|
| インフラ | Fly.io |
| AI Stage 1 | Grok turbo（情報収集・センチメント） |
| AI Stage 2 | Claude Opus 4.6（判断・リスク管理） |
| 発注 | Alpaca MCP Server |
| 判断頻度 | 30分間隔（分析を仕込み、頻度アップを検討） |
| 通知・ログ | Discord（調整ログ含む） |
| コスト節約 | Grokが「変化なし」→ Opus スキップ |

---

## アーキテクチャ

```
┌──────────────────────────────────────────────────────┐
│  Fly.io                                               │
│                                                       │
│  ┌─────────────────────────────────────────────┐     │
│  │  Stage 1: Grok turbo                        │     │
│  │  役割: 情報収集のみ（判断しない）             │     │
│  │  - Xリアルタイムセンチメント解析              │     │
│  │  - 速報・ニュース要約                         │     │
│  │  - 市場コンテキスト報告                       │     │
│  │                                              │     │
│  │  出力: MarketReport (JSON)                   │     │
│  └──────────────────┬──────────────────────────┘     │
│                     │                                 │
│            変化なし? ─── Yes ──→ hold（Opusスキップ） │
│                │                                      │
│               No                                      │
│                ▼                                      │
│  ┌─────────────────────────────────────────────┐     │
│  │  Stage 2: Claude Opus 4.6                   │     │
│  │  役割: 最終判断                               │     │
│  │  - ファンダメンタル・テクニカル分析            │     │
│  │  - リスク評価                                 │     │
│  │  - ポジションサイジング（調整時はログに記録）  │     │
│  │  - 売買判断                                   │     │
│  │                                              │     │
│  │  出力: TradeDecision (JSON)                  │     │
│  └──────────────────┬──────────────────────────┘     │
│                     ▼                                 │
│  ┌─────────────────────────────────────────────┐     │
│  │  Stage 3: Risk Guard（ルールベース）          │     │
│  │  - 日次損失上限チェック                       │     │
│  │  - ポジション集中度チェック                   │     │
│  │  - 全額投入の抑制（Opusが提案してもブロック） │     │
│  │  - ブロック時はDiscordにログ                  │     │
│  └──────────────────┬──────────────────────────┘     │
│                     ▼                                 │
│  ┌─────────────────────────────────────────────┐     │
│  │  Stage 4: Alpaca MCP → 発注                  │     │
│  └─────────────────────────────────────────────┘     │
│                                                       │
│  ┌─────────────────────────────────────────────┐     │
│  │  PostgreSQL (Fly.io)                         │     │
│  │  - パイプラインログ（cycle_id で全体追跡）    │     │
│  │  - 取引履歴                                   │     │
│  │  - 日次サマリー                               │     │
│  └─────────────────────────────────────────────┘     │
│                                                       │
│  ┌─────────────────────────────────────────────┐     │
│  │  Dashboard (Next.js on Vercel)               │     │
│  │  - パイプライン全体の可視化                   │     │
│  │  - Grokレポート → Opus判断 の流れが見える     │     │
│  │  - 緊急停止ボタン                             │     │
│  └─────────────────────────────────────────────┘     │
└──────────────────────────────────────────────────────┘
```

---

## Stage 1: Grok プロンプト

**役割を「情報収集のみ」に限定する。** 判断させない。

```python
GROK_SYSTEM = """
あなたは市場情報アナリストです。
事実の報告のみ行ってください。売買の推奨は絶対にしないでください。
"""

GROK_PROMPT = """
以下のJSON形式で市場状況を報告してください。

{
  "timestamp": "EST時刻",
  "significant_change": true/false,
  "sentiment": {
    "overall": -100〜+100,
    "trending_tickers": [
      {"symbol": "NVDA", "reason": "決算発表後の反応"}
    ],
    "notable_signals": ["影響力のある動き・投稿の要約"]
  },
  "breaking_news": ["直近の重要ニュース（なければ空配列）"],
  "market_context": {
    "spy_trend": "bullish/bearish/neutral",
    "vix_level": "low/moderate/high/extreme",
    "sector_rotation": "どのセクターに資金が動いているか"
  }
}

重要: significant_change は以下の場合のみ true にしてください:
- センチメントが前回から大きく変動（±30以上）
- 重要ニュースがある
- VIXが急変
それ以外は false にしてください。
"""
```

### Opusスキップ条件

```python
def should_skip_opus(grok_report: dict) -> bool:
    """Grokが変化なしと判断 → Opus呼び出しをスキップ"""
    if not grok_report.get("significant_change", False):
        return True
    if (abs(grok_report["sentiment"]["overall"]) < 20
        and len(grok_report["breaking_news"]) == 0):
        return True
    return False
```

---

## Stage 2: Opus 4.6 プロンプト

```python
OPUS_SYSTEM = """
あなたはリスク管理を重視するポートフォリオマネージャーです。
必ずアクションをJSON形式で返してください。
「判断できない」「もっと情報が必要」は禁止です。
buy/sell/holdのいずれかを必ず選択してください。
"""

OPUS_PROMPT = """
== 現在の状態 ==
残高: ${balance}
ポジション: {positions}
本日のP&L: ${daily_pnl}
最大許容損失: ${max_daily_loss}

== 市場データ ==
{price_data}

== Grokセンチメントレポート ==
{grok_report}

以下のJSON形式で判断してください:
{
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
    {
      "field": "調整した項目名",
      "original": "当初検討した値",
      "adjusted": "最終的な値",
      "reason": "調整理由"
    }
  ]
}

adjustments配列: ポジションサイズ縮小・ストップロス追加・レバレッジ抑制など
リスク管理のために調整を行った場合は必ず記録してください。
調整がなければ空配列 [] で構いません。
"""
```

### 出力パーサー

Opusは前置きを付けがち。JSONブロック抽出を堅牢にする。

```python
import json
import re

def parse_opus_response(raw: str) -> dict:
    """Opusの応答からJSONを抽出"""
    # 方法1: ```json ブロック
    match = re.search(r'```json\s*(.*?)\s*```', raw, re.DOTALL)
    if match:
        return json.loads(match.group(1))
    
    # 方法2: 最初の { から最後の } まで
    match = re.search(r'\{.*\}', raw, re.DOTALL)
    if match:
        return json.loads(match.group(0))
    
    raise ValueError(f"JSON parse failed: {raw[:200]}")
```

**Anthropic API tips:** assistant prefill で `{` を先頭に置くとJSON直出力を強制できる。

```python
response = client.messages.create(
    model="claude-opus-4-6-20260205",
    messages=[
        {"role": "user", "content": opus_prompt},
        {"role": "assistant", "content": "{"}  # prefill
    ]
)
result = json.loads("{" + response.content[0].text)
```

---

## Stage 3: Risk Guard

ルールベース。AIの判断をオーバーライドする最後の砦。

```python
class RiskGuard:
    def __init__(self, config):
        self.max_daily_loss = config["max_daily_loss"]       # 例: $500
        self.max_position_pct = config["max_position_pct"]   # 例: 50%
        self.max_single_trade_pct = config["max_single_trade_pct"]  # 例: 25%
        self.min_stop_loss = config["min_stop_loss"]         # 例: 必須
    
    def check(self, decision: dict, portfolio: dict) -> tuple[bool, str, list]:
        """Returns (passed, reason, adjustments)"""
        adjustments = []
        
        # 日次損失上限
        if portfolio["daily_pnl"] <= -self.max_daily_loss:
            return False, "daily_loss_limit_reached", []
        
        # ポジションサイズ制限
        if decision["position_size_pct"] > self.max_position_pct:
            old = decision["position_size_pct"]
            decision["position_size_pct"] = self.max_position_pct
            adjustments.append({
                "field": "position_size_pct",
                "original": old,
                "adjusted": self.max_position_pct,
                "reason": f"Risk Guard: 上限{self.max_position_pct}%に制限"
            })
        
        # ストップロス必須
        if decision["action"] in ("buy", "sell") and not decision.get("stop_loss"):
            return False, "stop_loss_required", []
        
        # confidence低すぎ
        if decision.get("confidence", 0) < 40:
            return False, f"confidence_too_low: {decision['confidence']}", []
        
        return True, "passed", adjustments
```

---

## Discordログ設計

**全ての調整をDiscordに通知する。** Opusの自己調整 + Risk Guardの調整の両方。

```python
async def send_discord_log(cycle_id: str, stage: str, data: dict):
    """Discord Webhookにログ送信"""
    
    embed = {
        "title": f"🔄 Cycle {cycle_id[:8]}",
        "fields": []
    }
    
    if stage == "grok":
        embed["color"] = 0x1DA1F2  # Twitter blue
        embed["fields"].append({
            "name": "Grok Report",
            "value": (
                f"Sentiment: {data['sentiment']['overall']}\n"
                f"Change: {'🔴 Yes' if data['significant_change'] else '⚪ No'}\n"
                f"News: {len(data.get('breaking_news', []))} items"
            )
        })
    
    elif stage == "opus_decision":
        embed["color"] = 0xD97706  # Opus orange
        action_emoji = {"buy": "🟢", "sell": "🔴", "hold": "⚪"}
        embed["fields"].append({
            "name": "Opus Decision",
            "value": (
                f"{action_emoji.get(data['action'], '❓')} "
                f"{data['action'].upper()} {data.get('symbol', '')}\n"
                f"Size: {data.get('position_size_pct', 0)}% | "
                f"Confidence: {data.get('confidence', 0)}%\n"
                f"Risk: {data.get('risk_assessment', 'unknown')}\n"
                f"Reason: {data.get('reasoning', '')}"
            )
        })
        
        # Opusの自己調整ログ
        if data.get("adjustments"):
            adj_text = "\n".join([
                f"⚖️ {a['field']}: {a['original']} → {a['adjusted']} ({a['reason']})"
                for a in data["adjustments"]
            ])
            embed["fields"].append({
                "name": "Opus Self-Adjustments",
                "value": adj_text
            })
    
    elif stage == "risk_guard":
        if not data["passed"]:
            embed["color"] = 0xEF4444  # Red
            embed["fields"].append({
                "name": "🚫 Risk Guard BLOCKED",
                "value": data["reason"]
            })
        elif data.get("adjustments"):
            embed["color"] = 0xF59E0B  # Yellow
            adj_text = "\n".join([
                f"🛡️ {a['field']}: {a['original']} → {a['adjusted']} ({a['reason']})"
                for a in data["adjustments"]
            ])
            embed["fields"].append({
                "name": "Risk Guard Adjustments",
                "value": adj_text
            })
    
    elif stage == "execution":
        embed["color"] = 0x22C55E  # Green
        embed["fields"].append({
            "name": "✅ Executed",
            "value": (
                f"Order: {data.get('alpaca_order_id', 'N/A')}\n"
                f"Status: {data.get('status', 'unknown')}"
            )
        })
    
    elif stage == "skip":
        embed["color"] = 0x6B7280  # Gray
        embed["fields"].append({
            "name": "⏭️ Opus Skipped",
            "value": "Grok: no significant change"
        })

    await send_webhook(embed)
```

### Discord通知の流れ（例）

```
⚪ Cycle a3f2... | Grok Report
   Sentiment: +12 | Change: No | News: 0 items
   → ⏭️ Opus Skipped: no significant change

🔴 Cycle b7e1... | Grok Report  
   Sentiment: +67 | Change: Yes | News: 2 items
   → 🟢 Opus Decision: BUY NVDA
     Size: 35% | Confidence: 72% | Risk: medium
     Reason: センチメント急上昇、決算後の買い圧力継続
   → ⚖️ Opus Self-Adjustments:
     position_size_pct: 50% → 35% (VIX上昇中のためサイズ縮小)
     stop_loss: なし → $118.50 (直近サポートライン)
   → ✅ Executed: Order abc123
   
🔴 Cycle c9d3... | Opus Decision: BUY TSLA
   Size: 60% | Confidence: 55%
   → 🛡️ Risk Guard Adjustments:
     position_size_pct: 60% → 50% (上限50%に制限)
   → ✅ Executed (adjusted)

🔴 Cycle d4e5... | Opus Decision: BUY SPY
   Confidence: 35%
   → 🚫 Risk Guard BLOCKED: confidence_too_low: 35
```

---

## Grokハルシネーション対策

Grokが存在しないニュースやティッカーを報告する可能性がある。Opusに渡す前にチェック。

```python
VALID_UNIVERSE = {"SPY", "QQQ", "NVDA", "TSLA", "MSFT", "AAPL", 
                  "AMZN", "GOOGL", "META", "PLTR", ...}

def validate_grok_report(report: dict) -> dict:
    # 不明なティッカーを除外
    tickers = report.get("sentiment", {}).get("trending_tickers", [])
    report["sentiment"]["trending_tickers"] = [
        t for t in tickers if t.get("symbol") in VALID_UNIVERSE
    ]
    
    # 極端なセンチメントに警告フラグ
    if abs(report.get("sentiment", {}).get("overall", 0)) > 90:
        report["_warning"] = "extreme_sentiment_may_be_hallucination"
    
    return report
```

---

## DBスキーマ

```sql
-- パイプライン全体ログ（1判断サイクル = 1行）
CREATE TABLE pipeline_log (
    id SERIAL PRIMARY KEY,
    cycle_id UUID NOT NULL,
    timestamp TIMESTAMPTZ DEFAULT NOW(),
    
    -- Stage 1: Grok
    grok_input JSONB,
    grok_output JSONB,
    grok_latency_ms INT,
    opus_skipped BOOLEAN DEFAULT FALSE,
    
    -- Stage 2: Opus
    opus_input JSONB,
    opus_output JSONB,
    opus_latency_ms INT,
    opus_adjustments JSONB DEFAULT '[]',
    
    -- Stage 3: Risk Guard
    risk_guard_passed BOOLEAN,
    risk_guard_reason TEXT,
    risk_guard_adjustments JSONB DEFAULT '[]',
    
    -- Stage 4: Execution
    order_submitted BOOLEAN DEFAULT FALSE,
    alpaca_order_id VARCHAR(100),
    execution_result JSONB
);

-- 取引ログ
CREATE TABLE trades (
    id SERIAL PRIMARY KEY,
    cycle_id UUID REFERENCES pipeline_log(cycle_id),
    timestamp TIMESTAMPTZ DEFAULT NOW(),
    symbol VARCHAR(50),
    action VARCHAR(10),
    quantity DECIMAL,
    price DECIMAL,
    order_type VARCHAR(20),
    stop_loss DECIMAL,
    take_profit DECIMAL,
    status VARCHAR(20),
    alpaca_order_id VARCHAR(100)
);

-- 日次サマリー
CREATE TABLE daily_summary (
    date DATE PRIMARY KEY,
    starting_balance DECIMAL,
    ending_balance DECIMAL,
    pnl DECIMAL,
    trade_count INT,
    opus_calls INT,
    opus_skips INT,
    risk_guard_blocks INT,
    api_cost_estimate DECIMAL
);

-- システム状態（緊急停止用）
CREATE TABLE system_state (
    key VARCHAR(50) PRIMARY KEY,
    value JSONB,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE UNIQUE INDEX idx_pipeline_cycle ON pipeline_log(cycle_id);
```

---

## メインループ

```python
import asyncio
import uuid

async def trading_cycle():
    cycle_id = str(uuid.uuid4())
    
    # Stage 1: Grok
    market_data = await get_market_snapshot()
    grok_start = time.time()
    grok_report = await grok_client.analyze(market_data)
    grok_latency = int((time.time() - grok_start) * 1000)
    
    grok_report = validate_grok_report(grok_report)
    await send_discord_log(cycle_id, "grok", grok_report)
    
    # Opusスキップ判定
    if should_skip_opus(grok_report):
        await log_to_db(cycle_id, grok=grok_report, opus_skipped=True)
        await send_discord_log(cycle_id, "skip", {})
        return
    
    # Stage 2: Opus
    portfolio = await get_portfolio_state()
    opus_input = build_opus_input(portfolio, market_data, grok_report)
    
    opus_start = time.time()
    opus_raw = await opus_client.analyze(opus_input)
    opus_latency = int((time.time() - opus_start) * 1000)
    
    decision = parse_opus_response(opus_raw)
    await send_discord_log(cycle_id, "opus_decision", decision)
    
    # Stage 3: Risk Guard
    passed, reason, rg_adjustments = risk_guard.check(decision, portfolio)
    
    if rg_adjustments:
        await send_discord_log(cycle_id, "risk_guard", {
            "passed": True, "adjustments": rg_adjustments
        })
    
    if not passed:
        await send_discord_log(cycle_id, "risk_guard", {
            "passed": False, "reason": reason
        })
        await log_to_db(cycle_id, grok=grok_report, opus=decision,
                       rg_passed=False, rg_reason=reason)
        return
    
    # Stage 4: Execute
    if decision["action"] != "hold":
        result = await execute_via_alpaca(decision)
        await send_discord_log(cycle_id, "execution", result)
        await log_to_db(cycle_id, grok=grok_report, opus=decision,
                       rg_passed=True, rg_adjustments=rg_adjustments,
                       execution=result)
    else:
        await log_to_db(cycle_id, grok=grok_report, opus=decision,
                       rg_passed=True, rg_reason="hold")

async def emergency_check():
    """30分サイクルとは別に、高頻度でドローダウン監視"""
    portfolio = await get_portfolio_state()
    if portfolio["daily_drawdown_pct"] > 5:
        await execute_emergency_liquidation()
        await send_discord_log("EMERGENCY", "risk_guard", {
            "passed": False,
            "reason": f"EMERGENCY STOP: drawdown {portfolio['daily_drawdown_pct']}%"
        })

# メインスケジューラ
async def main():
    scheduler = AsyncIOScheduler(timezone="America/New_York")
    
    # 30分間隔（市場時間のみ）
    scheduler.add_job(trading_cycle, 'cron',
                      day_of_week='mon-fri',
                      hour='9-15', minute='0,30')
    # 最後の判断（15:30）
    scheduler.add_job(trading_cycle, 'cron',
                      day_of_week='mon-fri',
                      hour='15', minute='30')
    
    # 緊急チェック（5分間隔）
    scheduler.add_job(emergency_check, 'cron',
                      day_of_week='mon-fri',
                      hour='9-15', minute='*/5')
    
    scheduler.start()
```

---

## コスト見積もり（30分間隔）

| 項目 | 単価 | 1日（13回） | 月（22日） |
|------|------|------------|-----------|
| Grok turbo | ~$0.01/call | $0.13 | $2.86 |
| Opus 4.6 | ~$0.15-0.30/call | $1-2（スキップ込） | $22-44 |
| Fly.io | - | - | $5-10 |
| Fly.io PostgreSQL | - | - | $5 |
| **合計** | | | **$35-62/月** |

Opusスキップが効けば、実際のOpus呼び出しは1日5-8回程度になるはず。

---

## 既存Grokシステムからの変更一覧

| 対象 | 変更内容 |
|------|---------|
| Grokプロンプト | 「判断＋発注指示」→「情報収集のみ、判断禁止」に書き換え |
| 新規追加 | Opus 4.6クライアント + プロンプト |
| 新規追加 | Opusスキップ判定ロジック |
| 新規追加 | Opus出力パーサー（prefill対応） |
| 新規追加 | Grokハルシネーションチェッカー |
| Risk Guard | confidence + risk_assessment チェック追加 |
| Discord通知 | 全ステージのログ出力（調整ログ含む） |
| DB | pipeline_log テーブル追加（cycle_id追跡） |
| スケジューラ | 15分 → 30分に変更 |
| 緊急停止 | 5分間隔のドローダウン監視（AI不要） |
| インフラ | Railway → Fly.io |
| ダッシュボード | パイプライン全体の可視化に拡張 |

---

## 段階的移行

**Week 1:** Grokプロンプト書き換え + Opusクライアント追加 + ペーパートレードで疎通確認

**Week 2:** Discord通知・パイプラインログ実装 + Risk Guard拡張

**Week 3:** ペーパートレードで30分間隔運用開始 + ダッシュボード拡張

**Week 4:** 分析 → 頻度・パラメータ調整 → 本番移行判断
