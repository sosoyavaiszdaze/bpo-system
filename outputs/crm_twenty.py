"""Twenty CRM 統合管理モジュール — 全カスタムオブジェクトのCRUD"""
import os
import json
import logging
import urllib.request
import urllib.parse
from datetime import datetime

log = logging.getLogger("bpo")

TWENTY_API_URL = os.environ.get("TWENTY_API_URL", "")
TWENTY_API_KEY = os.environ.get("TWENTY_API_KEY", "")


class TwentyCRM:
    """Twenty CRM へのデータ書き込みを統合管理"""

    def __init__(self, api_url=None, api_key=None):
        """初期化"""
        self.api_url = api_url or TWENTY_API_URL
        self.api_key = api_key or TWENTY_API_KEY

    # ── ActionLog ──────────────────────────────────────────

    def save_action_log(self, client_id, action_type, platform, title,
                         description="", target="", decision="", decision_by="system",
                         metric_before=None, metric_after=None, cost_saved=0):
        """アクションログを保存"""
        payload = {
            "title": f"[{action_type}] {client_id} - {title}",
            "bodyV2": {"markdown": (
                f"# Action Log\n"
                f"**Client:** {client_id}\n"
                f"**Type:** {action_type}\n"
                f"**Platform:** {platform}\n"
                f"**Target:** {target}\n"
                f"**Decision:** {decision}\n"
                f"**Decision By:** {decision_by}\n"
                f"**Cost Saved:** ¥{cost_saved:,.0f}\n"
                f"**Timestamp:** {datetime.utcnow().isoformat()}\n"
                f"\n## Description\n{description}\n"
                + (f"\n## Metrics Before\n```json\n{json.dumps(metric_before, ensure_ascii=False)}\n```\n" if metric_before else "")
                + (f"\n## Metrics After\n```json\n{json.dumps(metric_after, ensure_ascii=False)}\n```\n" if metric_after else "")
            )},
        }
        return self._graphql_mutate("createNote", payload)

    # ── FraudJudgment ─────────────────────────────────────

    def save_fraud_judgment(self, judgment_data):
        """Fraud判断データを保存"""
        payload = {
            "title": f"[Fraud判断] {judgment_data.get('judgment_id', '')}",
            "bodyV2": {"markdown": (
                f"# Fraud Judgment\n"
                f"**ID:** {judgment_data.get('judgment_id', '')}\n"
                f"**Category:** {judgment_data.get('category', '')}\n"
                f"**Status:** {judgment_data.get('status', 'pending')}\n"
                f"**Client:** {judgment_data.get('metadata', {}).get('client_id', '')}\n"
                f"**Platform:** {judgment_data.get('metadata', {}).get('platform', '')}\n"
                f"**Action:** {judgment_data.get('action', 'pending')}\n"
                f"**Judge:** {judgment_data.get('judge', '')}\n"
                f"**Created:** {judgment_data.get('created_at', '')}\n"
                f"**Resolved:** {judgment_data.get('resolved_at', '')}\n"
            )},
        }
        return self._graphql_mutate("createNote", payload)

    def update_fraud_judgment(self, judgment_id, updates):
        """Fraud判断データを更新（Notesの追記として実装）"""
        update_text = "\n".join(f"**{k}:** {v}" for k, v in updates.items())
        payload = {
            "title": f"[Fraud判断更新] {judgment_id}",
            "bodyV2": {"markdown": f"# Update: {judgment_id}\n{update_text}\n**Updated:** {datetime.utcnow().isoformat()}"},
        }
        return self._graphql_mutate("createNote", payload)

    # ── HealthSnapshot ────────────────────────────────────

    def save_health_snapshot(self, client_id, snapshot_data):
        """ヘルススナップショットを保存"""
        audit = snapshot_data.get("ads_audit") or {}
        anomalies = snapshot_data.get("anomalies") or {}
        waste = snapshot_data.get("waste") or {}

        payload = {
            "title": f"[Health] {client_id} - Score {audit.get('score', 'N/A')} ({audit.get('grade', '?')})",
            "bodyV2": {"markdown": (
                f"# Health Snapshot: {client_id}\n"
                f"**Date:** {datetime.utcnow().strftime('%Y-%m-%d')}\n"
                f"**Score:** {audit.get('score', 0)} / 100\n"
                f"**Grade:** {audit.get('grade', '?')}\n"
                f"**Campaigns:** {audit.get('total_campaigns', 0)}\n"
                f"**Total Cost:** ¥{audit.get('total_cost', 0):,.0f}\n"
                f"**Total CV:** {audit.get('total_conversions', 0)}\n"
                f"**Avg CPA:** ¥{audit.get('avg_cpa', 0):,.0f}\n"
                f"**Issues:** {audit.get('failed_checks', 0)}\n"
                f"**Anomaly Alerts:** {anomalies.get('alert_count', 0)}\n"
                f"**Waste Cost:** ¥{waste.get('total_waste_cost', 0):,.0f}\n"
            )},
        }
        return self._graphql_mutate("createNote", payload)

    # ── MonthlyReport ─────────────────────────────────────

    def generate_monthly_report(self, client_id, month):
        """月次レポートを自動生成（ActionLog + HealthSnapshot集計）"""
        log.info(f"[{client_id}] 月次レポート生成: {month}")

        actions = self.get_client_actions(client_id, month=month)
        health = self.get_client_health_history(client_id, days=30)

        if not actions and not health:
            return {
                "client_id": client_id, "month": month,
                "generated_at": datetime.utcnow().isoformat(),
                "note": "Twenty CRM未接続またはデータなし",
            }

        action_types = {}
        total_savings = 0
        for a in actions:
            title = a.get("title", "")
            for t in ["fraud_block", "bid_change", "budget_adjust", "daily_audit"]:
                if f"[{t}]" in title:
                    action_types[t] = action_types.get(t, 0) + 1
                    break

        scores = []
        for h in health:
            body = h.get("bodyV2", {}).get("markdown", "")
            for line in body.split("\n"):
                if "**Score:**" in line:
                    try:
                        scores.append(float(line.split("**Score:**")[1].split("/")[0].strip()))
                    except (ValueError, IndexError):
                        pass

        avg_score = round(sum(scores) / len(scores), 1) if scores else 0

        return {
            "client_id": client_id, "month": month,
            "generated_at": datetime.utcnow().isoformat(),
            "total_actions": len(actions),
            "action_by_type": action_types,
            "avg_score": avg_score,
            "health_snapshots": len(health),
            "total_savings": total_savings,
        }

    def save_monthly_report(self, report_data):
        """月次レポートを保存"""
        payload = {
            "title": f"[Monthly] {report_data.get('client_id', '')} - {report_data.get('month', '')}",
            "bodyV2": {"markdown": (
                f"# Monthly Report\n"
                f"**Client:** {report_data.get('client_id', '')}\n"
                f"**Month:** {report_data.get('month', '')}\n"
                f"**Generated:** {report_data.get('generated_at', '')}\n"
            )},
        }
        return self._graphql_mutate("createNote", payload)

    # ── AdvisoryComment ───────────────────────────────────

    def save_advisory_comment(self, action_log_id, author, content,
                               comment_type="advice", suggested_action=""):
        """アドバイザリーコメントを保存"""
        payload = {
            "title": f"[Advisory] {action_log_id} by {author}",
            "bodyV2": {"markdown": (
                f"# Advisory Comment\n"
                f"**Action Log:** {action_log_id}\n"
                f"**Author:** {author}\n"
                f"**Type:** {comment_type}\n"
                f"**Suggested Action:** {suggested_action}\n"
                f"\n## Content\n{content}\n"
            )},
        }
        return self._graphql_mutate("createNote", payload)

    # ── RuleChangeLog ─────────────────────────────────────

    def save_rule_change(self, change_data):
        """ルール変更ログを保存"""
        payload = {
            "title": f"[RuleChange] {change_data.get('metric', '')} - {change_data.get('reason', '')}",
            "bodyV2": {"markdown": (
                f"# Rule Change Log\n"
                f"**Metric:** {change_data.get('metric', '')}\n"
                f"**Old Threshold:** {change_data.get('old_threshold', '')}\n"
                f"**New Threshold:** {change_data.get('new_threshold', '')}\n"
                f"**Reason:** {change_data.get('reason', '')}\n"
                f"**Confidence:** {change_data.get('confidence', 0)*100:.0f}%\n"
                f"**Auto Applied:** {change_data.get('auto_applied', False)}\n"
                f"**Timestamp:** {datetime.utcnow().isoformat()}\n"
            )},
        }
        return self._graphql_mutate("createNote", payload)

    # ── クエリ ────────────────────────────────────────────

    def get_client_actions(self, client_id, month=None, action_type=None):
        """ActionLogフィルタ取得"""
        notes = self._rest_get("/notes", {"filter": json.dumps({"title": {"like": f"%{client_id}%"}})})
        if not notes:
            return []
        results = notes if isinstance(notes, list) else notes.get("data", {}).get("notes", [])
        if month:
            results = [n for n in results if month in n.get("title", "")]
        if action_type:
            results = [n for n in results if f"[{action_type}]" in n.get("title", "")]
        return results

    def get_client_health_history(self, client_id, days=30):
        """HealthSnapshot取得（直近N日分）"""
        notes = self._rest_get("/notes", {"filter": json.dumps({"title": {"like": f"%[Health] {client_id}%"}})})
        if not notes:
            return []
        results = notes if isinstance(notes, list) else notes.get("data", {}).get("notes", [])
        if days and results:
            from datetime import timedelta
            cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
            results = [n for n in results if n.get("createdAt", "") >= cutoff]
        return results

    def get_monthly_report(self, client_id, month):
        """MonthlyReport取得"""
        notes = self._rest_get("/notes", {"filter": json.dumps({"title": {"like": f"%[Monthly] {client_id} - {month}%"}})})
        if not notes:
            return None
        results = notes if isinstance(notes, list) else notes.get("data", {}).get("notes", [])
        return results[0] if results else None

    # ── Client管理 ────────────────────────────────────────

    def list_clients(self, active_only=True):
        """全クライアントを取得"""
        from engine.models import ClientConfig
        notes = self._rest_get("/notes", {"filter": json.dumps({"title": {"like": "%[Client]%"}})})
        if not notes:
            return []
        results = notes if isinstance(notes, list) else notes.get("data", {}).get("notes", [])
        clients = []
        for note in results:
            try:
                data = self._parse_client_note(note)
                if data and (not active_only or data.get("active", True)):
                    clients.append(ClientConfig.from_crm(data))
            except Exception:
                continue
        return clients

    def get_client(self, client_id):
        """単一クライアント取得"""
        from engine.models import ClientConfig
        notes = self._rest_get("/notes", {"filter": json.dumps({"title": {"like": f"%[Client] {client_id}%"}})})
        if not notes:
            return None
        results = notes if isinstance(notes, list) else notes.get("data", {}).get("notes", [])
        for note in results:
            data = self._parse_client_note(note)
            if data and data.get("clientId") == client_id:
                return ClientConfig.from_crm(data)
        return None

    def update_client_last_audit(self, client_id, timestamp):
        """監査完了時に lastAuditAt を更新"""
        payload = {
            "title": f"[ClientUpdate] {client_id} lastAudit",
            "bodyV2": {"markdown": f"**lastAuditAt:** {timestamp.isoformat() if hasattr(timestamp, 'isoformat') else timestamp}"},
        }
        return self._graphql_mutate("createNote", payload)

    def upsert_client(self, config):
        """クライアント作成/更新（移行スクリプト用）"""
        payload = {
            "title": f"[Client] {config.client_id}",
            "bodyV2": {"markdown": (
                f"# Client: {config.client_id}\n"
                f"**name:** {config.name}\n"
                f"**active:** {config.active}\n"
                f"**objective:** {config.objective}\n"
                f"**googleCustomerId:** {config.google_customer_id}\n"
                f"**metaAccountId:** {config.meta_account_id}\n"
                f"**tiktokAdvertiserId:** {config.tiktok_advertiser_id}\n"
                f"**slackChannel:** {config.slack_channel}\n"
                f"**slackWebhookEnv:** {config.slack_webhook_env}\n"
                f"**featuresAdtruth:** {config.features.adtruth}\n"
                f"**featuresSeoAudit:** {config.features.seo_audit}\n"
                f"**featuresClaudeAnalysis:** {config.features.claude_analysis}\n"
                f"**scheduleCron:** {config.schedule_cron}\n"
                f"**timezone:** {config.timezone}\n"
            )},
        }
        return self._graphql_mutate("createNote", payload)

    def _parse_client_note(self, note):
        """NotesからClientデータをパース"""
        body = note.get("bodyV2", {}).get("markdown", note.get("body", ""))
        data = {}
        for line in body.split("\n"):
            if "**" in line and ":**" in line:
                try:
                    key = line.split("**")[1].rstrip(":")
                    value = line.split(":** ")[1].strip()
                    if value.lower() == "true":
                        value = True
                    elif value.lower() == "false":
                        value = False
                    data[key] = value
                except (IndexError, ValueError):
                    continue
        return data if data.get("clientId") or data.get("name") else None

    # ── 共通 ──────────────────────────────────────────────

    def _rest_get(self, endpoint, params=None):
        """Twenty CRM REST GET リクエスト"""
        if not self.api_url or not self.api_key:
            return None
        try:
            url = f"{self.api_url}{endpoint}"
            if params:
                query = urllib.parse.urlencode(params)
                url = f"{url}?{query}"
            req = urllib.request.Request(
                url, method="GET",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                return json.loads(resp.read())
        except Exception as e:
            log.warning(f"Twenty CRM GET失敗 ({endpoint}): {e}")
            return None

    def _graphql_mutate(self, operation, variables):
        """Twenty CRM GraphQL mutation 実行"""
        if not self.api_url or not self.api_key:
            log.debug("Twenty CRM未設定。スキップ。")
            return None

        try:
            data = json.dumps(variables).encode("utf-8")
            req = urllib.request.Request(
                f"{self.api_url}/notes",
                data=data, method="POST",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                result = json.loads(resp.read())
                note_id = result.get("data", {}).get("createNote", {}).get("id", "")
                log.debug(f"Twenty CRM保存: {operation} → {note_id}")
                return note_id
        except Exception as e:
            log.warning(f"Twenty CRM {operation}失敗: {e}")
            return None

    def _graphql_query(self, query):
        """Twenty CRM GraphQL query 実行"""
        if not self.api_url or not self.api_key:
            return None
        try:
            data = json.dumps({"query": query}).encode("utf-8")
            req = urllib.request.Request(
                f"{self.api_url}/graphql",
                data=data, method="POST",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                return json.loads(resp.read())
        except Exception as e:
            log.warning(f"Twenty CRM query失敗: {e}")
            return None
