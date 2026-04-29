#!/usr/bin/env python3
"""Twenty CRM カスタムオブジェクトセットアップスクリプト

使用方法:
    TWENTY_API_URL=https://... TWENTY_API_KEY=... python3 scripts/setup_twenty_objects.py

作成されるオブジェクト:
- ActionLog: 運用アクション記録
- FraudJudgment: 不正判断記録
- HealthSnapshot: 日次ヘルススナップショット
- MonthlyReport: 月次レポート
- AdvisoryComment: アドバイザリーコメント
- RuleChangeLog: ルール変更記録
"""
import os
import json
import urllib.request
import sys

API_URL = os.environ.get("TWENTY_API_URL", "")
API_KEY = os.environ.get("TWENTY_API_KEY", "")

OBJECTS = [
    {
        "name": "ActionLog",
        "description": "広告運用アクションの記録（ブロック、入札変更、予算調整等）",
        "fields": [
            {"name": "clientId", "type": "TEXT", "description": "クライアントID"},
            {"name": "actionType", "type": "TEXT", "description": "アクション種別 (fraud_block, bid_change, budget_adjust)"},
            {"name": "platform", "type": "TEXT", "description": "プラットフォーム (google, meta, tiktok)"},
            {"name": "target", "type": "TEXT", "description": "対象 (IP, パブリッシャー, キャンペーン)"},
            {"name": "decision", "type": "TEXT", "description": "判断内容"},
            {"name": "decisionBy", "type": "TEXT", "description": "判断者 (system, user_name)"},
            {"name": "costSaved", "type": "NUMBER", "description": "推定コスト削減額"},
        ],
    },
    {
        "name": "FraudJudgment",
        "description": "不正検知の人間判断記録（Slack判断フロー連携）",
        "fields": [
            {"name": "judgmentId", "type": "TEXT", "description": "判断ID"},
            {"name": "category", "type": "TEXT", "description": "カテゴリ (cv_fraud, new_pattern, bid_reset)"},
            {"name": "status", "type": "TEXT", "description": "ステータス (pending, resolved, timeout)"},
            {"name": "action", "type": "TEXT", "description": "実行アクション"},
            {"name": "judge", "type": "TEXT", "description": "判断者"},
            {"name": "fraudRate", "type": "NUMBER", "description": "不正率"},
            {"name": "trueCvCount", "type": "NUMBER", "description": "真正CV数"},
        ],
    },
    {
        "name": "HealthSnapshot",
        "description": "日次のアカウントヘルススナップショット",
        "fields": [
            {"name": "clientId", "type": "TEXT", "description": "クライアントID"},
            {"name": "score", "type": "NUMBER", "description": "監査スコア (0-100)"},
            {"name": "grade", "type": "TEXT", "description": "グレード (A-F)"},
            {"name": "totalCost", "type": "NUMBER", "description": "総コスト"},
            {"name": "totalConversions", "type": "NUMBER", "description": "総CV数"},
            {"name": "issueCount", "type": "NUMBER", "description": "問題件数"},
            {"name": "alertCount", "type": "NUMBER", "description": "異常アラート件数"},
        ],
    },
    {
        "name": "MonthlyReport",
        "description": "月次集計レポート",
        "fields": [
            {"name": "clientId", "type": "TEXT", "description": "クライアントID"},
            {"name": "month", "type": "TEXT", "description": "対象月 (YYYY-MM)"},
            {"name": "avgScore", "type": "NUMBER", "description": "平均スコア"},
            {"name": "totalActions", "type": "NUMBER", "description": "アクション総数"},
            {"name": "totalSavings", "type": "NUMBER", "description": "推定コスト削減額合計"},
        ],
    },
    {
        "name": "AdvisoryComment",
        "description": "アクションに対するアドバイザリーコメント",
        "fields": [
            {"name": "actionLogId", "type": "TEXT", "description": "関連ActionLog ID"},
            {"name": "author", "type": "TEXT", "description": "著者"},
            {"name": "commentType", "type": "TEXT", "description": "種別 (advice, approval, rejection)"},
            {"name": "suggestedAction", "type": "TEXT", "description": "推奨アクション"},
        ],
    },
    {
        "name": "RuleChangeLog",
        "description": "監査ルール・閾値の変更記録",
        "fields": [
            {"name": "metric", "type": "TEXT", "description": "対象メトリクス"},
            {"name": "oldThreshold", "type": "NUMBER", "description": "旧閾値"},
            {"name": "newThreshold", "type": "NUMBER", "description": "新閾値"},
            {"name": "reason", "type": "TEXT", "description": "変更理由"},
            {"name": "confidence", "type": "NUMBER", "description": "確信度"},
            {"name": "autoApplied", "type": "BOOLEAN", "description": "自動適用されたか"},
        ],
    },
]


def main():
    if not API_URL or not API_KEY:
        print("ERROR: TWENTY_API_URL と TWENTY_API_KEY を環境変数に設定してください")
        sys.exit(1)

    print(f"Twenty CRM: {API_URL}")
    print(f"カスタムオブジェクト {len(OBJECTS)}件を作成します...\n")

    for obj in OBJECTS:
        print(f"  Creating: {obj['name']} — {obj['description']}")
        print(f"    Fields: {len(obj['fields'])}件")
        for field in obj["fields"]:
            print(f"      - {field['name']} ({field['type']}): {field['description']}")
        # NOTE: Twenty Metadata API のエンドポイントは環境依存
        # 実際のAPI呼び出しは Twenty のバージョンに合わせて調整
        print("    → Twenty Metadata API未実装。手動作成またはAPI確認後に自動化。")
        print()

    print("完了。Twenty の管理画面から上記オブジェクトを作成してください。")
    print("作成後、outputs/crm_twenty.py のNotes APIをカスタムオブジェクトAPIに差し替えます。")


if __name__ == "__main__":
    main()
