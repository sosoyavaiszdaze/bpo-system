"""異常検知 - 前日比較で急変を検出し原因候補を提示"""
import os
import json
import logging
from datetime import datetime, timedelta

log = logging.getLogger("bpo")

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")


def _load_previous(client_id):
    """前日データを読み込み"""
    prev_path = os.path.join(DATA_DIR, f"{client_id}_previous.json")
    if os.path.exists(prev_path):
        with open(prev_path, "r", encoding="utf-8") as f:
            return json.load(f)
    return None


def _save_current(client_id, data):
    """今日のデータを次回比較用に保存"""
    prev_path = os.path.join(DATA_DIR, f"{client_id}_previous.json")
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(prev_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, default=str)


def detect_anomalies(client_id, data, thresholds):
    """異常検知と原因候補の提示"""
    anomaly_cfg = thresholds.get("anomaly", {})
    cpa_thresh = anomaly_cfg.get("cpa_increase_pct", 20)
    ctr_thresh = anomaly_cfg.get("ctr_decrease_pct", 15)
    cpm_thresh = anomaly_cfg.get("cpm_increase_pct", 25)
    freq_max = anomaly_cfg.get("frequency_max", 4.0)

    alerts = []
    campaigns = data.get("campaigns", [])
    previous = _load_previous(client_id)

    # キャンペーン別チェック
    for camp in campaigns:
        name = camp.get("campaign", "unknown")
        freq = camp.get("frequency", 0)

        # フリークエンシー過多
        if freq >= freq_max:
            alerts.append({
                "type": "frequency_fatigue",
                "campaign": name,
                "message": f"フリークエンシー {freq:.1f} がしきい値 {freq_max} を超過",
                "cause": "クリエイティブ疲弊の可能性",
                "action": "新クリエイティブ追加またはオーディエンス拡張",
                "severity": "warning",
            })

    # 前日比較
    if previous:
        prev_totals = previous.get("totals", {})
        curr_totals = data.get("totals", {})

        prev_cpa = prev_totals.get("avg_cpa", 0)
        curr_cpa = curr_totals.get("avg_cpa", 0)
        if prev_cpa > 0:
            cpa_change = ((curr_cpa - prev_cpa) / prev_cpa) * 100
            if cpa_change >= cpa_thresh:
                alerts.append({
                    "type": "cpa_spike",
                    "message": f"CPA {cpa_change:+.1f}% 上昇 (¥{prev_cpa:,.0f} → ¥{curr_cpa:,.0f})",
                    "cause": "競合増加、クリエイティブ疲弊、または季節要因の可能性",
                    "action": "キャンペーン別CPAを確認し、急上昇した個別キャンペーンを特定",
                    "severity": "critical",
                })

        prev_ctr = prev_totals.get("avg_ctr", 0)
        curr_ctr = curr_totals.get("avg_ctr", 0)
        if prev_ctr > 0:
            ctr_change = ((curr_ctr - prev_ctr) / prev_ctr) * 100
            if ctr_change <= -ctr_thresh:
                alerts.append({
                    "type": "ctr_drop",
                    "message": f"CTR {ctr_change:+.1f}% 低下 ({prev_ctr:.2f}% → {curr_ctr:.2f}%)",
                    "cause": "広告の関連性低下またはオーディエンス飽和",
                    "action": "広告テキスト・画像の更新を検討",
                    "severity": "warning",
                })

    # 今日のデータを保存
    _save_current(client_id, data)

    result = {
        "alerts": alerts,
        "alert_count": len(alerts),
        "critical_count": len([a for a in alerts if a["severity"] == "critical"]),
        "has_previous_data": previous is not None,
    }

    log.info(f"[{client_id}] 異常検知完了: {len(alerts)}件のアラート")
    return result
