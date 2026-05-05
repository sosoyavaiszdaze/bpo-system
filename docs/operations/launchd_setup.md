# launchd セットアップ手順 (山本さん向け / launchd 知識ゼロ前提)

> **対象**: 山本 (Mac 利用、ターミナル基本操作のみ習得)
> **目的**: `scripts/daily_chatwork_check.py` を毎朝 09:00 JST に自動実行する設定
> **関連**: ADR-005、`scripts/launchd/com.zynect.bpo.daily-chatwork.plist`、`scripts/daily_chatwork_check.py`

---

## launchd とは (3 行で)

- macOS 標準の自動実行スケジューラ (Linux の cron に相当)
- 「plist」という設定ファイルを書いて `launchctl` コマンドで登録する
- ユーザがログイン中なら、Mac がスリープしていても起床直後に追いつき実行 (catch-up) してくれる

---

## 1. plist ファイルの配置場所

| 役割 | パス | 状態 |
|------|------|------|
| **マスター (git 管理)** | `~/bpo-system/bpo-system/scripts/launchd/com.zynect.bpo.daily-chatwork.plist` | ✅ 作成済み |
| **launchd 実行用 (本番)** | `~/Library/LaunchAgents/com.zynect.bpo.daily-chatwork.plist` | ❌ 未配置 (これからコピー) |

**設計**: マスターを git で管理 → コマンド 1 行で `~/Library/LaunchAgents/` にコピーして登録。修正時はリポジトリ側を変更してから再 cp + reload で反映。

---

## 2. plist の完全な内容

`scripts/launchd/com.zynect.bpo.daily-chatwork.plist` (作成済み):

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.zynect.bpo.daily-chatwork</string>

    <key>ProgramArguments</key>
    <array>
        <string>/Users/trunktrunk/bpo-system/bpo-system/venv/bin/python3</string>
        <string>/Users/trunktrunk/bpo-system/bpo-system/scripts/daily_chatwork_check.py</string>
        <string>--client</string>
        <string>pilotton</string>
        <string>--prefix</string>
        <string>[テスト] </string>
    </array>

    <key>WorkingDirectory</key>
    <string>/Users/trunktrunk/bpo-system/bpo-system</string>

    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key>
        <integer>9</integer>
        <key>Minute</key>
        <integer>0</integer>
    </dict>

    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
        <key>TZ</key>
        <string>Asia/Tokyo</string>
        <key>LANG</key>
        <string>ja_JP.UTF-8</string>
        <key>PYTHONUNBUFFERED</key>
        <string>1</string>
    </dict>

    <key>StandardOutPath</key>
    <string>/Users/trunktrunk/bpo-system/bpo-system/logs/daily-chatwork.out.log</string>

    <key>StandardErrorPath</key>
    <string>/Users/trunktrunk/bpo-system/bpo-system/logs/daily-chatwork.err.log</string>

    <key>RunAtLoad</key>
    <false/>

    <key>KeepAlive</key>
    <false/>

    <key>ProcessType</key>
    <string>Background</string>
</dict>
</plist>
```

### 各キーの意味

| キー | 値 | 意味 |
|------|-----|------|
| `Label` | `com.zynect.bpo.daily-chatwork` | launchctl から参照する一意の名前 |
| `ProgramArguments` | venv の python3 で daily_chatwork_check.py を実行 | 実行コマンドを配列形式で書く |
| `WorkingDirectory` | プロジェクトルート | `.env` を `load_dotenv()` で読むため必須 |
| `StartCalendarInterval` | `Hour=9, Minute=0` | 毎日 09:00 に起動 |
| `TZ` | `Asia/Tokyo` | 09:00 = JST 09:00 を保証 (これ無しだと UTC 解釈) |
| `EnvironmentVariables` | PATH / TZ / LANG / PYTHONUNBUFFERED | launchd は **シェル環境変数を一切継承しない**。必要な変数は明示 |
| `StandardOutPath` | logs/daily-chatwork.out.log | 標準出力の保存先 |
| `StandardErrorPath` | logs/daily-chatwork.err.log | エラー出力の保存先 |
| `RunAtLoad` | `false` | 登録時に即実行しない (明朝 09:00 まで待つ) |
| `KeepAlive` | `false` | 1 回実行で終了、常駐させない |
| `ProcessType` | `Background` | OS にバックグラウンドジョブと認識させる (省電力モード考慮) |

### 内部レビュー期間後の本番化 (約 2 週間後)

`ProgramArguments` から `--prefix` 行 2 つを削除し再 load:

```xml
<!-- 削除 -->
        <string>--prefix</string>
        <string>[テスト] </string>
