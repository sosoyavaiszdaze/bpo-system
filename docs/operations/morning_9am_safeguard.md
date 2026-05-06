# 朝 09:00 自動実行の確実成功 — 暫定対策 (Mac mini 移行前)

| 項目 | 値 |
|------|---|
| 作成日 | 2026-05-05 |
| 対象 | MacBook Air 上の launchd ジョブ `com.zynect.bpo.daily-chatwork` |
| 期間 | **本日 〜 Mac mini 移行 (5/5 昼以降) まで** |
| 関連 | ADR-005 (ChatWork ループ), `docs/operations/launchd_setup.md` |

> ⚠️ 本文書は **暫定対応** です。Mac mini 常時起動環境への移行後は §5 の手順で停止してください。

---

## 1. 現状の障害事象

### 5/4 朝 09:00 の失敗

```
症状:    DNS lookup error (Wi-Fi 切断状態で launchd 起動)
原因:    MacBook Air の Wi-Fi 一時切断
影響:    日次 ChatWork 投稿スキップ、ログにエラー記録
ChatWork: 5/4 朝の指摘投稿が届かず
```

### 5/5 朝の懸念

明日 5/5 の朝 09:00 は、5/7 提案前の **重要な実運用日** (ADR-011/012 実装着手前の最後の自然運用)。
ここで再度失敗すると以下が連鎖的に影響:

1. ChatWork 投稿エビデンスが 2 日連続欠ける
2. 5/7 提案で「自動運用エンジン稼働中」と訴求できない
3. Bot アカウント作成・自動提案エンジン実装に集中したい朝に手動リカバリ作業が発生

---

## 2. 3 案比較

### 案 A: caffeinate で sleep 防止 (08:55〜09:10)

`/usr/bin/caffeinate` で 15 分間スリープ抑止。

```bash
# 別 launchd ジョブで 08:55 に caffeinate -t 900 を起動
caffeinate -d -i -m -s -u -t 900
```

| 観点 | 評価 |
|------|------|
| 実装工数 | 低 (~10 分、新 plist 1 つ追加) |
| 確実性 | 🟡 **中** (Mac が既に起動中 + ログイン済前提、シャットダウン中は無効) |
| 副作用 | なし (sleep を 15 分妨げるだけ) |
| sudo 要否 | 不要 |
| 元に戻す手順 | `launchctl unload` + plist 削除のみ |

**弱点**: 「08:55 時点で Mac が起動していること」が前提。スリープ復帰でなく**シャットダウン状態の場合は無効**。

### 案 B: pmset wake schedule で 08:55 に強制起動

```bash
sudo pmset repeat wake MTWRFSU 08:55:00
```

| 観点 | 評価 |
|------|------|
| 実装工数 | 低 (~5 分、1 行コマンド) |
| 確実性 | 🟢 **高** (シャットダウン以外は確実に起床、スリープからは確実に wake) |
| 副作用 | システム全体の wake schedule 変更、毎日 08:55 に強制起動 |
| sudo 要否 | **必要** (1 回のみ) |
| 元に戻す手順 | `sudo pmset repeat cancel` |

**弱点**: sudo パスワード必要、システム設定変更で他の使用に影響 (毎朝 08:55 に PC が勝手に起動)。

### 案 C: launchd plist に複数時刻を登録 (09:00, 09:15, 09:30)

`StartCalendarInterval` を **配列形式で 3 時刻指定**。idempotency により多重投稿は防止される (Day 1 実装の `chatwork_sent.json`)。

```xml
<key>StartCalendarInterval</key>
<array>
    <dict><key>Hour</key><integer>9</integer><key>Minute</key><integer>0</integer></dict>
    <dict><key>Hour</key><integer>9</integer><key>Minute</key><integer>15</integer></dict>
    <dict><key>Hour</key><integer>9</integer><key>Minute</key><integer>30</integer></dict>
</array>
```

| 観点 | 評価 |
|------|------|
| 実装工数 | 低 (~5 分、plist 編集 + reload) |
| 確実性 | 🟢 **高** (3 回チャンス、Wi-Fi 一時切断には強い) |
| 副作用 | なし (idempotency で 2 回目以降は skip) |
| sudo 要否 | 不要 |
| 元に戻す手順 | plist を元に戻して reload |

**弱点**: Mac が完全シャットダウン or スリープでログイン未完の状態だと 3 回とも失敗。

---

## 3. 推奨案: **C (複数時刻登録) を採用**、必要なら B を組合せ

### 推奨理由

