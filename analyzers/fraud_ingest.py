"""Fraud データ取込 — AdTruth SDK / CSV フォールバック"""
import os
import csv
import logging

log = logging.getLogger("bpo")


def ingest_fraud_data(client_id, client_cfg, data):
    """不正検知用データの取込

    優先順:
    1. AdTruth SDK (API接続)
    2. CSV フォールバック (手動エクスポート)
    3. ヒューリスティック生成 (unified format から推定)

    Args:
        client_id: クライアントID
        client_cfg: クライアント設定
        data: unified format データ
    Returns:
        dict: fraud_data (ip_scores, publisher_scores, click_details)
    """
    # 1. AdTruth SDK
    adtruth_key = os.environ.get("ADTRUTH_API_KEY_YAMAMOTO", "")
    if adtruth_key:
        try:
            result = _fetch_adtruth(client_id, adtruth_key, client_cfg)
            if result:
                log.info(f"[{client_id}] AdTruth SDK からFraudデータ取込完了")
                return result
        except Exception as e:
            log.warning(f"[{client_id}] AdTruth SDK エラー、CSVフォールバック: {e}")

    # 2. CSV フォールバック
    data_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
    csv_path = os.path.join(data_dir, f"{client_id}_fraud_data.csv")
    if os.path.exists(csv_path):
        result = _load_csv_fraud(csv_path)
        log.info(f"[{client_id}] CSV Fraudデータ取込: {csv_path}")
        return result

    # 3. ヒューリスティック（unified format から推定）
    result = _generate_heuristic(data)
    log.info(f"[{client_id}] ヒューリスティックFraudデータ生成")
    return result


def _fetch_adtruth(client_id, api_key, client_cfg):
    """AdTruth SDK からデータ取得（スタブ）

    TODO: AdTruth SDK を pip install 後、実装
    - AdTruth Device Authentication API
    - リアルタイム不正スコア取得
    - IP レピュテーション連携
    """

    # スタブ — AdTruth API接続コード
    # base_url = "https://api.adtruth.com/v1"
    # headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    # endpoint = f"{base_url}/fraud/scores/{client_id}"
    # ...
    log.debug(f"[{client_id}] AdTruth SDK: スタブモード（API未実装）")
    return None


def _load_csv_fraud(csv_path):
    """CSV からフラウドデータ読込

    期待CSV形式:
    ip,publisher,click_id,score,cost,timestamp,fraud_type
    192.168.1.100,example.com,abc123,0.95,150,2026-01-01 12:00,click_farm
    """
    fraud_items = []

    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            fraud_items.append({
                "ip": row.get("ip", ""),
                "publisher": row.get("publisher", ""),
                "click_id": row.get("click_id", ""),
                "score": float(row.get("score", 0)),
                "cost": float(row.get("cost", 0)),
                "timestamp": row.get("timestamp", ""),
                "fraud_type": row.get("fraud_type", "unknown"),
                "placement": row.get("placement", ""),
            })

    total_cost = sum(f["cost"] for f in fraud_items)
    suspicious = [f for f in fraud_items if f["score"] >= 0.6]

    return {
        "source": "csv",
        "fraud_items": fraud_items,
        "total_items": len(fraud_items),
        "suspicious_count": len(suspicious),
        "total_cost": total_cost,
        "suspicious_cost": sum(f["cost"] for f in suspicious),
    }


def _generate_heuristic(data):
    """unified format データからヒューリスティックにフラウド指標を推定

    推定ロジック:
    - CTR異常高 (>20%) → ボット疑惑
    - CVR異常低 + 高CTR → クリックファーム
    - CPC異常低 (<¥5) → 低品質トラフィック
    """
    campaigns = data.get("campaigns", [])
    fraud_items = []

    for camp in campaigns:
        score = 0.0
        reasons = []
        ctr = camp.get("ctr", 0)
        clicks = camp.get("clicks", 0)
        cv = camp.get("conversions", 0)
        cost = camp.get("cost", 0)
        cpc = cost / clicks if clicks > 0 else 0

        # CTR異常高
        if ctr > 20:
            score += 0.4
            reasons.append(f"CTR異常高 {ctr:.1f}%")
        elif ctr > 10:
            score += 0.2
            reasons.append(f"CTR高 {ctr:.1f}%")

        # CVR異常低 + 高CTR
        if clicks > 100 and cv == 0 and ctr > 5:
            score += 0.3
            reasons.append("高CTR+ゼロCV")

        # CPC異常低
        if cpc > 0 and cpc < 5:
            score += 0.2
            reasons.append(f"CPC ¥{cpc:.0f}")

        if score >= 0.3:
            fraud_items.append({
                "ip": "",
                "publisher": "",
                "campaign": camp.get("campaign", ""),
                "platform": camp.get("platform", ""),
                "score": min(score, 1.0),
                "cost": cost,
                "fraud_type": "heuristic",
                "reasons": reasons,
            })

    return {
        "source": "heuristic",
        "fraud_items": fraud_items,
        "total_items": len(fraud_items),
        "suspicious_count": len([f for f in fraud_items if f["score"] >= 0.6]),
        "total_cost": sum(f["cost"] for f in fraud_items),
    }
