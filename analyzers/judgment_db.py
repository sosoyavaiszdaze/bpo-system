"""判断履歴のDB管理 & 学習反映（JSON ファイルベース）"""
import json
import logging
import os
from datetime import datetime, timedelta

log = logging.getLogger("bpo")

JUDGMENTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "judgments")
LEARNING_FILE = os.path.join(JUDGMENTS_DIR, "learning_history.json")


class JudgmentDB:
    """判断記録の管理クラス"""

    def __init__(self):
        """初期化: judgmentsディレクトリを作成"""
        os.makedirs(JUDGMENTS_DIR, exist_ok=True)

    def create_judgment(self, judgment_id, category, metadata, slack_ts="",
                        slack_channel="", timeout_at="", escalation_level="L1"):
        """新規判断レコードを作成"""
        record = {
            "judgment_id": judgment_id,
            "category": category,
            "metadata": metadata,
            "slack_ts": slack_ts,
            "slack_channel": slack_channel,
            "status": "pending",
            "escalation_level": escalation_level,
            "timeout_at": timeout_at,
            "created_at": datetime.now().isoformat(),
            "resolved_at": None,
            "action": None,
            "judge": None,
            "reason": None,
            "last_reminder_minutes": 0,
        }
        self._save(judgment_id, record)
        return record

    def get_judgment(self, judgment_id):
        """judgment_idで1件取得"""
        path = os.path.join(JUDGMENTS_DIR, f"{judgment_id}.json")
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        return None

    def get_pending_judgments(self):
        """未解決の判断一覧を取得"""
        pending = []
        for fname in os.listdir(JUDGMENTS_DIR):
            if fname == "learning_history.json" or not fname.endswith(".json"):
                continue
            path = os.path.join(JUDGMENTS_DIR, fname)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    record = json.load(f)
                if record.get("status") == "pending":
                    pending.append(record)
            except (json.JSONDecodeError, OSError):
                continue
        return pending

    def resolve_judgment(self, judgment_id, action, judge, reason, resolved_at):
        """判断を解決済みにする（JSON + Twenty CRM）"""
        record = self.get_judgment(judgment_id)
        if record:
            record["status"] = "resolved"
            record["action"] = action
            record["judge"] = judge
            record["reason"] = reason
            record["resolved_at"] = resolved_at
            self._save(judgment_id, record)
            self._add_to_learning_history(record)
            self._save_to_twenty_crm(record)

    def update_escalation_level(self, judgment_id, level):
        """エスカレーションレベルを更新"""
        record = self.get_judgment(judgment_id)
        if record:
            record["escalation_level"] = level
            self._save(judgment_id, record)

    def update_last_reminder(self, judgment_id, minutes):
        """最後のリマインダー時刻を更新"""
        record = self.get_judgment(judgment_id)
        if record:
            record["last_reminder_minutes"] = minutes
            self._save(judgment_id, record)

    def schedule_retry(self, judgment_id, delay_days):
        """リトライをスケジュール"""
        record = self.get_judgment(judgment_id)
        if record:
            record["retry_at"] = (datetime.now() + timedelta(days=delay_days)).isoformat()
            self._save(judgment_id, record)

    def _save(self, judgment_id, record):
        """レコードをJSONファイルに保存"""
        path = os.path.join(JUDGMENTS_DIR, f"{judgment_id}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(record, f, ensure_ascii=False, indent=2)

    def _save_to_twenty_crm(self, record):
        """Twenty CRM に判断結果を書き込み（JSONフォールバック付き）"""
        api_url = os.environ.get("TWENTY_API_URL", "")
        api_key = os.environ.get("TWENTY_API_KEY", "")
        if not api_url or not api_key:
            return

        import urllib.request
        payload = {
            "title": f"[Fraud判断] {record['judgment_id']}",
            "bodyV2": {"markdown": (
                f"# Fraud Judgment: {record['judgment_id']}\n"
                f"**Category:** {record['category']}\n"
                f"**Action:** {record['action']}\n"
                f"**Judge:** {record['judge']}\n"
                f"**Reason:** {record['reason']}\n"
                f"**Resolved:** {record['resolved_at']}\n"
            )},
        }
        try:
            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                f"{api_url}/notes", data=data, method="POST",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=10):
                log.info(f"Twenty CRM保存: {record['judgment_id']}")
        except Exception as e:
            log.warning(f"Twenty CRM保存失敗 (JSONフォールバック済): {e}")

    def _add_to_learning_history(self, record):
        """判断結果を学習履歴に蓄積"""
        history = self._load_learning_history()
        history.append({
            "judgment_id": record["judgment_id"],
            "category": record["category"],
            "metadata": record["metadata"],
            "action": record["action"],
            "judge": record["judge"],
            "resolved_at": record["resolved_at"],
        })
        with open(LEARNING_FILE, "w", encoding="utf-8") as f:
            json.dump(history, f, ensure_ascii=False, indent=2)

    def _load_learning_history(self):
        """学習履歴を読み込み"""
        if os.path.exists(LEARNING_FILE):
            try:
                with open(LEARNING_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError):
                return []
        return []

    def get_learning_stats(self):
        """学習統計: カテゴリ×アクション別の判断傾向"""
        history = self._load_learning_history()
        stats = {}
        for record in history:
            cat = record["category"]
            action = record["action"]
            stats.setdefault(cat, {})
            stats[cat][action] = stats[cat].get(action, 0) + 1
        return stats

    def get_auto_suggestion(self, category, metadata):
        """過去80%以上同じ判断なら自動提案を返す"""
        history = self._load_learning_history()
        similar = [r for r in history if r["category"] == category]

        if len(similar) < 10:
            return None

        client_id = metadata.get("client_id")
        client_similar = [r for r in similar if r["metadata"].get("client_id") == client_id]

        if len(client_similar) >= 5:
            action_counts = {}
            for r in client_similar:
                action_counts[r["action"]] = action_counts.get(r["action"], 0) + 1
            total = sum(action_counts.values())
            for action, count in action_counts.items():
                if count / total >= 0.80:
                    return action

        action_counts = {}
        for r in similar:
            action_counts[r["action"]] = action_counts.get(r["action"], 0) + 1
        total = sum(action_counts.values())
        for action, count in action_counts.items():
            if count / total >= 0.80:
                return action

        return None

    def generate_threshold_adjustment_recommendations(self):
        """学習データから閾値調整の推奨を生成"""
        stats = self.get_learning_stats()
        recommendations = []

        cv_stats = stats.get("cv_fraud_judgment", {})
        if cv_stats:
            total = sum(cv_stats.values())
            block_rate = cv_stats.get("block", 0) / total if total > 0 else 0
            if block_rate >= 0.80 and total >= 20:
                recommendations.append({
                    "type": "threshold_adjustment",
                    "category": "cv_fraud_judgment",
                    "suggestion": "CV付き不正でBlock判断が80%超。fake_ratio閾値を80%→60%に下げて自動ブロック範囲を拡大検討。",
                    "confidence": block_rate,
                    "sample_size": total,
                })

        pattern_stats = stats.get("new_pattern_confirmation", {})
        if pattern_stats:
            total = sum(pattern_stats.values())
            confirm_rate = pattern_stats.get("confirm_fraud", 0) / total if total > 0 else 0
            if confirm_rate >= 0.80 and total >= 15:
                recommendations.append({
                    "type": "threshold_adjustment",
                    "category": "new_pattern_confirmation",
                    "suggestion": "新種パターンでconfirm_fraud判断が80%超。confidence閾値を0.70→0.55に下げて自動対策範囲を拡大検討。",
                    "confidence": confirm_rate,
                    "sample_size": total,
                })

        return recommendations