1. **5/4 の実際の失敗モードは「Wi-Fi 一時切断」** = Mac は起動中で launchd 自体は走っていた。これは C の retry が最も効く
2. **sudo 不要** = 山本さん側でパスワード入力なしに即適用可能
3. **Mac mini 移行後の撤去が簡単** = plist を元に戻すだけ、システム設定変更なし
4. **副作用ゼロ** = 既存の `chatwork_sent.json` idempotency で 2 回目以降は自動スキップ

### B との組合せ判断

| 状況 | 推奨 |
|------|------|
| Mac が朝 08:55 時点で「起動中」or「スリープ」 | **C のみで十分** ✅ |
| Mac を毎晩シャットダウンしている | C + B 併用推奨 (B が必須) |
| 朝の Wi-Fi 切断が頻繁 | C を 4 回 (09:00/09:15/09:30/09:45) に拡張 |

→ 山本さんの MacBook Air 利用パターン (毎晩スリープ・シャットダウン頻度) で判断ください。**スリープのみなら C 単独で OK**。

---

## 4. 推奨案 (C) の今夜実装手順

### Step 1: 現行 plist のバックアップ

```bash
cp ~/Library/LaunchAgents/com.zynect.bpo.daily-chatwork.plist \
   ~/Library/LaunchAgents/com.zynect.bpo.daily-chatwork.plist.bak.20260505
```

### Step 2: plist を編集 (3 時刻配列に変更)

下記 1 行コマンドで、`StartCalendarInterval` の `<dict>...<dict>` ブロックを 3 時刻配列に置換:

```bash
# Python で安全に書き換え (xml plist を pyplist パースで操作)
python3 - <<'PYEOF'
import plistlib
from pathlib import Path

src = Path.home() / "Library/LaunchAgents/com.zynect.bpo.daily-chatwork.plist"
with open(src, "rb") as f:
    data = plistlib.load(f)

# 3 時刻に拡張
data["StartCalendarInterval"] = [
    {"Hour": 9, "Minute": 0},
    {"Hour": 9, "Minute": 15},
    {"Hour": 9, "Minute": 30},
]

# atomic write
tmp = src.with_suffix(".plist.tmp")
with open(tmp, "wb") as f:
    plistlib.dump(data, f)
tmp.replace(src)

print(f"✅ {src} を 3 時刻配列に更新")
print(f"   新規 StartCalendarInterval: {data['StartCalendarInterval']}")
PYEOF
```

### Step 3: launchd に再読込

```bash
launchctl unload ~/Library/LaunchAgents/com.zynect.bpo.daily-chatwork.plist
launchctl load ~/Library/LaunchAgents/com.zynect.bpo.daily-chatwork.plist
launchctl list | grep zynect
```

### Step 4: 次回起動時刻を確認

```bash
launchctl print "gui/$(id -u)/com.zynect.bpo.daily-chatwork" 2>/dev/null | \
  grep -A 10 "calendarinterval"
```

期待表示:
```
"com.apple.launchd.calendarinterval" = {
    "Hour" = 9
    "Minute" = 0
}
"com.apple.launchd.calendarinterval" = {
    "Hour" = 9
    "Minute" = 15
}
"com.apple.launchd.calendarinterval" = {
    "Hour" = 9
    "Minute" = 30
}
```

### Step 5: 翌朝の挙動確認 (5/5 朝 10:00 以降)

```bash
# 09:00 に投稿成功した場合
tail -30 ~/bpo-system/bpo-system/logs/daily-chatwork.err.log
# 「日次チェック完了: posted_indications=N, errors=[]」を 09:00〜09:30 のいずれかに記録

# 09:15 / 09:30 の retry 動作確認
launchctl print "gui/$(id -u)/com.zynect.bpo.daily-chatwork" 2>/dev/null | \
  grep "run count"
# 5/4 から 1〜3 増加していれば retry が動作した証拠
```

---

## 5. Mac mini 移行後の停止手順

Mac mini 常時起動環境では retry 不要 (Wi-Fi 安定 + 24h 稼働)。元の単一 09:00 設定に戻す:

### Step 1: plist を 1 時刻設定に戻す

```bash
python3 - <<'PYEOF'
import plistlib
from pathlib import Path

src = Path.home() / "Library/LaunchAgents/com.zynect.bpo.daily-chatwork.plist"
with open(src, "rb") as f:
    data = plistlib.load(f)

data["StartCalendarInterval"] = {"Hour": 9, "Minute": 0}

tmp = src.with_suffix(".plist.tmp")
with open(tmp, "wb") as f:
    plistlib.dump(data, f)
tmp.replace(src)

print(f"✅ {src} を 1 時刻 (09:00) に戻し")
PYEOF

launchctl unload ~/Library/LaunchAgents/com.zynect.bpo.daily-chatwork.plist
launchctl load ~/Library/LaunchAgents/com.zynect.bpo.daily-chatwork.plist
```

### Step 2: バックアップ削除 (任意、3 日後)

