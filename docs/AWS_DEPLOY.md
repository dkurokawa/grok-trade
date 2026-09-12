# AWS 移行 / デプロイ手順

Fly.io の常駐コンテナ構成から、AWS のサーバーレス構成へ移行した。

## 構成

```
EventBridge Scheduler ──30分ごと（平日 9:00-15:30 ET）──> Lambda[grok-trade-trading] ──┐
                     └─5分ごと（task=emergency_check）──>                              │
                                                                                       ├──> DynamoDB (on-demand)
Dashboard ──HTTPS──> Lambda Function URL ──> Lambda[grok-trade-api] ───────────────────┘
                                            (FastAPI + Mangum)

パイプライン: Grok（情報収集）→ Grok または Opus 4.6（判断）→ Risk Guard → Alpaca（発注）
外部: Alpaca / xAI(Grok) / Anthropic / Discord / Sentry
シークレット: SSM Parameter Store
```

`grok-trade-trading` は 1 つの Lambda で、EventBridge Scheduler が渡す `task`
（`trading_cycle` / `emergency_check`）で処理を切り替える。

| 旧 (Fly.io) | 新 (AWS) |
|---|---|
| 常駐コンテナ内の APScheduler | EventBridge Scheduler（外部トリガー） |
| `main.py`（API + スケジューラ同居） | `app.py`（API） / `lambda_trading.py`（スケジュール実行） |
| Postgres + SQLAlchemy | DynamoDB 単一テーブル（on-demand） |
| `scheduler.pause()` | DynamoDB `STATE/scheduler_running` |
| Fly secrets | SSM Parameter Store (SecureString) |

**コスト**: Lambda・EventBridge Scheduler・DynamoDB はいずれも無料枠内
（取引サイクル 14回/日 + 緊急チェック 84回/日 ≒ 月2,000回）。
zip パッケージ方式のためコンテナレジストリ(ECR)の保管料もかからず、
デプロイ成果物を置く S3 の数十 MB 分のみ。**実質 月 $0〜1**（旧構成は月約 $17）。

## 判断エンジンの切り替え

`DecisionEngine` パラメータ（環境変数 `DECISION_ENGINE`）で Stage 2 を選ぶ。
どちらも同じ形式の判断を返すので Risk Guard 以降は共通。

```bash
# Grok が直接判断（既定）
sam deploy --parameter-overrides DecisionEngine=grok

# Claude Opus 4.6 が判断（SSM に ANTHROPIC_API_KEY が必要）
sam deploy --parameter-overrides DecisionEngine=opus
```

## DynamoDB テーブル設計

単一テーブル `grok-trade`:

| pk | sk | 内容 |
|---|---|---|
| `TRADE` | `<UTC ISO時刻>#<乱数>` | 取引ログ（cycle_id・損切り・利確を含む） |
| `PIPELINE` | `<UTC ISO時刻>#<乱数>` | 1サイクル分の全ステージlog（旧 pipeline_log テーブル相当） |
| `STATE` | `scheduler_running` | 稼働フラグ |

sk が時刻順にソートされるため、最新 N 件は降順 Query 1 回で取得できる（GSI 不要）。

`PIPELINE` の項目名は旧 `pipeline_log` テーブルの列名を引き継いでいる
（`opus_output` / `risk_guard_passed` など）。`DECISION_ENGINE=grok` のときは
`opus_output` に Grok の判断が入るため、どちらが判断したかは `decision_engine`
フィールドで区別する。

## デプロイ

リポジトリのルートで以下を実行するだけでよい。

```bash
./scripts/deploy.sh
```

このスクリプトが順に行うこと:

1. **前提確認** — `sam` / `aws` の存在と AWS 認証をチェック
2. **シークレット登録** — **Bitwarden (`bw`) から自動取得**。ロックされていれば
   マスターパスワードを聞いてアンロックし、見つからないキーだけ手入力を求める
   （入力は非表示・履歴に残らない）
3. **ビルド** — `sam build`
4. **デプロイ** — `sam deploy`（CloudFormation スタック作成/更新）
5. **疎通確認** — `/health` が 200 を返すまで確認し、最後に設定すべき環境変数を表示

再実行しても安全で、済んでいる手順は自動的にスキップされる。

### 前提ツール

```bash
brew install aws-sam-cli   # または pip install aws-sam-cli
aws configure              # 認証情報を未設定の場合
```

**Docker は不要**。Lambda は zip パッケージ方式で、依存関係は
[backend/Makefile](../backend/Makefile) が Lambda のプラットフォーム
(Linux x86_64 / CPython 3.11) 向けにクロスインストールする
（`pip --platform manylinux2014_x86_64 --python-version 3.11 --only-binary=:all:`）。
そのため macOS/ARM 上でも `sam build` がそのまま通る。

### 個別に実行したい場合

```bash
# シークレットだけ登録し直す
./scripts/register-secrets.sh

# ビルドとデプロイだけ
cd backend
sam build
sam deploy --stack-name grok-trade --region ap-northeast-1 \
  --resolve-s3 --capabilities CAPABILITY_IAM

# API URL を確認
aws cloudformation describe-stacks --stack-name grok-trade \
  --region ap-northeast-1 \
  --query "Stacks[0].Outputs[?OutputKey=='ApiUrl'].OutputValue" --output text
```

