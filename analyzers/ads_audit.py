"""広告監査 v3.0 - 3媒体対応 63チェック（Layer1 CSV判定）"""
import logging

log = logging.getLogger("bpo")

PLATFORM_LABEL = {"google": "Google Ads", "meta": "Meta Ads", "tiktok": "TikTok Ads"}


def run_audit(client_id, data, thresholds):
    campaigns = data.get("campaigns", [])
    totals = data.get("totals", {})
    if not campaigns:
        return {"score": 0, "grade": "F", "issues": [], "quick_wins": [], "error": "データなし"}

    by_platform = {}
    for c in campaigns:
        p = c.get("platform", "unknown").lower()
        if p not in by_platform:
            by_platform[p] = []
        by_platform[p].append(c)

    common_t = thresholds.get("common", {})
    google_t = thresholds.get("google", {})
    meta_t = thresholds.get("meta", {})
    tiktok_t = thresholds.get("tiktok", {})
    issues = []
    quick_wins = []
    total_cost = totals.get("total_cost", 0)
    total_cv = totals.get("total_conversions", 0)
    avg_cpa = totals.get("avg_cpa", 0)
    avg_ctr = totals.get("avg_ctr", 0)

    platform_roas, platform_cpa, platform_cv, platform_cost = {}, {}, {}, {}
    for p, camps in by_platform.items():
        pc = sum(c.get("cost", 0) for c in camps)
        pv = sum(c.get("conversions", 0) for c in camps)
        pr = sum(c.get("revenue", 0) for c in camps)
        platform_cost[p] = pc
        platform_cv[p] = pv
        platform_cpa[p] = pc / pv if pv > 0 else 0
        platform_roas[p] = pr / pc if pc > 0 else 0

    # === 共通 C01-C15 ===
    for camp in campaigns:
        name = camp.get("campaign", "unknown")
        p = camp.get("platform", "unknown").lower()
        p_label = PLATFORM_LABEL.get(p, p)
        cost, cv, cpa = camp.get("cost", 0), camp.get("conversions", 0), camp.get("cpa", 0)
        roas, ctr, cpm = camp.get("roas", 0), camp.get("ctr", 0), camp.get("cpm", 0)
        freq, rev = camp.get("frequency", 0), camp.get("revenue", 0)
        clicks, imps = camp.get("clicks", 0), camp.get("impressions", 0)

        if ctr < common_t.get("ctr_min", 1.0) and cost > 0:
            issues.append({"check_id": "C01", "severity": "medium", "platform": p, "campaign": name, "issue": f"CTR {ctr:.2f}% が共通下限未満", "action": "ターゲティングまたはクリエイティブの見直し"})
        if cv == 0 and cost >= common_t.get("cv_zero_cost_min", 5000):
            issues.append({"check_id": "C02", "severity": "critical", "platform": p, "campaign": name, "issue": f"CV 0件でコスト ¥{cost:,.0f} が発生", "action": "即停止またはLP・ターゲティング全面見直し"})
        if roas > 0 and roas < common_t.get("roas_min", 1.0) and cost >= 30000:
            issues.append({"check_id": "C03", "severity": "critical", "platform": p, "campaign": name, "issue": f"ROAS {roas:.2f} で赤字運用", "action": "予算縮小→LP改善→ターゲティング見直し"})
        if freq > common_t.get("frequency_max", 4.0):
            issues.append({"check_id": "C04", "severity": "high", "platform": p, "campaign": name, "issue": f"フリークエンシー {freq:.1f} が上限超過", "action": "新クリエイティブ追加またはオーディエンス拡張"})
        if roas >= 3.0 and cv >= 5:
            quick_wins.append({"check_id": "C05", "platform": p, "severity": "high", "campaign": name, "action": f"ROAS {roas:.1f} と好調。予算20-30%増加を推奨"})
        if ctr >= 2.0 and freq <= 2.0 and cv >= 3:
            quick_wins.append({"check_id": "C06", "platform": p, "severity": "medium", "campaign": name, "action": f"CTR {ctr:.2f}%/頻度 {freq:.1f} でスケール余地あり"})
        if avg_cpa > 0 and cpa > avg_cpa * 2.0 and cv >= 2 and cost >= 10000:
            quick_wins.append({"check_id": "C07", "platform": p, "severity": "medium", "campaign": name, "action": f"CPA改善で ¥{(cpa - avg_cpa) * cv:,.0f} 削減可能"})
        if cpm > 25000 and cost >= 10000:
            issues.append({"check_id": "C08", "severity": "medium", "platform": p, "campaign": name, "issue": f"CPM ¥{cpm:,.0f} が異常に高い", "action": "ターゲティング拡大またはプレースメント見直し"})
        if avg_cpa > 0 and cost >= avg_cpa * 3 and cv == 0:
            issues.append({"check_id": "C09", "severity": "critical", "platform": p, "campaign": name, "issue": f"CPA目標の3倍消化でCV 0件", "action": "即停止を検討"})
        if avg_cpa > 0 and cost > 0 and cost < avg_cpa * 20:
            issues.append({"check_id": "C10", "severity": "medium", "platform": p, "campaign": name, "issue": f"予算 ¥{cost:,.0f} がCPA×20未満。学習データ不足", "action": "予算増加または統合を検討"})
        cvr = (cv / clicks * 100) if clicks > 0 else 0
        if cvr < 1.0 and cost >= 50000 and clicks >= 100:
            issues.append({"check_id": "C11", "severity": "high", "platform": p, "campaign": name, "issue": f"CVR {cvr:.2f}% が極端に低い", "action": "LP改善・ターゲティング見直し"})
        if imps >= 10000 and clicks < imps * 0.005 and cost >= 20000:
            issues.append({"check_id": "C12", "severity": "medium", "platform": p, "campaign": name, "issue": f"大量表示({imps:,})に対しクリック極少({clicks})", "action": "広告コピー・クリエイティブ強化"})
        if rev > 0 and cost > 0 and rev < cost * 0.8:
            issues.append({"check_id": "C13", "severity": "high", "platform": p, "campaign": name, "issue": f"売上¥{rev:,.0f}がコスト¥{cost:,.0f}の80%未満", "action": "ROAS改善が見込めない場合は撤退検討"})
        p_avg = platform_cpa.get(p, avg_cpa)
        if p_avg > 0 and cpa > p_avg * 2.5 and cost >= 10000 and cv > 0:
            issues.append({"check_id": "C14", "severity": "high", "platform": p, "campaign": name, "issue": f"CPA ¥{cpa:,.0f} が{p_label}平均の{cpa/p_avg:.1f}倍", "action": "ターゲティング縮小・KW精査・LP改善"})
        if total_cost > 0 and cost / total_cost >= 0.5:
            issues.append({"check_id": "C15", "severity": "medium", "platform": p, "campaign": name, "issue": f"全体コストの{cost/total_cost*100:.0f}%が集中", "action": "他CPへの予算分散を検討"})

    # === Google G01-G20 ===
    for camp in by_platform.get("google", []):
        name, cost, cv, cpa = camp.get("campaign",""), camp.get("cost",0), camp.get("conversions",0), camp.get("cpa",0)
        roas, ctr, cpm, freq = camp.get("roas",0), camp.get("ctr",0), camp.get("cpm",0), camp.get("frequency",0)
        clicks, imps, rev = camp.get("clicks",0), camp.get("impressions",0), camp.get("revenue",0)
        ct = camp.get("campaign_type","").lower()
        st = google_t.get("search",{})
        if ct == "search":
            if ctr < st.get("ctr_min",3.0) and cost > 0:
                issues.append({"check_id":"G01","severity":"high","platform":"google","campaign":name,"issue":f"検索CTR {ctr:.2f}% が基準 {st.get('ctr_min',3.0)}% 未満","action":"広告コピー改善・除外KW追加"})
            if avg_cpa > 0 and cpa > avg_cpa * st.get("cpa_ratio_max",2.0) and cost >= 10000:
                issues.append({"check_id":"G02","severity":"high","platform":"google","campaign":name,"issue":f"検索CPA ¥{cpa:,.0f} が平均の {cpa/avg_cpa:.1f}倍","action":"KW精査・入札調整"})
            cpc = cost/clicks if clicks>0 else 0
            if cpc > 500 and cv == 0:
                issues.append({"check_id":"G03","severity":"high","platform":"google","campaign":name,"issue":f"検索CPC ¥{cpc:,.0f} でCV 0件","action":"KW関連性確認・LP改善"})
            if ctr < 2.0 and cpm > 15000:
                issues.append({"check_id":"G04","severity":"medium","platform":"google","campaign":name,"issue":f"低CTR+高CPM — KWマッチ精度に問題","action":"検索語句レポート確認・除外KW追加"})
            if ctr >= 5.0 and roas >= 3.0:
                quick_wins.append({"check_id":"G05","platform":"google","severity":"high","campaign":name,"action":f"検索CTR {ctr:.1f}%/ROAS {roas:.1f} — 予算拡大推奨"})
        elif ct == "shopping":
            if roas < google_t.get("shopping",{}).get("roas_min",3.0) and cost >= 20000:
                issues.append({"check_id":"G06","severity":"high","platform":"google","campaign":name,"issue":f"Shopping ROAS {roas:.2f} が基準未満","action":"フィード品質改善"})
            if ctr < 1.0 and imps >= 5000:
                issues.append({"check_id":"G07","severity":"medium","platform":"google","campaign":name,"issue":f"Shopping CTR {ctr:.2f}% が低い","action":"商品画像・価格見直し"})
            if roas >= 4.0 and cv >= 5:
                quick_wins.append({"check_id":"G08","platform":"google","severity":"high","campaign":name,"action":f"Shopping ROAS {roas:.1f} — 商品グループ拡張推奨"})
        elif ct == "pmax":
            pm = google_t.get("pmax",{})
            if roas < pm.get("roas_min",2.0) and cost >= 30000:
                issues.append({"check_id":"G09","severity":"medium","platform":"google","campaign":name,"issue":f"PMax ROAS {roas:.2f} が基準未満","action":"アセットグループ見直し"})
            ewcv = cv*7
            if ewcv < pm.get("conversion_min_weekly",30) and cost >= 30000:
                issues.append({"check_id":"G10","severity":"medium","platform":"google","campaign":name,"issue":f"推定週間CV {ewcv:.0f} がPMax学習基準未満","action":"マイクロCV導入"})
            gc = platform_cost.get("google",0)
            if gc > 0 and cost/gc >= 0.6:
                issues.append({"check_id":"G11","severity":"medium","platform":"google","campaign":name,"issue":f"PMaxがGoogle全体の{cost/gc*100:.0f}%占有","action":"Search/Shoppingとバランス見直し"})
            if freq >= 4.0:
                issues.append({"check_id":"G12","severity":"high","platform":"google","campaign":name,"issue":f"PMax Frequency {freq:.1f} — 配信面偏り","action":"クリエイティブ追加・オーディエンス拡張"})
        elif ct in ("gdn","display"):
            if ctr < 0.5 and cost >= 20000:
                issues.append({"check_id":"G13","severity":"medium","platform":"google","campaign":name,"issue":f"GDN CTR {ctr:.2f}% が低い","action":"プレースメント除外"})
            if cpm < 200 and imps >= 50000:
                issues.append({"check_id":"G14","severity":"medium","platform":"google","campaign":name,"issue":f"CPM極低 — 低品質配信面の可能性","action":"プレースメントレポート確認"})
        elif ct in ("youtube","video"):
            cpv = cost/clicks if clicks>0 else 0
            if cpv > 30 and cost >= 20000:
                issues.append({"check_id":"G15","severity":"medium","platform":"google","campaign":name,"issue":f"YouTube CPV ¥{cpv:,.0f} が高い","action":"ターゲティング・動画改善"})
            if ctr < 0.3 and imps >= 10000:
                issues.append({"check_id":"G16","severity":"medium","platform":"google","campaign":name,"issue":f"YouTube CTR {ctr:.2f}% — 低エンゲージメント","action":"サムネイル・冒頭改善"})
        elif ct in ("demand_gen","demandgen","dg"):
            if roas < 1.5 and cost >= 20000:
                issues.append({"check_id":"G17","severity":"medium","platform":"google","campaign":name,"issue":f"Demand Gen ROAS {roas:.2f} が低い","action":"クリエイティブ・オーディエンス見直し"})
        elif ct in ("app","uac"):
            if cpa > 3000 and cost >= 20000:
                issues.append({"check_id":"G18","severity":"medium","platform":"google","campaign":name,"issue":f"App CPI ¥{cpa:,.0f} が高い","action":"クリエイティブ・ターゲティング見直し"})
        if imps == 0 and cost == 0:
            issues.append({"check_id":"G19","severity":"low","platform":"google","campaign":name,"issue":"配信停止中","action":"ステータス・ポリシー確認"})
        if clicks >= 200 and cv == 0 and cost >= 10000:
            issues.append({"check_id":"G20","severity":"critical","platform":"google","campaign":name,"issue":f"{clicks}クリックでCV 0件","action":"CVタグ・LP確認"})

    # === Meta M01-M16 ===
    for camp in by_platform.get("meta", []):
        name, cost, cv, cpa = camp.get("campaign",""), camp.get("cost",0), camp.get("conversions",0), camp.get("cpa",0)
        roas, ctr, cpm, freq = camp.get("roas",0), camp.get("ctr",0), camp.get("cpm",0), camp.get("frequency",0)
        clicks, imps, rev = camp.get("clicks",0), camp.get("impressions",0), camp.get("revenue",0)
        ct = camp.get("campaign_type","").lower()
        mf = meta_t.get("feed",{})
        mr = meta_t.get("reels",{})
        ma = platform_cpa.get("meta", avg_cpa)
        if ct == "feed":
            if ctr < mf.get("ctr_min",1.0) and cost >= 10000:
                issues.append({"check_id":"M01","severity":"medium","platform":"meta","campaign":name,"issue":f"Feed CTR {ctr:.2f}% が基準未満","action":"クリエイティブ改善"})
            if freq > mf.get("frequency_max",3.0):
                issues.append({"check_id":"M02","severity":"high","platform":"meta","campaign":name,"issue":f"フリークエンシー {freq:.1f} が上限超過","action":"クリエイティブ追加"})
            if freq > 4.0 and "retarget" in name.lower():
                issues.append({"check_id":"M03","severity":"high","platform":"meta","campaign":name,"issue":f"リタゲ頻度 {freq:.1f} が疲弊基準超過","action":"除外設定・ウィンドウ短縮"})
            if ma > 0 and cpa > ma * mf.get("cpa_ratio_max",2.5) and cost >= 10000:
                issues.append({"check_id":"M04","severity":"high","platform":"meta","campaign":name,"issue":f"Feed CPA ¥{cpa:,.0f} がMeta平均の{cpa/ma:.1f}倍","action":"オーディエンス・クリエイティブ見直し"})
        elif ct in ("reels","reel"):
            if ctr < mr.get("ctr_min",0.7) and cost >= 10000:
                issues.append({"check_id":"M05","severity":"medium","platform":"meta","campaign":name,"issue":f"Reels CTR {ctr:.2f}% が基準未満","action":"冒頭フック改善・UGC風導入"})
            if ctr < 0.5 and imps >= 20000:
                issues.append({"check_id":"M06","severity":"medium","platform":"meta","campaign":name,"issue":f"Reels エンゲージメント極低","action":"動画フレーム・サウンド改善"})
        elif ct in ("stories","story"):
            if ctr < 0.5 and cost >= 10000:
                issues.append({"check_id":"M07","severity":"medium","platform":"meta","campaign":name,"issue":f"Stories CTR {ctr:.2f}% が低い","action":"CTA配置・フルスクリーン最適化"})
        elif ct in ("audience_network","an"):
            if ctr < 0.3 and cost >= 10000:
                issues.append({"check_id":"M08","severity":"high","platform":"meta","campaign":name,"issue":f"AN CTR {ctr:.2f}% — 低品質配信面","action":"AN除外検討"})
        elif ct in ("advantage_plus","asc","advantage"):
            if roas < 2.0 and cost >= 30000:
                issues.append({"check_id":"M09","severity":"high","platform":"meta","campaign":name,"issue":f"Advantage+ ROAS {roas:.2f} が基準未満","action":"カタログ品質・入札上限確認"})
        ewcv = cv*7
        if ewcv < 50 and cost >= 20000:
            issues.append({"check_id":"M10","severity":"medium","platform":"meta","campaign":name,"issue":f"推定週間CV {ewcv:.0f} が学習基準未満","action":"上位ファネル最適化・CP統合"})
        if "prospect" in name.lower():
            rtc = [c for c in by_platform.get("meta",[]) if "retarget" in c.get("campaign","").lower()]
            if rtc and rtc[0].get("cpa",0) > 0 and cpa > rtc[0]["cpa"]*4:
                issues.append({"check_id":"M11","severity":"medium","platform":"meta","campaign":name,"issue":f"プロスペCPAがリタゲの{cpa/rtc[0]['cpa']:.1f}倍","action":"LLA品質改善"})
        if cpm > 5000 and cost >= 10000:
            issues.append({"check_id":"M12","severity":"medium","platform":"meta","campaign":name,"issue":f"CPM ¥{cpm:,.0f} が高い","action":"オーディエンスサイズ拡大"})
        if clicks >= 300 and cv <= 1 and cost >= 20000:
            issues.append({"check_id":"M13","severity":"critical","platform":"meta","campaign":name,"issue":f"{clicks}クリックでCV {cv}件","action":"Pixel・LP確認"})
        same = [c for c in by_platform.get("meta",[]) if c.get("campaign_type","") == ct]
        if len(same) <= 1 and cost >= 30000:
            issues.append({"check_id":"M14","severity":"medium","platform":"meta","campaign":name,"issue":"同配信面のCP数が少ない — A/Bテスト不足","action":"クリエイティブバリエーション追加"})
        if cost >= 50000 and roas < 1.5:
            issues.append({"check_id":"M15","severity":"medium","platform":"meta","campaign":name,"issue":f"ROAS {roas:.2f} — CBO/ABO見直し必要","action":"CBO↔ABO切替テスト"})
        if roas >= 4.0 and freq <= 2.5 and cv >= 3:
            quick_wins.append({"check_id":"M16","platform":"meta","severity":"high","campaign":name,"action":f"ROAS {roas:.1f}/Freq {freq:.1f} — スケール余地大"})

    # === TikTok T01-T13 ===
    for camp in by_platform.get("tiktok", []):
        name, cost, cv, cpa = camp.get("campaign",""), camp.get("cost",0), camp.get("conversions",0), camp.get("cpa",0)
        roas, ctr, cpm, freq = camp.get("roas",0), camp.get("ctr",0), camp.get("cpm",0), camp.get("frequency",0)
        clicks, imps = camp.get("clicks",0), camp.get("impressions",0)
        ct = camp.get("campaign_type","").lower()
        ti = tiktok_t.get("in_feed",{})
        if ct in ("in_feed","infeed","in-feed"):
            if ctr < ti.get("ctr_min",0.8) and cost >= 10000:
                issues.append({"check_id":"T01","severity":"high","platform":"tiktok","campaign":name,"issue":f"In-Feed CTR {ctr:.2f}% が基準未満","action":"冒頭フック・サウンド改善"})
        if roas > 0 and roas < 1.0 and cost >= 20000:
            issues.append({"check_id":"T02","severity":"critical","platform":"tiktok","campaign":name,"issue":f"TikTok ROAS {roas:.2f} で赤字","action":"予算縮小・ターゲティング見直し"})
        ewcv = cv*7
        if ewcv < 50 and cost >= 15000:
            issues.append({"check_id":"T03","severity":"medium","platform":"tiktok","campaign":name,"issue":f"推定週間CV {ewcv:.0f} が学習基準未満","action":"上位ファネル最適化・予算増加"})
        if ct in ("spark_ads","spark"):
            if ctr < 0.5 and cost >= 10000:
                issues.append({"check_id":"T04","severity":"medium","platform":"tiktok","campaign":name,"issue":f"Spark CTR {ctr:.2f}% — 効果薄い","action":"別投稿でテスト"})
            ns = [c for c in by_platform.get("tiktok",[]) if c.get("campaign_type","").lower() not in ("spark_ads","spark")]
            if ns:
                nsa = sum(c.get("ctr",0) for c in ns)/len(ns)
                if nsa > 0 and ctr < nsa*0.8:
                    issues.append({"check_id":"T05","severity":"medium","platform":"tiktok","campaign":name,"issue":f"Spark CTR {ctr:.2f}% が通常平均 {nsa:.2f}% を下回る","action":"高エンゲージメント投稿に変更"})
        if ct in ("search_ads","search"):
            if ctr < 1.0 and cost >= 10000:
                issues.append({"check_id":"T06","severity":"medium","platform":"tiktok","campaign":name,"issue":f"TikTok Search CTR {ctr:.2f}%","action":"KW見直し"})
        if ct == "pangle":
            if ctr < 0.2 and cost >= 10000:
                issues.append({"check_id":"T07","severity":"high","platform":"tiktok","campaign":name,"issue":f"Pangle CTR {ctr:.2f}% — 低品質","action":"Pangle除外検討"})
        if ct in ("smart+","smart_plus","automated"):
            if roas < 1.5 and cost >= 20000:
                issues.append({"check_id":"T08","severity":"medium","platform":"tiktok","campaign":name,"issue":f"Smart+ ROAS {roas:.2f} — 効果不十分","action":"ROAS目標確認・手動CPと比較"})
        if cpm > 3000 and cost >= 10000:
            issues.append({"check_id":"T09","severity":"medium","platform":"tiktok","campaign":name,"issue":f"CPM ¥{cpm:,.0f} が高い","action":"ターゲティング拡大"})
        ttc = platform_cost.get("tiktok",0)
        if ttc > 0 and cost/ttc >= 0.7:
            issues.append({"check_id":"T10","severity":"medium","platform":"tiktok","campaign":name,"issue":f"TikTok予算の{cost/ttc*100:.0f}%が集中","action":"別CPを追加"})
        if clicks >= 200 and cv == 0 and cost >= 10000:
            issues.append({"check_id":"T11","severity":"critical","platform":"tiktok","campaign":name,"issue":f"{clicks}クリックでCV 0件","action":"Pixel・LP確認"})
        if imps >= 30000 and clicks < imps*0.003:
            issues.append({"check_id":"T12","severity":"medium","platform":"tiktok","campaign":name,"issue":"大量表示に対しクリック極少","action":"冒頭フック・CTA改善"})
        if roas >= 2.5 and freq <= 2.0 and cv >= 3:
            quick_wins.append({"check_id":"T13","platform":"tiktok","severity":"high","campaign":name,"action":f"ROAS {roas:.1f}/Freq {freq:.1f} — スケール余地あり"})

    # === クロス媒体 X01-X07 ===
    if len(platform_roas) >= 2:
        best_r = max(platform_roas, key=platform_roas.get)
        worst_r = min(platform_roas, key=platform_roas.get)
        if platform_roas[worst_r] > 0:
            gap = platform_roas[best_r]/platform_roas[worst_r]
            if gap >= 3.0:
                issues.append({"check_id":"X01","severity":"high","platform":"cross","campaign":"全体","issue":f"媒体間ROAS格差 {gap:.1f}倍","action":f"{worst_r}の予算を{best_r}にシフト"})
    if len(platform_cpa) >= 2:
        best_c = min(platform_cpa, key=platform_cpa.get)
        worst_c = max(platform_cpa, key=platform_cpa.get)
        if platform_cpa[best_c] > 0:
            gap = platform_cpa[worst_c]/platform_cpa[best_c]
            if gap >= 3.0:
                issues.append({"check_id":"X02","severity":"high","platform":"cross","campaign":"全体","issue":f"媒体間CPA格差 {gap:.1f}倍","action":f"{worst_c}の効率改善または予算再配分"})
    tr = sum(c.get("revenue",0) for c in campaigns)
    if total_cost > 0:
        tro = tr/total_cost
        if tro < 1.0:
            issues.append({"check_id":"X03","severity":"critical","platform":"cross","campaign":"全体","issue":f"全媒体ROAS {tro:.2f} — 全体赤字","action":"低ROAS媒体の予算縮小を即時実行"})
    pcvr = {}
    for p2, c2 in by_platform.items():
        pk = sum(c.get("clicks",0) for c in c2)
        pv2 = sum(c.get("conversions",0) for c in c2)
        if pk > 0: pcvr[p2] = pv2/pk*100
    if len(pcvr) >= 2:
        bc = max(pcvr, key=pcvr.get)
        wc = min(pcvr, key=pcvr.get)
        if pcvr[wc] > 0 and pcvr[bc]/pcvr[wc] >= 3.0:
            issues.append({"check_id":"X04","severity":"medium","platform":"cross","campaign":"全体","issue":f"媒体間CVR格差 {pcvr[bc]/pcvr[wc]:.1f}倍","action":"LP最適化を媒体別に実施"})
    if len(platform_roas) >= 2 and total_cost > 0:
        for p3, pc3 in platform_cost.items():
            ps = pc3/total_cost*100
            pr3 = platform_roas.get(p3,0)
            if ps >= 40 and pr3 < 1.5:
                issues.append({"check_id":"X05","severity":"high","platform":"cross","campaign":"全体","issue":f"{PLATFORM_LABEL.get(p3,p3)}が{ps:.0f}%占有でROAS {pr3:.1f}","action":"高ROAS媒体へ予算移行"})
    pcpc = {}
    for p4, c4 in by_platform.items():
        pk2 = sum(c.get("clicks",0) for c in c4)
        pc4 = sum(c.get("cost",0) for c in c4)
        if pk2 > 0: pcpc[p4] = pc4/pk2
    if len(pcpc) >= 2:
        ch = min(pcpc, key=pcpc.get)
        ex = max(pcpc, key=pcpc.get)
        if pcpc[ch] > 0 and pcpc[ex]/pcpc[ch] >= 4.0:
            issues.append({"check_id":"X06","severity":"medium","platform":"cross","campaign":"全体","issue":f"CPC格差 {pcpc[ex]/pcpc[ch]:.1f}倍","action":"高CPC媒体の入札見直し"})
    if total_cost > 0 and total_cv > 0:
        oa = total_cost/total_cv
        if oa > 15000:
            issues.append({"check_id":"X07","severity":"high","platform":"cross","campaign":"全体","issue":f"全体CPA ¥{oa:,.0f} — 高コスト体質","action":"低CPA CPへの集中"})

    # === スコア算定 ===
    sw = thresholds.get("scoring",{}).get("severity_weights", {"critical": 5.0, "high": 3.0, "medium": 1.5, "low": 0.5})
    tp = sum(float(sw.get(i.get("severity","low"), 1.0)) for i in issues)
    mp = max(len(campaigns) * 8 + len(by_platform) * 15, 100)
    score = max(0, min(100, round(100 - (tp / mp * 100))))
    gc = thresholds.get("scoring",{}).get("grades",{})
    if score >= gc.get("A",90): grade = "A"
    elif score >= gc.get("B",75): grade = "B"
    elif score >= gc.get("C",60): grade = "C"
    elif score >= gc.get("D",40): grade = "D"
    else: grade = "F"

    platform_summary = {}
    for p in ["google","meta","tiktok"]:
        pi = [i for i in issues if i.get("platform") == p]
        pc = by_platform.get(p,[])
        if pc:
            platform_summary[p] = {
                "campaigns": len(pc), "issues": len(pi),
                "critical": len([i for i in pi if i["severity"]=="critical"]),
                "cost": sum(c.get("cost",0) for c in pc),
                "conversions": sum(c.get("conversions",0) for c in pc),
                "roas": platform_roas.get(p,0),
            }

    result = {
        "score": score, "grade": grade,
        "total_campaigns": len(campaigns),
        "total_cost": totals.get("total_cost",0),
        "total_conversions": totals.get("total_conversions",0),
        "avg_cpa": avg_cpa, "avg_ctr": avg_ctr,
        "issues": issues, "quick_wins": quick_wins,
        "critical_count": len([i for i in issues if i["severity"]=="critical"]),
        "high_count": len([i for i in issues if i["severity"]=="high"]),
        "medium_count": len([i for i in issues if i["severity"]=="medium"]),
        "platform_summary": platform_summary,
        "check_count": 63,
    }
    log.info(f"[{client_id}] 監査完了: Score {score} ({grade}), Issues {len(issues)}, QuickWins {len(quick_wins)}")
    return result
