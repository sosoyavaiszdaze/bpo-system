"""異常検知 v2.0 - 3媒体対応、前日比較で急変を検出し原因候補を提示"""
import os
import json
import logging

log = logging.getLogger("bpo")
DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")

# 媒体別デフォルト閾値
PLATFORM_DEFAULTS = {
    "google": {
        "cpa_increase_pct": 20, "ctr_decrease_pct": 15, "cpm_increase_pct": 25,
        "frequency_max": 4.0, "roas_decrease_pct": 20,
        "cost_spike_pct": 30, "impression_drop_pct": 50,
        "causes": {
            "cpa_spike": "品質スコア低下、競合増加、または検索語句の変化",
            "ctr_drop": "広告ランク低下、検索意図の変化、またはRSA疲弊",
            "frequency": "オーディエンスリスト枯渇（GDN/YouTube）またはPMax配信面偏り",
            "roas_drop": "CVR低下、商品ページ変更、または季節要因",
        }
    },
    "meta": {
        "cpa_increase_pct": 18, "ctr_decrease_pct": 12, "cpm_increase_pct": 20,
        "frequency_max": 3.0, "roas_decrease_pct": 18,
        "cost_spike_pct": 25, "impression_drop_pct": 40,
        "causes": {
            "cpa_spike": "クリエイティブ疲弊、オーディエンス飽和、またはiOS計測ロス",
            "ctr_drop": "クリエイティブ疲弊、Frequency過多、または配信面シフト",
            "frequency": "クリエイティブ疲弊の兆候。CPA悪化前に新素材追加を推奨",
            "roas_drop": "Advantage+の学習リセット、またはLTV低下",
        }
    },
    "tiktok": {
        "cpa_increase_pct": 22, "ctr_decrease_pct": 15, "cpm_increase_pct": 30,
        "frequency_max": 3.5, "roas_decrease_pct": 25,
        "cost_spike_pct": 35, "impression_drop_pct": 50,
        "causes": {
            "cpa_spike": "クリエイティブ寿命切れ（TikTokは平均2-3週）、またはSmart+暴走",
            "ctr_drop": "動画の鮮度低下、トレンド乖離、またはサウンド効果不足",
            "frequency": "In-Feed配信のオーディエンス枯渇。Spark Adsで新UGC追加を推奨",
            "roas_drop": "LP離脱率上昇、またはTikTok Shop連携不具合",
        }
    },
}


def _load_previous(client_id):
    prev_path = os.path.join(DATA_DIR, f"{client_id}_previous.json")
    if os.path.exists(prev_path):
        with open(prev_path, "r", encoding="utf-8") as f:
            return json.load(f)
    return None


def _save_current(client_id, data):
    prev_path = os.path.join(DATA_DIR, f"{client_id}_previous.json")
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(prev_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, default=str)


def _get_platform_config(platform, thresholds):
    """媒体別の閾値と原因テンプレートを取得"""
    anomaly_cfg = thresholds.get("anomaly", {})
    defaults = PLATFORM_DEFAULTS.get(platform, PLATFORM_DEFAULTS["google"])
    return {
        "cpa_increase_pct": anomaly_cfg.get("cpa_increase_pct", defaults["cpa_increase_pct"]),
        "ctr_decrease_pct": anomaly_cfg.get("ctr_decrease_pct", defaults["ctr_decrease_pct"]),
        "cpm_increase_pct": anomaly_cfg.get("cpm_increase_pct", defaults["cpm_increase_pct"]),
        "frequency_max": anomaly_cfg.get("frequency_max", defaults["frequency_max"]),
        "roas_decrease_pct": anomaly_cfg.get("roas_decrease_pct", defaults.get("roas_decrease_pct", 20)),
        "cost_spike_pct": anomaly_cfg.get("cost_spike_pct", defaults.get("cost_spike_pct", 30)),
        "impression_drop_pct": anomaly_cfg.get("impression_drop_pct", defaults.get("impression_drop_pct", 50)),
        "causes": defaults["causes"],
    }


def _group_by_platform(campaigns):
    """キャンペーンを媒体別にグループ化"""
    groups = {}
    for camp in campaigns:
        p = camp.get("platform", "unknown").lower()
        if p not in groups:
            groups[p] = []
        groups[p].append(camp)
    return groups