#### API キーの取得元

Fly.io は登録済みシークレットの「値」を返さない（名前のみ）ため、移行時は別の場所から
持ってくる必要がある。`scripts/register-secrets.sh` は **Bitwarden** を自動で参照する。

vault 内で以下のいずれかの形になっていれば自動で見つかる（大文字小文字・
アンダースコア・ハイフン・空白の違いは吸収する）:

| vault 内の形 | 例 |
|---|---|
| アイテムのカスタムフィールド名がキー名 | フィールド `GROK_API_KEY` に値 |
| アイテム名がキー名 → パスワード欄の値 | アイテム `ALPACA_SECRET_KEY` |
| アイテム名がキー名 → ノート（1行のみ） | アイテム `Discord Webhook Alerts` |

見つからないキーはその場で手入力を求められる。

登録対象のキー:

| キー | 必須 |
|---|---|
| `GROK_API_KEY` / `ALPACA_API_KEY` / `ALPACA_SECRET_KEY` | 必須 |
| `ANTHROPIC_API_KEY` | `DECISION_ENGINE=opus` のときのみ必須 |
| `DISCORD_WEBHOOK_ALERTS` / `DISCORD_WEBHOOK_TRADES` | 任意（未設定なら通知しない） |
| `SENTRY_DSN` | 任意（未設定なら Sentry 無効） |
| `API_SHARED_SECRET` | 自動生成 |

不足しているキーは `/health` の `missing_secrets` に出る。

### ダッシュボードの向き先を変更

Vercel（または `.env.local`）で以下を設定する:

```
NEXT_PUBLIC_API_URL=<上で取得した Function URL>
API_SHARED_SECRET=<手順2で生成した共有シークレット>
```

`API_SHARED_SECRET` は **`NEXT_PUBLIC_` を付けない**こと。付けるとブラウザのバンドルに
埋め込まれて誰でも読める。ダッシュボードの開始/停止ボタンはサーバー側の API ルート
(`/api/control`) を経由し、そこでシークレットを付与して backend を叩く。

### GitHub Actions 用の設定（CI から自動デプロイする場合）

CI は OIDC で AWS にデプロイする。以下を用意する:

1. GitHub OIDC プロバイダを IAM に登録
2. `repo:dkurokawa/grok-trade:*` を信頼するデプロイ用 IAM ロールを作成
   （CloudFormation / Lambda / S3 / IAM / DynamoDB / Scheduler / SSM 読み取り権限）
3. リポジトリシークレットに登録:
   - `AWS_DEPLOY_ROLE_ARN` — 上記ロールの ARN
   - `DISCORD_WEBHOOK_ALERTS` — 通知用（既存）

## 運用

### 手動で実行

`grok-trade-trading` は `task` で処理を切り替える（省略時は `trading_cycle`）。

```bash
# 取引サイクル（Grok → 判断 → Risk Guard → 発注）
aws lambda invoke --function-name grok-trade-trading \
  --region ap-northeast-1 --cli-binary-format raw-in-base64-out \
  --payload '{"task": "trading_cycle"}' /dev/stdout

# 緊急チェック（ドローダウン監視のみ。AI を呼ばない）
aws lambda invoke --function-name grok-trade-trading \
  --region ap-northeast-1 --cli-binary-format raw-in-base64-out \
  --payload '{"task": "emergency_check"}' /dev/stdout
```

停止フラグが立っていると、どちらも何もせずに終了する。

### 緊急停止 / 再開

```bash
API_URL=$(aws cloudformation describe-stacks --stack-name grok-trade \
  --region ap-northeast-1 \
  --query "Stacks[0].Outputs[?OutputKey=='ApiUrl'].OutputValue" --output text)
SECRET=$(aws ssm get-parameter --name /grok-trade/API_SHARED_SECRET \
  --with-decryption --region ap-northeast-1 --query Parameter.Value --output text)

curl -X POST "${API_URL%/}/stop"  -H "x-api-key: $SECRET"   # 停止
curl -X POST "${API_URL%/}/start" -H "x-api-key: $SECRET"   # 再開
```

停止フラグは DynamoDB に永続化されるため、停止中は以降の EventBridge 起動で
取引サイクルが即座に return する（Lambda 自体は起動するが何もしない）。

### ログ

```bash
sam logs --stack-name grok-trade --name TradingFunction --tail
sam logs --stack-name grok-trade --name ApiFunction --tail
```

### ローカル実行

```bash
cd backend
# API をローカル起動（.env に各種キーが必要）
uvicorn app:app --reload --port 8000

# テスト（DynamoDB は moto でモックされるので AWS 接続不要）
pytest tests/ --ignore=tests/test_production_e2e.py
```

## Fly.io の停止（移行完了後）

AWS 側の稼働を確認してから実施する。

```bash
export PATH="$HOME/.fly/bin:$PATH"

fly status -a grok-trade-bot        # 現状確認
fly scale count 0 -a grok-trade-bot # まずマシンを止めて課金を止める

# 問題なければアプリごと削除（取り消し不可）
fly apps destroy grok-trade-bot
```

移行前は 2 台のマシンが起動しており、どちらも APScheduler を実行していた
（＝取引サイクルが二重実行される状態だった）。AWS 構成ではトリガーが
EventBridge Scheduler ただ 1 つなので、この問題は解消している。
