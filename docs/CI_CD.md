# Grok Trade CI/CD パイプライン

## 概要

GitHub ActionsとFly.ioを使用した自動デプロイパイプライン。PRごとにプレビュー環境を作成し、テスト後に本番デプロイを行う。

## パイプラインフロー

```
┌─────────────────────────────────────────────────────────────────┐
│                        PR作成/更新                               │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│  Step 1: Lint & Type Check                                      │
│  ├─ Ruff (Python linter)                                        │
│  └─ MyPy (Type checker)                                         │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│  Step 2: Unit Tests                                             │
│  ├─ pytest (test_production_e2e.py以外)                          │
│  └─ Coverage report → Codecov                                   │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│  Step 3: Deploy Preview (PRのみ)                                 │
│  ├─ Fly.io にプレビューアプリ作成: grok-trade-pr-{PR番号}          │
│  ├─ Secrets設定 (paper trading mode)                            │
│  └─ PRコメントにプレビューURL投稿                                 │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│  Step 4: E2E Tests on Preview                                   │
│  ├─ ヘルスチェック待機 (最大5分)                                  │
│  ├─ pytest test_production_e2e.py                               │
│  ├─ エンドポイント直接テスト (/health, /status, /trades, /decisions)│
│  └─ PRコメント更新 (テスト結果付き)                               │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                        PR マージ                                 │
└─────────────────────────────────────────────────────────────────┘
                              │
              ┌───────────────┴───────────────┐
              ▼                               ▼
┌─────────────────────────┐     ┌─────────────────────────┐
│  Step 5: Deploy Production │     │  Cleanup Preview        │
│  ├─ grok-trade-bot へデプロイ │     │  └─ PR用アプリ削除       │
│  └─ Discord通知            │     │     grok-trade-pr-{N}   │
└─────────────────────────┘     └─────────────────────────┘
              │
              ▼
┌─────────────────────────────────────────────────────────────────┐
│  Step 6: E2E Tests on Production                                │
│  ├─ ヘルスチェック待機                                           │
│  ├─ pytest test_production_e2e.py                               │
│  └─ 失敗時: Discord通知                                          │
└─────────────────────────────────────────────────────────────────┘
```

## ワークフローファイル

### メインCI/CD: `.github/workflows/ci.yml`

| ジョブ | トリガー | 依存関係 | 説明 |
|--------|----------|----------|------|
| `lint` | PR, Push | - | Ruff + MyPy |
| `test` | PR, Push | - | pytest + coverage |
| `deploy-preview` | PR (not closed) | lint, test | Fly.ioプレビュー |
| `e2e-preview` | PR (not closed) | deploy-preview | E2Eテスト |
| `deploy-production` | Push to main | lint, test | 本番デプロイ |
| `e2e-production` | Push to main | deploy-production | 本番E2E |
| `cleanup-preview` | PR closed | - | プレビュー削除 |

### 定期ヘルスチェック: `.github/workflows/scheduled-health-check.yml`

- **スケジュール**: 毎時0分 (cron: `0 * * * *`)
- **対象**: https://grok-trade-bot.fly.dev/health
- **失敗時**: Discord通知

## 環境変数・Secrets

### GitHub Secrets (必須)

| Secret | 説明 |
|--------|------|
| `FLY_API_TOKEN` | Fly.io org-level token |
| `ALPACA_API_KEY` | Alpaca API key |
| `ALPACA_SECRET_KEY` | Alpaca secret key |
| `GROK_API_KEY` | Grok API key |
| `DATABASE_URL` | Fly Postgres URL |
| `DISCORD_WEBHOOK_TRADES` | 取引通知用Webhook |
| `DISCORD_WEBHOOK_ALERTS` | アラート用Webhook |
| `MAX_DAILY_LOSS` | 日次損失上限 |
| `MAX_POSITION_RATIO` | ポジション比率上限 |
| `TRADING_INTERVAL` | 取引間隔(秒) |

### CIで使用されるテスト用環境変数

```yaml
env:
  DATABASE_URL: "sqlite:///:memory:"
  ALPACA_API_KEY: test_key
  ALPACA_SECRET_KEY: test_secret
  ALPACA_PAPER: "true"
  GROK_API_KEY: test_grok_key
  MAX_DAILY_LOSS: "500"
  MAX_POSITION_RATIO: "0.5"
```

## Fly.io設定

### 本番環境

- **アプリ名**: `grok-trade-bot`
- **URL**: https://grok-trade-bot.fly.dev
- **リージョン**: `nrt` (東京)
- **VM**: `shared-cpu-1x`, 512MB RAM
- **常時起動**: `auto_stop_machines = 'off'`

### プレビュー環境

- **アプリ名**: `grok-trade-pr-{PR番号}`
- **URL**: https://grok-trade-pr-{PR番号}.fly.dev
- **設定**: 本番と同じ (paper trading mode)
- **ライフサイクル**: PR closeで自動削除

## PRコメント例

```markdown
## 🚀 Preview Deployment

**URL:** https://grok-trade-pr-123.fly.dev

### Endpoint Tests
| Endpoint | Status | Link |
|----------|--------|------|
| /health | ✅ | [Open](https://grok-trade-pr-123.fly.dev/health) |
| /status | ✅ | [Open](https://grok-trade-pr-123.fly.dev/status) |
| /trades | ✅ | [Open](https://grok-trade-pr-123.fly.dev/trades) |
| /decisions | ✅ | [Open](https://grok-trade-pr-123.fly.dev/decisions) |

### E2E Test Results
✅ All tests passed (5 passed, 0 failed)

> ⚠️ This preview uses **paper trading** mode.
> Preview will be deleted when PR is closed.
```

## トラブルシューティング

### FLY_API_TOKEN エラー

```
Error: the config for your app is missing an app name
```

**原因**: Deploy-scopedトークンでは新規アプリ作成不可

**解決**: Org-levelトークンを生成
```bash
flyctl tokens create org personal
```

### Permission denied (PRコメント)

```
Resource not accessible by integration
```

**解決**: ワークフローに権限追加
```yaml
permissions:
  contents: read
  pull-requests: write
```

### データベース接続エラー

プレビュー環境がDB接続できない場合、secretsが正しく設定されているか確認:
```bash
flyctl secrets list --app grok-trade-pr-{N}
```

## ローカル開発

```bash
# 依存関係インストール
cd backend
pip install -r requirements.txt

# テスト実行
pytest tests/ --ignore=tests/test_production_e2e.py -v

# E2Eテスト (本番URLに対して)
PRODUCTION_API_URL=https://grok-trade-bot.fly.dev pytest tests/test_production_e2e.py -v

# Lint
ruff check .
mypy . --ignore-missing-imports
```

## 参考リンク

- [Fly.io Documentation](https://fly.io/docs/)
- [GitHub Actions Documentation](https://docs.github.com/en/actions)
- [Codecov](https://codecov.io/)