```bash
rm ~/Library/LaunchAgents/com.zynect.bpo.daily-chatwork.plist.bak.20260505
```

### Step 3: B を併用していた場合は wake schedule も解除

```bash
sudo pmset repeat cancel
```

---

## 6. 失敗時の即時リカバリ手順

5/5 朝 09:30 以降に確認して投稿が来ていない場合:

```bash
# 1. ログ確認
tail -50 ~/bpo-system/bpo-system/logs/daily-chatwork.err.log

# 2. 手動で即座に実行 (Wi-Fi 復活確認後)
launchctl start com.zynect.bpo.daily-chatwork

# 3. それでも失敗するなら直接スクリプト実行
cd ~/bpo-system/bpo-system
venv/bin/python3 scripts/daily_chatwork_check.py --client pilotton --prefix "[テスト] "
```

---

## 7. 案 A (caffeinate) を併用する場合 (オプション)

`docs/operations/launchd_setup.md` の本体 plist には組み込まず、別 plist を作成:

```bash
# scripts/launchd/com.zynect.bpo.morning-caffeinate.plist (新規、本暫定対応用)
cat > ~/Library/LaunchAgents/com.zynect.bpo.morning-caffeinate.plist <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.zynect.bpo.morning-caffeinate</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/bin/caffeinate</string>
        <string>-d</string>
        <string>-i</string>
        <string>-m</string>
        <string>-s</string>
        <string>-u</string>
        <string>-t</string>
        <string>1500</string>
    </array>
    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key><integer>8</integer>
        <key>Minute</key><integer>55</integer>
    </dict>
    <key>RunAtLoad</key>
    <false/>
    <key>StandardErrorPath</key>
    <string>/tmp/caffeinate.err.log</string>
</dict>
</plist>
EOF

launchctl load ~/Library/LaunchAgents/com.zynect.bpo.morning-caffeinate.plist
```

→ 08:55〜09:20 の 25 分間 (1500 秒) スリープ抑止。Mac mini 移行時は plist 削除。

---

## 8. 即時実行コマンド (山本さん向け、コピペ用)

**全部まとめて実行する場合**:

```bash
# === Step 1: バックアップ + Step 2: plist 編集 + Step 3: 再読込 + Step 4: 確認 ===
cp ~/Library/LaunchAgents/com.zynect.bpo.daily-chatwork.plist \
   ~/Library/LaunchAgents/com.zynect.bpo.daily-chatwork.plist.bak.20260505 && \
\
python3 - <<'PYEOF' && \
import plistlib
from pathlib import Path
src = Path.home() / "Library/LaunchAgents/com.zynect.bpo.daily-chatwork.plist"
with open(src, "rb") as f:
    data = plistlib.load(f)
data["StartCalendarInterval"] = [
    {"Hour": 9, "Minute": 0},
    {"Hour": 9, "Minute": 15},
    {"Hour": 9, "Minute": 30},
]
tmp = src.with_suffix(".plist.tmp")
with open(tmp, "wb") as f:
    plistlib.dump(data, f)
tmp.replace(src)
print(f"✅ plist 更新: 3 時刻配列 (09:00, 09:15, 09:30)")
PYEOF
\
launchctl unload ~/Library/LaunchAgents/com.zynect.bpo.daily-chatwork.plist && \
launchctl load ~/Library/LaunchAgents/com.zynect.bpo.daily-chatwork.plist && \
echo "" && \
echo "=== launchctl list 確認 ===" && \
launchctl list | grep zynect && \
echo "" && \
echo "=== 設定された起動時刻 ===" && \
launchctl print "gui/$(id -u)/com.zynect.bpo.daily-chatwork" 2>/dev/null | \
  grep -A 1 '"Hour"' | head -10 && \
echo "" && \
echo "✅ 5/5 朝 09:00 / 09:15 / 09:30 の 3 回チャンス設定完了"
```

これで **明日朝 09:00 / 09:15 / 09:30 の 3 回起動** になり、いずれかで Wi-Fi が復活していれば成功します。

---

## 9. References

- ADR-005: [ChatWork 経由の指摘・完了・月次運用ループ](../decisions/ADR-005-chatwork-indication-completion-monthly-loop.md)
- ADR-011: [ChatWork 自動通知グループの設計](../decisions/ADR-011-chatwork-auto-notification-group.md)
- ADR-012: [企業別自動提案エンジンの設計](../decisions/ADR-012-auto-proposal-engine.md)
- 既存運用手順: `docs/operations/launchd_setup.md`
- 既存 plist マスター: `scripts/launchd/com.zynect.bpo.daily-chatwork.plist`
- idempotency 実装: `notifiers/chatwork_notifier.py` (重複投稿防止の根拠)