```

---

## 3. 登録コマンド

### 何をしているか
- ログディレクトリを作成
- マスター plist を `~/Library/LaunchAgents/` にコピー
- 構文を検証 (`plutil -lint`)
- launchd に登録 (`launchctl load`)
- 登録できたか確認 (`launchctl list`)

```bash
cd ~/bpo-system/bpo-system

# (1) ログディレクトリ作成
mkdir -p logs

# (2) plist を LaunchAgents へコピー
cp scripts/launchd/com.zynect.bpo.daily-chatwork.plist \
   ~/Library/LaunchAgents/com.zynect.bpo.daily-chatwork.plist

# (3) plist 構文を検証
plutil -lint ~/Library/LaunchAgents/com.zynect.bpo.daily-chatwork.plist
# → 期待出力: "...plist: OK"

# (4) launchd に登録
launchctl load ~/Library/LaunchAgents/com.zynect.bpo.daily-chatwork.plist
# → 無音で完了 (出力なしが正常)

# (5) 登録確認
launchctl list | grep zynect
# → 期待出力: "-	0	com.zynect.bpo.daily-chatwork"
```

`launchctl list` の 3 列の意味:
- 1 列目 `-`: 現在の PID (まだ走ってないので `-`)
- 2 列目 `0`: 前回終了コード (0 = 成功 / それ以外 = エラー)
- 3 列目: Label

### `launchctl bootstrap` (新方式) を使う場合

macOS 10.10+ では `bootstrap` が新しい推奨方式 (`load` も引き続き動作):

```bash
# bootstrap (新方式)
launchctl bootstrap "gui/$(id -u)" \
  ~/Library/LaunchAgents/com.zynect.bpo.daily-chatwork.plist

# bootout (新方式の解除)
launchctl bootout "gui/$(id -u)/com.zynect.bpo.daily-chatwork"
```

本ドキュメントでは互換性の高い `load`/`unload` を採用します。

---

## 4. 動作確認コマンド

### 何をしているか
- 即時実行 (`launchctl start`) で明朝を待たずにテスト
- ログを `tail` で目視確認
- launchd 内部状態 (`launchctl print`) で次回起動時刻を確認

```bash
# (1) 即時テスト実行
launchctl start com.zynect.bpo.daily-chatwork

# (2) 数秒待機
sleep 5

# (3) 結果を確認
launchctl list | grep zynect
# → 2 列目が 0 なら成功、それ以外はトラブルシュート参照

# (4) 標準出力ログ (最新 30 行)
tail -30 ~/bpo-system/bpo-system/logs/daily-chatwork.out.log

# (5) エラーログ (空であれば問題なし)
tail -30 ~/bpo-system/bpo-system/logs/daily-chatwork.err.log

# (6) リアルタイム監視 (Ctrl+C で抜ける)
tail -f ~/bpo-system/bpo-system/logs/daily-chatwork.out.log

# (7) 次回起動時刻 + 累積実行回数 + 直近終了コード
launchctl print "gui/$(id -u)/com.zynect.bpo.daily-chatwork" 2>/dev/null | \
  grep -E "state|next firing|run count|last exit code"
```

成功時のログ末尾例:
```
2026-XX-XX HH:MM:SS [INFO] daily_chatwork: 日次 ChatWork チェック開始: client=pilotton dry_run=False prefix='[テスト] '
2026-XX-XX HH:MM:SS [INFO] daily_chatwork: 検知: N 件 upsert / clean 候補: M 件
2026-XX-XX HH:MM:SS [INFO] daily_chatwork: 日次チェック完了: {'posted_indications': 3, 'posted_completions': 0, 'errors': []}
```

ChatWork 本番ルーム (rid 435851481) に `[テスト] ` プレフィクス付きの投稿が来ていれば成功。

---

## 5. 解除・削除手順

### 何をしているか
- launchd から登録解除 (即時、ジョブ停止)
- 完全に消したい場合は plist ファイル本体を削除

```bash
# (1) launchd から解除
launchctl unload ~/Library/LaunchAgents/com.zynect.bpo.daily-chatwork.plist

