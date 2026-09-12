# Grok Trade Bot

Grok と Claude Opus を組み合わせた自動株式トレーディングボット。Alpaca API でペーパートレード/本番取引を行い、Discord に通知を送信。

## アーキテクチャ

AWS サーバーレス構成。取引サイクルは EventBridge Scheduler が市場時間中だけ起動する。

```
EventBridge Scheduler ──30分ごと──▶ Lambda[trading]
  (平日 9:00-15:30 ET)                  │
                                        │  Stage 1: Grok    市場センチメント収集（判断しない）
                                        │      ↓  変化が小さければ以降をスキップ
                                        │  Stage 2: Grok / Opus 4.6   売買判断
                                        │      ↓
                                        │  Stage 3: Risk Guard        判断をルールで検証・縮小
                                        │      ↓
                                        │  Stage 4: Alpaca            発注（損切り・利確付き）
                                        │
                                        ├──▶ DynamoDB (パイプラインログ・取引履歴・稼働フラグ)
                                        └──▶ Discord (ステージごとに通知)

EventBridge Scheduler ──5分ごと───▶ Lambda[trading] (task=emergency_check)
                                        └── ドローダウン5%超で全ポジション清算し停止

Dashboard (Vercel) ──HTTPS──▶ Lambda Function URL ──▶ Lambda[api] ──▶ DynamoDB
                                                       (FastAPI + Mangum)
```

### 判断エンジンの切り替え

`DECISION_ENGINE` で Stage 2 を切り替える。どちらも同じ形式の判断を返すので、
Risk Guard 以降の処理は変わらない。

| 値 | Stage 2 | 追加で必要なキー |
|---|---|---|
| `grok`（既定） | Grok が自身のレポートから直接判断 | なし |
| `opus` | Claude Opus 4.6 が Grok のレポートを読んで判断 | `ANTHROPIC_API_KEY` |

## インフラ構成

| コンポーネント | サービス | 備考 |
|---|---|---|
| 取引サイクル / 緊急チェック | Lambda `grok-trade-trading` | EventBridge Scheduler が `task` を渡して起動 |
| API | Lambda `grok-trade-api` + Function URL | FastAPI を Mangum でラップ |
| Database | DynamoDB `grok-trade` (on-demand) | 単一テーブル設計 |
| シークレット | SSM Parameter Store (SecureString) | `/grok-trade/*` |
| エラー監視 | Sentry | `SENTRY_DSN` 未設定なら無効 |
| Dashboard | Vercel | `NEXT_PUBLIC_API_URL` に Function URL を設定 |
| 通知 | Discord Webhook | trades / alerts チャンネル |

リージョンは `ap-northeast-1`（東京）。Lambda・EventBridge・DynamoDB とも無料枠内で収まる。

## セットアップ

### 必要な環境変数

Lambda 上では SSM Parameter Store から自動で読み込まれる（`config.py`）。
ローカル開発では `.env` に設定する。

```bash
# Alpaca API
ALPACA_API_KEY=xxx
ALPACA_SECRET_KEY=xxx
ALPACA_PAPER=true          # ペーパートレード
ALPACA_DATA_FEED=iex       # 無料プランは iex のみ。有料プランなら sip

# Grok/xAI API
GROK_API_KEY=xxx
GROK_MODEL=grok-3-mini

# Claude Opus（DECISION_ENGINE=opus のときだけ必要）
ANTHROPIC_API_KEY=xxx
DECISION_ENGINE=grok

# Discord通知
DISCORD_WEBHOOK_TRADES=https://discord.com/api/webhooks/...
DISCORD_WEBHOOK_ALERTS=https://discord.com/api/webhooks/...

# API の /start /stop を保護する共有シークレット
API_SHARED_SECRET=xxx

# リスク管理
MAX_DAILY_LOSS=500
MAX_POSITION_RATIO=0.5
MAX_SINGLE_TRADE_PCT=0.25
MIN_CONFIDENCE=40

# DynamoDB / 監視
DDB_TABLE=grok-trade
SENTRY_DSN=                # 空なら Sentry 無効
```

### ローカル開発

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install pytest pytest-asyncio pytest-cov 'moto[dynamodb]'

# テスト実行（DynamoDB は moto でモックされるので AWS 接続不要）
pytest tests/ --ignore=tests/test_production_e2e.py -v

# API をローカル起動
uvicorn app:app --reload --port 8000
```

### デプロイ

```bash
./scripts/deploy.sh
```

前提確認 → シークレット登録 → ビルド → デプロイ → 疎通確認 までを行う。
再実行しても安全で、済んでいる手順はスキップされる。Docker は不要。

詳細は [docs/AWS_DEPLOY.md](docs/AWS_DEPLOY.md) を参照。

## API エンドポイント

| メソッド | パス | 認証 | 説明 |
|---------|------|------|------|
| GET | `/health` | - | ヘルスチェック（稼働状態・判断エンジン・不足シークレット） |
| GET | `/status` | - | アカウント状況・ポジション |
| GET | `/trades` | - | 取引履歴 |
| GET | `/decisions` | - | 判断サマリー |
| GET | `/pipeline` | - | パイプライン全ステージのログ |
| POST | `/start` | `x-api-key` | 取引サイクル再開 |
| POST | `/stop` | `x-api-key` | 取引サイクル停止 |

`/start` `/stop` は `x-api-key` ヘッダに `API_SHARED_SECRET` を要求する。

## CI/CD

GitHub Actions で自動化:

1. **PR作成時**: Lint → Test → SAM Validate & Build
2. **main へ push**: Lint → Test → Build → SAM Deploy → スモークテスト
3. **定期実行**: 毎時ヘルスチェック（API 応答 + 取引 Lambda の実行実績を確認）、
   毎日22:30(JST) E2Eテスト

詳細は [docs/CI_CD.md](docs/CI_CD.md) を参照。

## 監視銘柄

- MSTR (MicroStrategy)
- TSLA (Tesla)
- QQQ (Nasdaq ETF)
- SPY (S&P500 ETF)

## リスク管理

Stage 3 の Risk Guard が、AI の判断をルールで検証する最後の砦になる。

- **日次損失上限**: $500 超過でシステム自動停止（DynamoDB の稼働フラグを false にする）
- **確信度**: `MIN_CONFIDENCE`（既定40）未満の判断はブロック
- **ストップロス必須**: 損切り価格のない買いはブロック
- **ポジション上限**: 総資産の50%、1取引あたり25%まで自動縮小
- **緊急停止**: 5分ごとに監視し、ドローダウン5%超で全ポジション清算
- **取引間隔**: 市場時間中30分ごとに分析

## ライセンス

Private - 個人利用のみ
