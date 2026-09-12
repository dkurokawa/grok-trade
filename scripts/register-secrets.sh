#!/usr/bin/env bash
# Register the bot's secrets into SSM Parameter Store as SecureStrings.
#
# Values are resolved in this order, per key:
#   1. Bitwarden vault (bw), if it is installed and unlocked
#   2. Manual entry, read with `read -rs` so nothing is echoed or kept in history
#
# Secret values are never printed. Re-running is safe: existing SSM parameters
# are overwritten, so this doubles as a rotation tool.
set -euo pipefail

REGION="${AWS_REGION:-ap-northeast-1}"
PREFIX="${SSM_PREFIX:-/grok-trade}"

# ANTHROPIC_API_KEY is only needed with DECISION_ENGINE=opus; SENTRY_DSN is optional.
KEYS=(GROK_API_KEY ALPACA_API_KEY ALPACA_SECRET_KEY
      DISCORD_WEBHOOK_ALERTS DISCORD_WEBHOOK_TRADES
      ANTHROPIC_API_KEY SENTRY_DSN)

put() {
  aws ssm put-parameter --region "$REGION" \
    --name "$PREFIX/$1" --type SecureString \
    --value "$2" --overwrite >/dev/null
}

# ---------------------------------------------------------------- Bitwarden
BW_READY=0
if command -v bw >/dev/null 2>&1 && command -v jq >/dev/null 2>&1; then
  case "$(bw status 2>/dev/null | jq -r .status 2>/dev/null || echo unknown)" in
    unlocked)
      BW_READY=1 ;;
    locked)
      echo "Bitwarden がロックされています。アンロックします（マスターパスワード入力）。"
      # bw unlock prompts on this terminal; the session key never leaves this shell.
      if BW_SESSION="$(bw unlock --raw)"; then
        export BW_SESSION
        BW_READY=1
        echo
      else
        echo "  アンロックできませんでした。手入力に切り替えます。"
      fi ;;
    *)
      echo "Bitwarden は未ログインです（bw login）。手入力に切り替えます。" ;;
  esac
fi

VAULT_JSON=""
HINTED=0
if [ "$BW_READY" = 1 ]; then
  echo "Bitwarden から候補を検索中..."
  VAULT_JSON="$(bw list items 2>/dev/null || echo '[]')"
  echo "  ${#KEYS[@]} 個のキーを照合します"
  echo
fi

# Look a key up in the vault. Checks, in order:
#   - a custom field whose name matches the key (case/underscore-insensitive)
#   - a login password on an item whose name matches the key
#   - the item's secure note body, if it is a single line
lookup_bw() {
  local key="$1"
  [ -n "$VAULT_JSON" ] || return 1
  printf '%s' "$VAULT_JSON" | jq -r --arg k "$key" '
    def norm: ascii_downcase | gsub("[^a-z0-9]"; "");
    ($k | norm) as $nk
    | [ .[]
        | . as $item
        | ( ($item.fields // [])[]
            | select((.name // "" | norm) == $nk)
            | .value )
        , ( select(($item.name // "" | norm) == $nk)
            | $item.login.password // empty )
        , ( select(($item.name // "" | norm) == $nk)
            | select(($item.notes // "") | test("\n") | not)
            | $item.notes // empty )
      ]
    | map(select(. != null and . != ""))
    | first // empty
  ' 2>/dev/null
}

# ------------------------------------------------------------------- 登録
echo "SSM $PREFIX ($REGION) に登録します"
echo

for KEY in "${KEYS[@]}"; do
  VALUE=""
  SOURCE=""

  if [ "$BW_READY" = 1 ]; then
    VALUE="$(lookup_bw "$KEY" || true)"
    [ -n "$VALUE" ] && SOURCE="Bitwarden"
  fi

  if [ -z "$VALUE" ] && [ "$BW_READY" = 1 ] && [ "$HINTED" != 1 ]; then
    # Show what the vault actually contains (names only) so a naming mismatch
    # is obvious rather than leaving the user guessing.
    echo "  Bitwarden で自動照合できませんでした。vault の項目名一覧:"
    printf '%s' "$VAULT_JSON" | jq -r '.[].name' 2>/dev/null | sed 's/^/    - /' | head -40
    echo "  （該当する項目があれば、その値を下に貼り付けてください）"
    echo
    HINTED=1
  fi

  if [ -z "$VALUE" ]; then
    printf '  %s (入力, 空Enterでスキップ): ' "$KEY"
    read -rs VALUE
    echo
    [ -n "$VALUE" ] && SOURCE="手入力"
  fi

  if [ -n "$VALUE" ]; then
    put "$KEY" "$VALUE"
    echo "  ✓ $KEY  ($SOURCE)"
  else
    echo "  – $KEY  スキップ"
  fi
  unset VALUE
done

# Shared secret protecting POST /start and /stop. Generated, never typed.
if aws ssm get-parameter --region "$REGION" --name "$PREFIX/API_SHARED_SECRET" >/dev/null 2>&1; then
  echo "  ✓ API_SHARED_SECRET  (既存を維持)"
else
  put API_SHARED_SECRET "$(openssl rand -hex 32)"
  echo "  ✓ API_SHARED_SECRET  (自動生成)"
fi

echo
echo "登録済みパラメータ:"
aws ssm get-parameters-by-path --region "$REGION" --path "$PREFIX" \
  --query "Parameters[].Name" --output table
