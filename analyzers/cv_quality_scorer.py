"""CVクオリティスコア: 各CVの「実在度」を自動判定し、偽CVを除外した真のCV数で複合判定を行う。"""
import logging

log = logging.getLogger("bpo")

QUALITY_SIGNALS = {
    "server_side_verified": {"weight": 0.20, "description": "サーバーサイドトラッキングで確認済み"},
    "click_to_cv_time_normal": {"weight": 0.15, "description": "クリック→CV時間が5秒以上"},
    "crm_email_opened": {"weight": 0.10, "description": "CV後にメール開封あり"},
    "crm_login": {"weight": 0.10, "description": "CV後にログインあり"},
    "crm_page_view": {"weight": 0.05, "description": "CV後にページ閲覧あり"},
    "crm_phone_verified": {"weight": 0.10, "description": "電話番号が実在"},
    "email_not_disposable": {"weight": 0.10, "description": "使い捨てメールでない"},
    "form_completion_time_human": {"weight": 0.10, "description": "フォーム入力時間が人間的(3-120秒)"},
    "ip_not_residential_proxy": {"weight": 0.10, "description": "レジデンシャルプロキシでない"},
}

REAL_CV_THRESHOLD = 0.50
FAKE_CV_THRESHOLD = 0.25


def score_conversion(cv_data):
    """1件のCVに対してクオリティスコアを算出"""
    score = 0.0
    signals_met = []
    signals_failed = []

    if cv_data.get("server_side_verified", False):
        score += QUALITY_SIGNALS["server_side_verified"]["weight"]
        signals_met.append("server_side_verified")
    else:
        signals_failed.append("server_side_verified")

    ct = cv_data.get("click_to_cv_seconds")
    if ct is not None and ct >= 5.0:
        score += QUALITY_SIGNALS["click_to_cv_time_normal"]["weight"]
        signals_met.append("click_to_cv_time_normal")
    else:
        signals_failed.append("click_to_cv_time_normal")

    for key in ["crm_email_opened", "crm_login", "crm_page_view", "crm_phone_verified"]:
        if cv_data.get(key, False):
            score += QUALITY_SIGNALS[key]["weight"]
            signals_met.append(key)
        else:
            signals_failed.append(key)

    if not cv_data.get("email_is_disposable", True):
        score += QUALITY_SIGNALS["email_not_disposable"]["weight"]
        signals_met.append("email_not_disposable")
    else:
        signals_failed.append("email_not_disposable")

    ft = cv_data.get("form_completion_seconds")
    if ft is not None and 3.0 <= ft <= 120.0:
        score += QUALITY_SIGNALS["form_completion_time_human"]["weight"]
        signals_met.append("form_completion_time_human")
    else:
        signals_failed.append("form_completion_time_human")

    if not cv_data.get("ip_is_residential_proxy", True):
        score += QUALITY_SIGNALS["ip_not_residential_proxy"]["weight"]
        signals_met.append("ip_not_residential_proxy")
    else:
        signals_failed.append("ip_not_residential_proxy")

    if score >= REAL_CV_THRESHOLD:
        classification = "real"
    elif score < FAKE_CV_THRESHOLD:
        classification = "fake"
    else:
        classification = "uncertain"

    return {
        "cv_id": cv_data.get("cv_id"),
        "quality_score": round(score, 3),
        "classification": classification,
        "signals_met": signals_met,
        "signals_failed": signals_failed,
    }


def calculate_true_cv_count(conversions):
    """配信面/パブリッシャー単位で「真のCV数」を算出"""
    scored = [score_conversion(cv) for cv in conversions]
    real_cvs = [s for s in scored if s["classification"] == "real"]
    fake_cvs = [s for s in scored if s["classification"] == "fake"]
    uncertain_cvs = [s for s in scored if s["classification"] == "uncertain"]

    return {
        "total_cvs": len(scored),
        "real_cv_count": len(real_cvs),
        "fake_cv_count": len(fake_cvs),
        "uncertain_cv_count": len(uncertain_cvs),
        "real_cv_ratio": len(real_cvs) / len(scored) if scored else 0,
        "avg_quality_score": sum(s["quality_score"] for s in scored) / len(scored) if scored else 0,
        "scored_conversions": scored,
    }


def enhanced_composite_decision(fraud_score, fraud_rate, monthly_cv_raw, cv_quality_result, threshold):
    """CV Quality Scoreを組み込んだ強化版複合判定"""
    true_cv_count = cv_quality_result.get("real_cv_count", 0)
    fake_ratio = cv_quality_result.get("fake_cv_count", 0) / max(monthly_cv_raw, 1)

    if fraud_score < threshold:
        return "monitor_only"

    if fraud_rate >= 0.20 and true_cv_count == 0:
        return "block"

    if fraud_rate >= 0.20 and fake_ratio >= 0.80:
        return "block"

    if fraud_rate >= 0.20 and true_cv_count >= 50:
        return "flag_and_monitor"

    if fraud_rate >= 0.20 and 0 < true_cv_count < 50 and fake_ratio >= 0.50:
        return "block"

    return "flag_and_monitor"