# (2) 解除確認 (出力なし = 解除成功)
launchctl list | grep zynect

# (3) plist ファイル本体を削除 (完全に消す場合のみ)
rm ~/Library/LaunchAgents/com.zynect.bpo.daily-chatwork.plist

# 注: リポジトリ側 scripts/launchd/ の plist は残す (再登録用マスター)
```

### 一時停止 (再登録するつもりがある場合)

```bash
# unload するだけで plist は残す → 再登録は launchctl load 1 行で復帰
launchctl unload ~/Library/LaunchAgents/com.zynect.bpo.daily-chatwork.plist
```

---

## 6. Mac スリープ中の挙動と対処法

| 9:00 時点の Mac 状態 | 挙動 |
|---------------------|------|
| ログイン中 + 通常稼働 | **そのまま 09:00 に実行** |
| ログイン中 + スリープ | **起床直後 (蓋を開けた瞬間 / 触った瞬間) に catch-up 実行** ← launchd 標準動作 |
| シャットダウン | **実行されない** (catch-up 対象外、9:00 はスキップされる) |
| 別ユーザでログイン | **実行されない** (LaunchAgents は対象ユーザのログイン中のみ) |

### 推奨運用

最低条件: **Mac が「ログイン状態」**。スリープしていても OK。

完璧を期すなら以下を設定 (システム設定 → バッテリー):
- 「ディスプレイがオフのときコンピュータを自動でスリープさせない」を ON
- (電源アダプタ接続時のみ ON でも可)

スケジュール変更で Mac を毎晩シャットダウンする運用ならば:
- 翌朝 8:55 までに Mac を起動 (= ログイン完了) しておけば 9:00 は通常通り実行
- ログイン直後にも `RunAtLoad` を `true` にしておけば「起動時に走る」が、Phase A は推奨しない (1 日 1 回ルールが崩れる)

---

## 7. 3 日間ステージング運用シミュレーション

### 開始日時提案

**5/4 (土) 朝 09:00 開始 → 5/6 (月) 朝 09:00 終了** (合計 3 回起動を観察)

| 日 | 時刻 | 何が起きるか | 主観察ポイント |
|---|------|-------------|---------------|
| 5/3 (本日) 中 | — | セットアップ完了 + 即時テスト | 動作確認、ログにエラーがないか |
| **5/4 朝 09:00** | Day 1 | **初回スケジュール起動** | 24 件検知 → cap 3 件投稿 (前回手動 dry-run 同等) |
| **5/5 朝 09:00** | Day 2 | 重複抑制確認 | 同じ rule_id は idempotency でスキップ、新規だけ通知 |
| **5/6 朝 09:00** | Day 3 | clean カウント観察 | 解消した指摘があれば consecutive_clean_days が 1 → 2 と進む |

### 毎朝 09:30 頃に確認するチェックリスト (5/4, 5/5, 5/6 の 3 回繰り返し)

```bash
# (1) 当日のログが追加されているか
ls -la ~/bpo-system/bpo-system/logs/daily-chatwork.out.log
# → 更新時刻が当日 09:00〜09:05 になっていれば成功

# (2) 当日の実行ログ末尾
tail -30 ~/bpo-system/bpo-system/logs/daily-chatwork.out.log
# → "日次チェック完了: {'posted_indications': N, 'errors': []}" を確認

# (3) launchd 状態 (run count が日数分カウントアップ、next firing が翌朝)
launchctl print "gui/$(id -u)/com.zynect.bpo.daily-chatwork" 2>/dev/null | \
  grep -E "state|next firing|run count|last exit code"

# (4) state DB の更新確認
ls -la ~/bpo-system/bpo-system/outputs/chatwork_state/pilotton_indications.json
# → 更新時刻が当日になっている

# (5) ChatWork ルームを目視 (rid 435851481)
# → [テスト] プレフィクス付き投稿が当日朝 09:00〜09:05 に来ているか
```

### 3 日間累積で評価する判定基準

```bash
# 投稿件数を集計
grep "投稿:" ~/bpo-system/bpo-system/logs/daily-chatwork.out.log | tail -3

