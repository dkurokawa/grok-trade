#!/usr/bin/env bash
# One-command deploy for the Grok Trade bot on AWS.
#
#   ./scripts/deploy.sh
#
# Steps: prerequisite check -> secrets -> build -> deploy -> smoke test.
# Safe to re-run; each step skips work that is already done.
set -euo pipefail

REGION="${AWS_REGION:-ap-northeast-1}"
STACK="${SAM_STACK_NAME:-grok-trade}"
PREFIX="${SSM_PREFIX:-/grok-trade}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

step() { echo; echo "▶ $*"; }
ok()   { echo "  ✓ $*"; }
die()  { echo "  ✗ $*" >&2; exit 1; }

# ---------------------------------------------------------------- 1. 前提確認
step "1/5 前提ツールの確認"
command -v sam >/dev/null || die "sam が見つからない → brew install aws-sam-cli"
ok "sam $(sam --version | awk '{print $4}')"
command -v aws >/dev/null || die "aws CLI が見つからない → brew install awscli"
ACCOUNT=$(aws sts get-caller-identity --query Account --output text 2>/dev/null) \
  || die "AWS 認証が無効 → aws configure"
ok "AWS アカウント $ACCOUNT / リージョン $REGION"

# ------------------------------------------------------------ 2. シークレット
step "2/5 シークレット (SSM Parameter Store)"
REQUIRED=(GROK_API_KEY ALPACA_API_KEY ALPACA_SECRET_KEY)
EXISTING=$(aws ssm get-parameters-by-path --region "$REGION" --path "$PREFIX" \
  --query "Parameters[].Name" --output text 2>/dev/null || true)

MISSING=()
for KEY in "${REQUIRED[@]}"; do
  case " $EXISTING " in
    *"$PREFIX/$KEY"*) ;;
    *) MISSING+=("$KEY") ;;
  esac
done

if [ ${#MISSING[@]} -gt 0 ]; then
  echo "  未登録: ${MISSING[*]}"
  "$REPO_ROOT/scripts/register-secrets.sh"

  # Re-check: deploying without these produces a stack whose Lambdas cannot
  # trade, so stop here rather than shipping something known to be broken.
  EXISTING=$(aws ssm get-parameters-by-path --region "$REGION" --path "$PREFIX" \
    --query "Parameters[].Name" --output text 2>/dev/null || true)
  STILL_MISSING=()
  for KEY in "${REQUIRED[@]}"; do
    case " $EXISTING " in
      *"$PREFIX/$KEY"*) ;;
      *) STILL_MISSING+=("$KEY") ;;
    esac
  done
  if [ ${#STILL_MISSING[@]} -gt 0 ]; then
    echo
    echo "  未登録のまま: ${STILL_MISSING[*]}"
    echo
    echo "  これらが無いと Bot は取引できません。Bitwarden で見つからない場合は"
    echo "  vault 内の項目名を確認してください:"
    echo
    echo "      bw unlock          # 未アンロックなら"
    echo "      bw list items | jq -r '.[].name'"
    echo
    echo "  該当項目が分かったら ./scripts/register-secrets.sh を再実行し、"
    echo "  プロンプトに値を貼り付けてください。"
    die "シークレット未登録のためデプロイを中止しました"
  fi
else
  ok "登録済み"
fi

# ------------------------------------------------------------------ 3. ビルド
step "3/5 ビルド (Docker 不要)"
cd "$REPO_ROOT/backend"
sam build >/dev/null || die "sam build に失敗"
ok "Lambda パッケージを作成"

# ---------------------------------------------------------------- 4. デプロイ
step "4/5 デプロイ (CloudFormation)"
sam deploy \
  --stack-name "$STACK" \
  --region "$REGION" \
  --resolve-s3 \
  --capabilities CAPABILITY_IAM \
  --no-confirm-changeset \
  --no-fail-on-empty-changeset

API_URL=$(aws cloudformation describe-stacks --stack-name "$STACK" --region "$REGION" \
  --query "Stacks[0].Outputs[?OutputKey=='ApiUrl'].OutputValue" --output text)
[ -n "$API_URL" ] || die "API URL を取得できなかった"
ok "API URL: $API_URL"

# -------------------------------------------------------------- 5. 疎通確認
step "5/5 疎通確認"
for i in 1 2 3 4 5; do
  CODE=$(curl -s -o /dev/null -w "%{http_code}" "${API_URL%/}/health" --max-time 30 || echo 000)
  if [ "$CODE" = "200" ]; then
    ok "/health が 200 を返した"
    break
  fi
  echo "  ... 応答待ち ($i/5): $CODE"
  [ "$i" = 5 ] && die "/health が応答しない"
  sleep 10
done

curl -s "${API_URL%/}/health" --max-time 30; echo

SECRET=$(aws ssm get-parameter --region "$REGION" --name "$PREFIX/API_SHARED_SECRET" \
  --with-decryption --query Parameter.Value --output text 2>/dev/null || echo "")

# Never print the decrypted secret itself - length + sha256 prefix are enough
# to sanity-check it was fetched, without putting the value in a terminal
# scrollback or CI log.
SECRET_LEN=$(printf '%s' "$SECRET" | wc -c | tr -d ' ')
SECRET_SHA=$(printf '%s' "$SECRET" | shasum -a 256 | cut -c1-12)

cat <<EOF

────────────────────────────────────────────────────────────
デプロイ完了

  API URL : $API_URL
  取引    : 市場時間中 30分ごとに EventBridge Scheduler が起動

  API_SHARED_SECRET: 長さ $SECRET_LEN 文字 / sha256先頭12桁 $SECRET_SHA
  (値そのものはここに表示しない。取得し直すには:
   aws ssm get-parameter --region "$REGION" --name "$PREFIX/API_SHARED_SECRET" --with-decryption --query Parameter.Value --output text)

ダッシュボード (Vercel) に設定する環境変数（すべてサーバー専用。NEXT_PUBLIC_ は付けない）:

  API_URL=$API_URL
  API_SHARED_SECRET=<上記のシークレット。値は手元で取得して貼る>
  DASHBOARD_USER=<ダッシュボード全体を守る Basic 認証のユーザー名>
  DASHBOARD_PASSWORD=<同・パスワード>

  ※ API_SHARED_SECRET / DASHBOARD_USER / DASHBOARD_PASSWORD に
     NEXT_PUBLIC_ は付けないこと (付けるとブラウザのバンドルから読めてしまう)

停止 / 再開 (値は上のコマンドで取得してから):

  curl -X POST "${API_URL%/}/stop"  -H "x-api-key: \$SECRET"
  curl -X POST "${API_URL%/}/start" -H "x-api-key: \$SECRET"

Fly.io の停止手順は docs/AWS_DEPLOY.md を参照
────────────────────────────────────────────────────────────
EOF
