# Grok Trade Bot

Grok AI を活用した自動株式トレーディングボット。Alpaca API でペーパートレード/本番取引を行い、Discord に通知を送信。

## アーキテクチャ

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│   Grok AI   │────▶│  Trade Bot  │────▶│   Alpaca    │
│  (分析判断)   │     │  (Fly.io)   │     │  (取引執行)   │
└─────────────┘     └──────┬──────┘     └─────────────┘
                          │
              ┌───────────┼───────────┐
              ▼           ▼           ▼
        ┌─────────┐ ┌─────────┐ ┌─────────┐
        │ Fly DB  │ │ Discord │ │Dashboard│
        │(Postgres)│ │ (通知)  │ │(Vercel) │
        └─────────┘ └─────────┘ └─────────┘
```

## インフラ構成

| コンポーネント | サービス | URL |
|---------------|---------|-----|
| Backend API | Fly.io | https://grok-trade-bot.fly.dev |
| Database | Fly Postgres | grok-trade-db.flycast |
| Dashboard | Vercel (SSO) | - |
| 通知 | Discord Webhook | trades / alerts チャンネル |

## セットアップ

### 必要な環境変数

```bash
# Alpaca API
ALPACA_API_KEY=xxx
ALPACA_SECRET_KEY=xxx
ALPACA_PAPER=true  # ペーパートレード

# Grok/xAI API
GROK_API_KEY=xxx

# Database
DATABASE_URL=postgres://...

# Discord通知
DISCORD_WEBHOOK_TRADES=https://discord.com/api/webhooks/...
DISCORD_WEBHOOK_ALERTS=https://discord.com/api/webhooks/...

# リスク管理
MAX_DAILY_LOSS=500
MAX_POSITION_RATIO=0.5
TRADING_INTERVAL=15  # 分
```

### ローカル開発

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# テスト実行
pytest tests/ -v

# サーバー起動
uvicorn main:app --reload
```

### デプロイ

```bash
# Fly.io にデプロイ
cd backend
flyctl deploy
```

## API エンドポイント

| メソッド | パス | 説明 |
|---------|------|------|
| GET | `/health` | ヘルスチェック |
| GET | `/status` | アカウント状況・ポジション |
| GET | `/trades` | 取引履歴 |
| GET | `/decisions` | Grok判断履歴 |
| POST | `/start` | スケジューラー開始 |
| POST | `/stop` | スケジューラー停止 |

## CI/CD

GitHub Actions で自動化:

1. **PR作成時**: Lint → Test → Preview Deploy → E2E Test
2. **マージ時**: 本番Deploy → E2E Test → Preview Cleanup
3. **定期実行**: 毎時ヘルスチェック、毎日22:30(JST) E2Eテスト

詳細は [docs/CICD.md](docs/CICD.md) を参照。

## 監視銘柄

- MSTR (MicroStrategy)
- TSLA (Tesla)
- QQQ (Nasdaq ETF)
- SPY (S&P500 ETF)

## リスク管理

- **日次損失上限**: $500 超過でシステム自動停止
- **ポジション比率**: 総資産の50%まで
- **取引間隔**: 15分ごとに市場分析

## ライセンス

Private - 個人利用のみ
