# ChatWork スケジューラ起動・停止手順 (ADR-005 / Day 3 D3)

> **対象**: 山本 (システム運用者)
> **目的**: BPO System の日次/月次 ChatWork ジョブを macOS で常駐させる手順
> **関連**: ADR-005、`integrations/scheduler.py`、`scripts/daily_chatwork_check.py`

---

## 1. 前提

- macOS (darwin) を想定。常時起動のサーバへ移す場合は cron 節を参照
- Python venv: `/Users/trunktrunk/bpo-system/bpo-system/venv`
- 必要 .env キー: `CHATWORK_API_TOKEN` / `CHATWORK_ROOM_ID_PILOTTON` / (任意) `CHATWORK_TEST_PREFIX`
- ジョブ時刻 (Asia/Tokyo):
  - **日次 09:00**: 指摘・解消通知
  - **月次 1日 10:00**: 月次レポート + PDF 添付

---

## 2. 内部レビュー期間（最初の2週間）の運用

`.env` に以下を追記して、投稿に `[テスト] ` プレフィクスを付与:

```bash
echo 'CHATWORK_TEST_PREFIX="[テスト] "' >> .env
```

2 週間後、誤検知率 5% 以下を確認したら以下で本番化:

```bash
sed -i '' 's|^CHATWORK_TEST_PREFIX=.*|CHATWORK_TEST_PREFIX=|' .env
```

---

## 3. 動作確認 (手動実行)

### 3.1 dry-run 確認 (ChatWork に投稿しない)

```bash
cd /Users/trunktrunk/bpo-system/bpo-system
venv/bin/python3 scripts/daily_chatwork_check.py --client pilotton --dry-run --prefix "[テスト] "
```

期待ログ:
```
[INFO] daily_chatwork: 日次 ChatWork チェック開始: client=pilotton dry_run=True ...
[INFO] daily_chatwork: 検知: N 件 upsert / clean 候補: M 件
[INFO] daily_chatwork: 日次チェック完了: {'posted_indications': X, 'posted_completions': Y, 'errors': []}
```

### 3.2 実投稿テスト

```bash
venv/bin/python3 scripts/daily_chatwork_check.py --client pilotton --prefix "[テスト] "
```

ChatWork ルーム rid 435851481 に投稿される。

### 3.3 月次レポート手動実行 (前月)

```bash
venv/bin/python3 scripts/monthly_chatwork_report.py --client pilotton --prefix "[テスト] "
```

### 3.4 月次レポート手動実行 (期間指定)

```bash
venv/bin/python3 scripts/monthly_chatwork_report.py --client pilotton --period 2026-04 --prefix "[テスト] "
```

---

## 4. 常駐起動: launchd (推奨, macOS)

### 4.1 plist ファイル作成

`~/Library/LaunchAgents/com.zynectmedia.bpo-scheduler.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.zynectmedia.bpo-scheduler</string>
    <key>ProgramArguments</key>
    <array>
        <string>/Users/trunktrunk/bpo-system/bpo-system/venv/bin/python3</string>
        <string>/Users/trunktrunk/bpo-system/bpo-system/integrations/scheduler.py</string>
    </array>
    <key>WorkingDirectory</key>
    <string>/Users/trunktrunk/bpo-system/bpo-system</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PYTHONPATH</key>
        <string>/Users/trunktrunk/bpo-system/bpo-system</string>
        <key>TZ</key>
        <string>Asia/Tokyo</string>
    </dict>
    <key>KeepAlive</key>
    <true/>
    <key>RunAtLoad</key>
    <true/>
    <key>StandardOutPath</key>
    <string>/Users/trunktrunk/bpo-system/bpo-system/logs/scheduler.out.log</string>
    <key>StandardErrorPath</key>
    <string>/Users/trunktrunk/bpo-system/bpo-system/logs/scheduler.err.log</string>
</dict>
</plist>
```

