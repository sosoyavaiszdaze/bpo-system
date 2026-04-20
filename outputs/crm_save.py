"""CRM保存 - Twenty CRM APIにレポートデータを保存"""
import os
import json
import logging
import urllib.request

log = logging.getLogger("bpo")

TWENTY_API_URL = "http://204.168.139.193:3000/rest"
TWENTY_API_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiI4ZGE5ZTZiZS02M2QyLTQ5ZDEtYjk2ZC0wM2VjYzczYjU4MDkiLCJ0eXBlIjoiQVBJX0tFWSIsIndvcmtzcGFjZUlkIjoiOGRhOWU2YmUtNjNkMi00OWQxLWI5NmQtMDNlY2M3M2I1ODA5IiwiaWF0IjoxNzc0Njg3MjY5LCJleHAiOjQ5MjgyODcyNjgsImp0aSI6IjA1MDZmZjkyLWNkODEtNDE5Yy05ZjhiLWQyMTkzMjM2Njk0ZSJ9.oxdS0JdomEHFdNwIpm64YYfyLC4R_F1SgkNKNMfWzME"


def save_to_crm(client_id, results, config):
    body_md = _build_markdown(client_id, results)
    timestamp = results.get("timestamp", "")[:10]
    client_name = results.get("client_name", client_id)
    audit = results.get("ads_audit") or {}
    score = audit.get("score", "N/A")
    grade = audit.get("grade", "?")

    payload = {
        "title": f"[BPO] {client_name} {timestamp} - Score {score} ({grade})",
        "bodyV2": {
            "markdown": body_md
        }
    }

    try:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            f"{TWENTY_API_URL}/notes",
            data=data,
            method="POST",
            headers={
                "Authorization": f"Bearer {TWENTY_API_KEY}",
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read())
            note_id = result.get("data", {}).get("createNote", {}).get("id", "")
            log.info(f"[{client_id}] CRM保存完了: Note ID {note_id}")
            return True
    except Exception as e:
        log.error(f"[{client_id}] CRM保存失敗: {e}")
        return False


def _build_markdown(client_id, results):
    audit = results.get("ads_audit") or {}
    anomalies = results.get("anomalies") or {}
    waste = results.get("waste") or {}
    timestamp = results.get("timestamp", "")[:10]

    lines = []
    lines.append(f"# BPO Daily Report: {timestamp}")
    lines.append(f"**Client:** {results.get('client_name', client_id)}")
    lines.append("")
    lines.append(f"## Health Score: {audit.get('score', 'N/A')} / 100 ({audit.get('grade', '?')})")
    lines.append(f"- Campaigns: {audit.get('total_campaigns', 0)}")
    lines.append(f"- Total Cost: Y{audit.get('total_cost', 0):,.0f}")
    lines.append(f"- Total CV: {audit.get('total_conversions', 0):.0f}")
    lines.append(f"- Avg CPA: Y{audit.get('avg_cpa', 0):,.0f}")
    lines.append(f"- Avg CTR: {audit.get('avg_ctr', 0):.2f}%")
    lines.append("")

    issues = audit.get("issues", [])
    if issues:
        lines.append(f"## Issues ({len(issues)})")
        for i in issues:
            lines.append(f"- **[{i.get('severity', '').upper()}]** {i['campaign']}: {i['issue']}")
        lines.append("")

    alerts = anomalies.get("alerts", [])
    if alerts:
        lines.append(f"## Anomaly Alerts ({len(alerts)})")
        for a in alerts:
            lines.append(f"- **{a.get('campaign', 'Overall')}**: {a['message']}")
            lines.append(f"  - Cause: {a['cause']}")
            lines.append(f"  - Action: {a['action']}")
        lines.append("")

    waste_items = waste.get("waste_items", [])
    if waste_items:
        lines.append(f"## Wasted Budget: {waste.get('potential_savings', 'Y0')}")
        for w in waste_items:
            lines.append(f"- **{w['campaign']}**: {w['message']}")
        lines.append("")

    quick_wins = audit.get("quick_wins", [])
    if quick_wins:
        lines.append(f"## Quick Wins ({len(quick_wins)})")
        for q in quick_wins:
            lines.append(f"- **{q['campaign']}**: {q['action']}")

    return "\n".join(lines)