def detect_anomalies(client_id, data, thresholds):
    """3媒体対応の異常検知"""
    alerts = []
    campaigns = data.get("campaigns", [])
    previous = _load_previous(client_id)
    platform_groups = _group_by_platform(campaigns)

    # === キャンペーン単位チェック（媒体別閾値） ===
    for platform, camps in platform_groups.items():
        cfg = _get_platform_config(platform, thresholds)
        platform_label = {"google": "Google", "meta": "Meta", "tiktok": "TikTok"}.get(platform, platform)

        for camp in camps:
            name = camp.get("campaign", "unknown")
            freq = camp.get("frequency", 0)
            ctr = camp.get("ctr", 0)
            roas = camp.get("roas", 0)
            cost = camp.get("cost", 0)
            camp_type = camp.get("campaign_type", "")

            # A1: フリークエンシー過多
            if freq >= cfg["frequency_max"]:
                alerts.append({
                    "type": "frequency_fatigue",
                    "platform": platform,
                    "campaign": name,
                    "campaign_type": camp_type,
                    "value": freq,
                    "threshold": cfg["frequency_max"],
                    "message": f"[{platform_label}] {name}: フリークエンシー {freq:.1f} > {cfg['frequency_max']}",
                    "cause": cfg["causes"]["frequency"],
                    "action": "新クリエイティブ追加またはオーディエンス拡張",
                    "severity": "warning",
                })

            # A2: ROAS赤字（1.0未満）
            if roas > 0 and roas < 1.0 and cost >= 30000:
                alerts.append({
                    "type": "roas_deficit",
                    "platform": platform,
                    "campaign": name,
                    "campaign_type": camp_type,
                    "value": roas,
                    "cost": cost,
                    "message": f"[{platform_label}] {name}: ROAS {roas:.2f} で赤字運用中（コスト ¥{cost:,.0f}）",
                    "cause": "広告費が売上を上回っている。LP・ターゲティング・入札の見直しが必要",
                    "action": "即時: 予算縮小 → LP改善 → ターゲティング見直し",
                    "severity": "critical",
                })

            # A3: 高コスト低CTR（媒体別基準）
            ctr_floor = {"google": 1.0, "meta": 0.5, "tiktok": 0.4}.get(platform, 0.5)
            if ctr < ctr_floor and cost >= 50000:
                alerts.append({
                    "type": "low_ctr_high_spend",
                    "platform": platform,
                    "campaign": name,
                    "campaign_type": camp_type,
                    "value": ctr,
                    "threshold": ctr_floor,
                    "cost": cost,
                    "message": f"[{platform_label}] {name}: CTR {ctr:.2f}% が極端に低い（コスト ¥{cost:,.0f}）",
                    "cause": cfg["causes"]["ctr_drop"],
                    "action": "広告コピー・クリエイティブの全面刷新を検討",
                    "severity": "warning",
                })

    # === 媒体別の前日比較 ===
    if previous:
        prev_campaigns = previous.get("campaigns", [])
        prev_by_platform = _group_by_platform(prev_campaigns)

        for platform, curr_camps in platform_groups.items():
            cfg = _get_platform_config(platform, thresholds)
            platform_label = {"google": "Google", "meta": "Meta", "tiktok": "TikTok"}.get(platform, platform)
            prev_camps = prev_by_platform.get(platform, [])

            if not prev_camps:
                continue

            # 媒体別集計
            curr_cost = sum(c.get("cost", 0) for c in curr_camps)
            prev_cost = sum(c.get("cost", 0) for c in prev_camps)
            curr_cv = sum(c.get("conversions", 0) for c in curr_camps)
            prev_cv = sum(c.get("conversions", 0) for c in prev_camps)
            curr_imps = sum(c.get("impressions", 0) for c in curr_camps)
            prev_imps = sum(c.get("impressions", 0) for c in prev_camps)
            curr_clicks = sum(c.get("clicks", 0) for c in curr_camps)
            prev_clicks = sum(c.get("clicks", 0) for c in prev_camps)

            curr_cpa = curr_cost / curr_cv if curr_cv > 0 else 0
            prev_cpa = prev_cost / prev_cv if prev_cv > 0 else 0
            curr_ctr = (curr_clicks / curr_imps * 100) if curr_imps > 0 else 0
            prev_ctr = (prev_clicks / prev_imps * 100) if prev_imps > 0 else 0

            # B1: CPA急上昇
            if prev_cpa > 0 and curr_cpa > 0:
                cpa_change = ((curr_cpa - prev_cpa) / prev_cpa) * 100
                if cpa_change >= cfg["cpa_increase_pct"]:
                    alerts.append({
                        "type": "cpa_spike",
                        "platform": platform,
                        "change_pct": round(cpa_change, 1),
                        "message": f"[{platform_label}] CPA {cpa_change:+.1f}% 上昇 (¥{prev_cpa:,.0f} → ¥{curr_cpa:,.0f})",
                        "cause": cfg["causes"]["cpa_spike"],
                        "action": "キャンペーン別CPAを確認し、急上昇した個別キャンペーンを特定",
                        "severity": "critical",
                    })

            # B2: CTR急低下
            if prev_ctr > 0 and curr_ctr > 0:
                ctr_change = ((curr_ctr - prev_ctr) / prev_ctr) * 100
                if ctr_change <= -cfg["ctr_decrease_pct"]:
                    alerts.append({
                        "type": "ctr_drop",
                        "platform": platform,
                        "change_pct": round(ctr_change, 1),
                        "message": f"[{platform_label}] CTR {ctr_change:+.1f}% 低下 ({prev_ctr:.2f}% → {curr_ctr:.2f}%)",
                        "cause": cfg["causes"]["ctr_drop"],
                        "action": "広告テキスト・クリエイティブの更新を検討",
                        "severity": "warning",
                    })

            # B3: コスト急増
            if prev_cost > 0:
                cost_change = ((curr_cost - prev_cost) / prev_cost) * 100
                if cost_change >= cfg["cost_spike_pct"]:
                    alerts.append({
                        "type": "cost_spike",
                        "platform": platform,
                        "change_pct": round(cost_change, 1),
                        "message": f"[{platform_label}] コスト {cost_change:+.1f}% 急増 (¥{prev_cost:,.0f} → ¥{curr_cost:,.0f})",
                        "cause": "入札競争激化、予算上限引き上げ、または配信面拡張",
                        "action": "入札設定と予算上限を確認",
                        "severity": "warning",
                    })

            # B4: インプレッション急減
            if prev_imps > 0:
                imp_change = ((curr_imps - prev_imps) / prev_imps) * 100
                if imp_change <= -cfg["impression_drop_pct"]:
                    alerts.append({
                        "type": "impression_drop",
                        "platform": platform,
                        "change_pct": round(imp_change, 1),
                        "message": f"[{platform_label}] インプレッション {imp_change:+.1f}% 急減 ({prev_imps:,} → {curr_imps:,})",
                        "cause": "予算枯渇、入札負け、ポリシー違反、または学習フェーズリセット",
                        "action": "予算残高・広告ステータス・入札額を確認",
                        "severity": "critical",
                    })

    # === クロス媒体チェック ===
    if len(platform_groups) >= 2:
        platform_cpas = {}
        for platform, camps in platform_groups.items():
            total_cost = sum(c.get("cost", 0) for c in camps)
            total_cv = sum(c.get("conversions", 0) for c in camps)
            if total_cv > 0:
                platform_cpas[platform] = total_cost / total_cv

        if len(platform_cpas) >= 2:
            best = min(platform_cpas, key=platform_cpas.get)
            worst = max(platform_cpas, key=platform_cpas.get)
            if platform_cpas[best] > 0:
                ratio = platform_cpas[worst] / platform_cpas[best]
                if ratio >= 3.0:
                    best_label = {"google": "Google", "meta": "Meta", "tiktok": "TikTok"}.get(best, best)
                    worst_label = {"google": "Google", "meta": "Meta", "tiktok": "TikTok"}.get(worst, worst)
                    alerts.append({
                        "type": "cross_platform_cpa_gap",
                        "message": f"媒体間CPA格差: {worst_label} ¥{platform_cpas[worst]:,.0f} vs {best_label} ¥{platform_cpas[best]:,.0f} ({ratio:.1f}倍差)",
                        "cause": "媒体適性の不一致、またはLP・ターゲティングの媒体別最適化が未実施",
                        "action": f"{worst_label}の予算を{best_label}に一部移行、または{worst_label}の改善を優先",
                        "severity": "warning",
                    })

    # 今日のデータを保存
    _save_current(client_id, data)

    # 媒体別サマリー
    platform_summary = {}
    for a in alerts:
        p = a.get("platform", "cross")
        if p not in platform_summary:
            platform_summary[p] = {"alerts": 0, "critical": 0}
        platform_summary[p]["alerts"] += 1
        if a.get("severity") == "critical":
            platform_summary[p]["critical"] += 1

    result = {
        "alerts": alerts,
        "alert_count": len(alerts),
        "critical_count": len([a for a in alerts if a.get("severity") == "critical"]),
        "has_previous_data": previous is not None,
        "platform_summary": platform_summary,
    }

    log.info(f"[{client_id}] 異常検知完了: {len(alerts)}件のアラート (Critical: {result['critical_count']}件)")
    return result
