# Grok Trade CI/CD パイプライン

## 概要

GitHub Actions と AWS SAM による自動デプロイパイプライン。PR ではテンプレート検証と
イメージビルドまでを行い、`main` へのマージで本番へデプロイする。

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
│  └─ MyPy (Type checker)          ※どちらも continue-on-error     │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│  Step 2: Unit Tests                                             │
│  ├─ pytest (test_production_e2e.py以外)                          │
│  ├─ DynamoDB は moto でモック（AWS 接続不要）                     │
│  ├─ カバレッジ閾値 60%（未満で失敗）                              │
│  └─ Coverage report → Codecov                                   │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│  Step 3: SAM Validate & Build                                   │
│  ├─ sam validate --lint （テンプレート検証）                      │
│  └─ sam build          （Lambda パッケージのビルド）              │
└─────────────────────────────────────────────────────────────────┘
                              │
                     main へ push した場合のみ
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│  Step 4: Deploy (AWS SAM)                                       │
│  ├─ OIDC で AWS の IAM ロールを引き受け                           │
│  ├─ sam deploy （CloudFormation スタック更新）                    │
│  ├─ Function URL をスタック出力から取得                           │
│  └─ /health へスモークテスト（最大5回リトライ）                    │
└─────────────────────────────────────────────────────────────────┘
```

## 定期実行 (scheduled-health-check.yml)

| スケジュール | 内容 |
|---|---|
| 毎時 | `/health` の応答確認 + 取引 Lambda が直近1時間に実行されたかを CloudWatch で確認（取引サイクルは市場時間中のみ動くため、この確認が意味を持つのは平日日中） |
| 平日 22:30 JST | 本番 E2E テスト (`test_production_e2e.py`) |

いずれも失敗時は Discord に通知する。

**毎時チェックが「Lambda の実行実績」も見る理由**: API が 200 を返していても、
EventBridge Scheduler が止まっていれば取引は行われない。API の生死と
取引サイクルの生死は別物なので、両方を確認する。

## 必要な GitHub シークレット

| シークレット | 用途 |
|---|---|
| `AWS_DEPLOY_ROLE_ARN` | OIDC で引き受けるデプロイ用 IAM ロールの ARN |
| `DISCORD_WEBHOOK_ALERTS` | 失敗通知の送信先 |

Alpaca / Grok / Discord の API キーは GitHub ではなく **SSM Parameter Store** に置き、
Lambda が起動時に読み込む（[AWS_DEPLOY.md](AWS_DEPLOY.md) 参照）。

## 旧構成（Fly.io）からの変更点

| 旧 | 新 | 理由 |
|---|---|---|
| PR ごとに Fly プレビューアプリを作成 → E2E → クリーンアップ | PR では `sam validate` + `sam build` のみ | PR ごとの実クラウド環境は AWS では構築コストが高く、常時課金の要因にもなるため。テンプレートとイメージのビルド検証で回帰は捕捉できる |
| `flyctl deploy` | `sam deploy` | — |
| Fly secrets | SSM Parameter Store | — |
| 固定 URL (`grok-trade-bot.fly.dev`) | スタック出力から Function URL を取得 | Function URL はデプロイ時に払い出されるため |

PR 単位で実環境の E2E を再開したい場合は、`sam deploy --stack-name grok-trade-pr-<番号>`
で一時スタックを作り、PR クローズ時に `aws cloudformation delete-stack` する構成を
追加すればよい。