# state DB 全レコードの status 分布
~/bpo-system/bpo-system/venv/bin/python3 -c "
import json
with open('/Users/trunktrunk/bpo-system/bpo-system/outputs/chatwork_state/pilotton_indications.json') as f:
    d = json.load(f)
print(f'総レコード数: {len(d[\"indications\"])}')
status = {}
for r in d['indications'].values():
    status[r['status']] = status.get(r['status'], 0) + 1
print('status 分布:', status)
"
```

### 判定基準 (3 日後の go/no-go)

| 指標 | OK 基準 | NG 基準 |
|------|---------|---------|
| 3 日連続でジョブ起動 | run count 3 以上 | 1〜2 (どこかで失敗) |
| エラーログ | err.log が空 or "WARN" のみ | "ERROR" / "Traceback" あり |
| ChatWork 投稿 | 各日 1 〜 3 件投稿 | 投稿ゼロ / 9 件超 (cap 機能異常) |
| 文面の自然さ | 担当者目線で違和感なし | 機械的・誤読・崩れ |
| 誤検知率 | < 5% (= 24 件中 1 件以下) | 5% 超過 (誤検知の修正が必要) |
| state DB 整合性 | indication_id がユニーク・status 矛盾なし | 重複 / 異常な status 値 |

### Phase B 移行判断

3 日間で **OK 基準を全項目満たす** → 内部レビュー残り 11 日継続 → 14 日経過時点で再評価 → 問題なければ pilotton 担当者を ChatWork ルームに招待 + `[テスト]` プレフィクス削除で本番化。

---

## トラブルシュート (確認順序: ログ → plist 構文 → パーミッション → PATH)

### 1. まずログを見る

```bash
tail -100 ~/bpo-system/bpo-system/logs/daily-chatwork.err.log
tail -100 ~/bpo-system/bpo-system/logs/daily-chatwork.out.log
```

### 2. plist 構文チェック

```bash
plutil -lint ~/Library/LaunchAgents/com.zynect.bpo.daily-chatwork.plist
# OK 以外なら plist の XML 構文エラー → リポジトリから再 cp
```

### 3. パーミッション確認

```bash
ls -la ~/Library/LaunchAgents/com.zynect.bpo.daily-chatwork.plist
# → -rw-r--r-- (644) であること、所有者が自分であること
```

### 4. PATH / venv 確認

```bash
# venv の python が存在するか
ls ~/bpo-system/bpo-system/venv/bin/python3

# 直接実行できるか
~/bpo-system/bpo-system/venv/bin/python3 --version
```

### 症状別早見表

| 症状 | 想定原因 | 対処 |
|------|---------|------|
| `launchctl list \| grep zynect` で何も出ない | load されていない | `launchctl load ...` を再実行 |
| 2 列目が `1` | Python スクリプトがエラー終了 | err.log を確認 |
| 2 列目が `78` | バイナリ不在 | `venv/bin/python3` の存在確認 |
| 2 列目が `127` | python3 のパス不正 | plist の `ProgramArguments` を絶対パスで再確認 |
| 「`CHATWORK_API_TOKEN 未設定`」ログ | `.env` 未読込 | `WorkingDirectory` 確認、`.env` の場所確認 |
| 起動時刻が JST にならない | TZ 未設定 | plist の `EnvironmentVariables` の `TZ=Asia/Tokyo` 確認 |
| ChatWork に投稿が来ない | API トークン無効 | `venv/bin/python3 scripts/daily_chatwork_check.py --client pilotton --dry-run --prefix "[テスト] "` で手動確認 |
| `Operation now in progress` エラー | 既に登録済み | 一旦 `unload` してから `load` |
| `bootstrap failed: 5: Input/output error` | 同名 Label が既にロード済み | `launchctl bootout gui/$(id -u)/com.zynect.bpo.daily-chatwork` してから再 bootstrap |

---

## 参考

- ADR-005: `docs/decisions/ADR-005-chatwork-indication-completion-monthly-loop.md`
- 旧版 (scheduler.py 経由の常駐方式): `docs/operations/chatwork_scheduler_setup.md`
- 日次スクリプト本体: `scripts/daily_chatwork_check.py`
- Apple 公式 launchd.plist man: `man launchd.plist` (ターミナルで実行)
