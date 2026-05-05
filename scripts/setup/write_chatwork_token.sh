#!/usr/bin/env bash
# write_chatwork_token.sh — ChatWork API トークン安全書き込みスクリプト
#
# 目的: .env の CHATWORK_API_TOKEN を安全に上書きする
#       (山本がチャット等にトークン値を貼り付けることなく書き込み)
#
# 安全策:
#   - read -rs で入力 (画面非表示)
#   - 長さチェック (30 文字未満なら警告終了 / exit 2)
#   - 重複貼付検出 (前半 == 後半なら Cmd+V 2 回押し疑いで終了 / exit 3)
#     2026-05-03 の token=64chars 重複事故 (401 Unauthorized) から学習した防御
#   - .env を事前にバックアップ
#   - sed で当該行のみ in-place 置換 (冪等)
#   - 行末の不可視文字を cat -e で可視化検証
#   - 値そのものは表示せず、prefix5 + suffix4 + 文字数のみ表示
#   - 関数終了時に変数を unset
#
# 使い方:
#   bash scripts/setup/write_chatwork_token.sh

set -euo pipefail

# プロジェクトルートに移動 (.env を確実に対象化)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$PROJECT_ROOT"

ENV_FILE="$PROJECT_ROOT/.env"
TARGET_KEY="CHATWORK_API_TOKEN"
MIN_LENGTH=30

# クリーンアップハンドラ: 異常終了時もトークン変数を確実に消す
cleanup() {
    unset NEW_TOKEN || true
}
trap cleanup EXIT INT TERM

echo "===== CHATWORK_API_TOKEN 書き込みスクリプト ====="
echo "対象ファイル: $ENV_FILE"
echo

# .env の存在確認
if [[ ! -f "$ENV_FILE" ]]; then
    echo "❌ ERROR: .env が見つかりません: $ENV_FILE"
    exit 1
fi

# 現在の値の状態を表示 (マスク済み)
echo "-- 現在の状態 --"
awk -F= -v key="$TARGET_KEY" '$1 == key {
    n = length($2)
    if (n == 0) print key "=(empty)"
    else if (n < 30) print key "=(SHORT! " n " chars, prefix=" substr($2, 1, 5) " suffix=" substr($2, n-3) ")"
    else print key "=(set, " n " chars, prefix=" substr($2, 1, 5) "..." substr($2, n-3) ")"
}' "$ENV_FILE"
echo

# 入力プロンプト (画面非表示)
echo "新しい ChatWork API トークンを入力してください (画面に表示されません)"
echo "貼り付け後 Enter を押す (Ctrl+C で中止):"
read -rs NEW_TOKEN
echo

# 長さチェック
TOKEN_LEN=${#NEW_TOKEN}
if (( TOKEN_LEN < MIN_LENGTH )); then
    echo "❌ ERROR: トークンが ${MIN_LENGTH} 文字未満です (入力 ${TOKEN_LEN} 文字)"
    echo "         ChatWork API トークンは通常 32 文字以上です。"
    echo "         書き込みを中止しました。.env は変更されていません。"
    exit 2
fi

# 重複貼付検出 (Cmd+V 2 回押し対策、2026-05-03 の token=64chars 事故から学習)
# 入力長が偶数 かつ 前半 == 後半 なら同じ値が 2 回貼られた疑い → 再入力を促す
HALF_LEN=$(( TOKEN_LEN / 2 ))
if (( TOKEN_LEN % 2 == 0 )) && [[ "${NEW_TOKEN:0:$HALF_LEN}" == "${NEW_TOKEN:$HALF_LEN}" ]]; then
    echo "❌ ERROR: トークンが重複貼付されています"
    echo "         (入力 ${TOKEN_LEN} 文字、前半 ${HALF_LEN} 文字 == 後半 ${HALF_LEN} 文字)"
    echo "         貼り付け時に Cmd+V を 2 回押した、または貼付元が既に重複していた可能性があります。"
    echo "         一度ターミナルをクリア (Ctrl+L) してから本スクリプトを再実行してください。"
    echo "         書き込みを中止しました。.env は変更されていません。"
    exit 3
fi

# 入力の prefix/suffix を表示 (マスク)
PREFIX5="${NEW_TOKEN:0:5}"
SUFFIX4="${NEW_TOKEN: -4}"
echo "✅ 入力受領: ${TOKEN_LEN} chars, prefix=${PREFIX5}... suffix=...${SUFFIX4}"
echo

# .env をバックアップ
TS=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE=".env.bak.${TS}"
cp "$ENV_FILE" "$BACKUP_FILE"
echo "✅ バックアップ作成: $BACKUP_FILE"

# 既存の CHATWORK_API_TOKEN= 行があるか確認
if grep -qE "^${TARGET_KEY}=" "$ENV_FILE"; then
    # 既存行を sed で in-place 置換 (冪等: 何度実行しても重複行が増えない)
    # NEW_TOKEN 内の特殊文字 ($,&,/) を sed エスケープ
    ESCAPED_TOKEN=$(printf '%s' "$NEW_TOKEN" | sed -e 's/[\/&]/\\&/g')
    sed -i '' "s|^${TARGET_KEY}=.*|${TARGET_KEY}=${ESCAPED_TOKEN}|" "$ENV_FILE"
    unset ESCAPED_TOKEN
    echo "✅ 既存行を上書き (sed in-place)"
else
    # 行が無ければ末尾に追記 (実運用では稀だが念のため)
    {
        echo ""
        echo "${TARGET_KEY}=${NEW_TOKEN}"
    } >> "$ENV_FILE"
    echo "✅ 新規行を追記"
fi

# 入力トークンを即座に unset (メモリから消去)
unset NEW_TOKEN

# 書き込み結果を verify (マスク済み)
echo
echo "-- 書き込み後の状態 --"
awk -F= -v key="$TARGET_KEY" '$1 == key {
    n = length($2)
    if (n == 0) print key "=(empty)"
    else print key "=(set, " n " chars, prefix=" substr($2, 1, 5) "..." substr($2, n-3) ")"
}' "$ENV_FILE"

# 行末不可視文字 ($ で行末、^M があれば CR 混入) を cat -e で可視化
# 値部分はマスクして = の右側を *** に置換
echo
echo "-- 行末確認 (値はマスク、$ = LF / ^M = CR 混入 / その他制御文字検出用) --"
grep -E "^${TARGET_KEY}=" "$ENV_FILE" | \
    awk -F= -v key="$TARGET_KEY" '{print key "=*** [val_len=" length($2) "]"}' | cat -e

# 同じ行が複数無いことを確認 (冪等性検証)
COUNT=$(grep -cE "^${TARGET_KEY}=" "$ENV_FILE")
if (( COUNT == 1 )); then
    echo "✅ 冪等性 OK: ${TARGET_KEY}= 行は 1 件のみ"
else
    echo "⚠️  WARNING: ${TARGET_KEY}= 行が ${COUNT} 件あります。手動確認してください。"
fi

# 旧バックアップ (7 日超) のクリーンアップ提案
OLD_BAKS=$(find . -maxdepth 1 -name ".env.bak.*" -mtime +7 2>/dev/null | wc -l | tr -d ' ')
if (( OLD_BAKS > 0 )); then
    echo
    echo "ℹ️  7 日以上前の .env バックアップが ${OLD_BAKS} 件あります。"
    echo "    削除する場合: find . -maxdepth 1 -name '.env.bak.*' -mtime +7 -delete"
fi

echo
echo "===== 完了 ====="
echo "次のステップ: Claude Code 側で疎通テストを依頼してください。"