### 4.2 起動・停止

```bash
mkdir -p /Users/trunktrunk/bpo-system/bpo-system/logs

# 起動 (PC 再起動後も自動起動)
launchctl load ~/Library/LaunchAgents/com.zynectmedia.bpo-scheduler.plist

# 状態確認
launchctl list | grep bpo-scheduler

# ログ確認
tail -f logs/scheduler.out.log
tail -f logs/scheduler.err.log

# 停止
launchctl unload ~/Library/LaunchAgents/com.zynectmedia.bpo-scheduler.plist
```

### 4.3 plist の注意点

- `.env` は自動読み込みされる (load_dotenv が `.env` を参照)。`EnvironmentVariables` には記載不要
- macOS の省電力で深夜にスリープすると 09:00 ジョブが遅延する可能性あり。
  → 「システム設定 → バッテリー → スリープしない (電源接続時)」を有効化推奨
- 別 macOS ユーザでログインしているとジョブが走らない。サーバ常駐用途は cron / systemd を検討

---

## 5. 代替: crontab (Linux サーバ移行時)

```cron
# 日次 09:00 JST
0 9 * * * TZ=Asia/Tokyo cd /opt/bpo-system && venv/bin/python3 scripts/daily_chatwork_check.py --client pilotton >> logs/daily_chatwork.log 2>&1

# 月次 1日 10:00 JST
0 10 1 * * TZ=Asia/Tokyo cd /opt/bpo-system && venv/bin/python3 scripts/monthly_chatwork_report.py --client pilotton >> logs/monthly_chatwork.log 2>&1
```

cron は APScheduler 経由ではなく直接スクリプトを叩くので、`integrations/scheduler.py` を起動しない構成。複数ジョブを 1 プロセスでまとめたい場合は scheduler.py 経由が便利。

---

## 6. 障害時の自己監視

`scripts/daily_chatwork_check.py` の `main()` は致命的失敗時に `post_self_alert()` を呼び、
ChatWork に「🚨 BPO System 自己監視アラート」を投稿する。

- idempotency: 同一日・同一エラー内容なら 1 日 1 回までしか投稿されない (キー: `self_alert:YYYY-MM-DD:hash`)
- 自己監視通知さえ失敗する場合 (= ChatWork API トークン期限切れ等) は `logs/scheduler.err.log` に残るので
  最後の防波堤として **週 1 回はログ確認** を推奨

---

## 7. トラブルシューティング

| 症状 | 原因 | 対処 |
|------|------|------|
| 「CHATWORK_API_TOKEN 未設定」 | .env が読み込まれていない | `load_dotenv` が探す `.env` の絶対パスを確認 |
| 投稿が重複 | idempotency ストアが消えた | `state/chatwork_sent.json` を確認、必要なら復元 |
| 「指摘なし」しか出ない | analyzer 結果が空 / `data_available=False` | `pipeline.py run pilotton --report-version v3` を手動実行して挙動確認 |
| ジョブが走らない | launchd が落ちている | `launchctl list \| grep bpo-scheduler` でステータス確認 |
| PDF 添付サイズ警告 | Free プラン 5MB 超過 | Business プラン以上への移行検討、または PDF 軽量化 |
| state JSON 破損 | 同時書き込み | `state/chatwork_sent.json.tmp` が残っていれば手動削除、新規再作成される |
| 内部レビュー期間後に [テスト] が消えない | `CHATWORK_TEST_PREFIX` が空文字でなく未定義になっている | `.env` で空文字を明示: `CHATWORK_TEST_PREFIX=` |

---

## 8. 参考

- ADR-005: `docs/decisions/ADR-005-chatwork-indication-completion-monthly-loop.md`
- スケジューラ本体: `integrations/scheduler.py`
- 日次スクリプト: `scripts/daily_chatwork_check.py`
- 月次スクリプト: `scripts/monthly_chatwork_report.py`
