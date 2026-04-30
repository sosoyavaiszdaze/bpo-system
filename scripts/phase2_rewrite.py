#!/usr/bin/env python3
"""Phase 2: Python check_id → YAML rule_id 書き換え + YAML追加スクリプト"""
import os
import yaml

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RULES_DIR = os.path.join(BASE_DIR, "config", "rules")
CHECKS_DIR = os.path.join(BASE_DIR, "analyzers", "checks")

# ========================================
# 1. ID置換マップ
# ========================================

GOOGLE_REMAP = {
    "G01": "G25", "G03": "G39", "G04": "G17", "G05": "G31",
    "G08": "G13", "G11": "G20", "G12": "G42",
    "G13": "G28", "G14": "G27", "G17": "G29",
    "G20": "G26", "G21": "G26b",
    "G-KW1": "G32",
    "G26": "G37", "G27": "G37b", "G28": "G37c",
    "G29": "G34",
    "G36": "G11", "G37": "G11b", "G38": "G12", "G39": "G13b",
    "G40": "G11c",
    "G43": "G03", "G47": "G02", "G48": "G08", "G49": "G06", "G-CT2": "G04",
    "G50": "G22", "G51": "G22b", "G52": "G22c", "G53": "G22d",
    "G56": "G09", "G57": "G09b", "G58": "G54",
    # _unmapped → 新ID
    "G-PM1": "G68", "G-PM2": "G68b", "G-PM3": "G70", "G-PM4": "G68c",
    "G-PM5": "G68d", "G-PM6": "G70b",
    "G-AI1": "G75", "G-DG1": "G74", "G-DG2": "G76",
    "G-DG3": "G77", "G-CTV1": "G78",
    "G-WS1": "G79",
    # unchanged
    "G07": "G53b", "G09": "G15b",
    "G15": "G27b", "G16": "G80",
    "G22": "G26c", "G23": "G26d", "G24": "G26e",
    "G31": "G65b", "G32": "G66b",
    "G41": "G41", "G45": "G81", "G59": "G59", "G60": "G60",
}

META_REMAP = {
    "M-PI1": "M01", "M-PI2": "M03", "M-PI3": "M02", "M-PI4": "M05",
    "M-PI5": "M06", "M-PI7": "M04", "M-PI8": "M08",
    "M-CR1": "M47", "M-CR2": "M24", "M-CR5": "M35",
    "M-ST1": "M14", "M-ST2": "M15", "M-ST3": "M09", "M-ST4": "M44",
    "M-ST5": "M11", "M-ST6": "M12",
    "M-AU1": "M49", "M-AU2": "M51", "M-AU3": "M50", "M-AU4": "M53",
    "M-AU5": "M54", "M-C03": "M45", "M-C06": "M19",
    # _unmapped → 新ID
    "M-PI6": "M56", "M-CR3": "M57", "M-CR4": "M58", "M-CR6": "M59",
    "M-ST7": "M60", "M-AU6": "M61",
    "M-C01": "M62", "M-C02": "M63", "M-C04": "M64", "M-C05": "M65",
}

TIKTOK_REMAP = {
    "T-TC1": "T01", "T-TC2": "T02",
    "T-CR2": "T16", "T-CR4": "T23", "T-CR5": "T19",
    "T-CR7": "T14", "T-CR8": "T15",
    "T-BL1": "T06", "T-BL3": "T08", "T-ST2": "T10",
    "T-C01": "T03", "T-C04": "T30",
    # _unmapped → 新ID
    "T-CR1": "T36", "T-CR3": "T37", "T-CR6": "T38",
    "T-BL2": "T39",
    "T-ST1": "T40", "T-ST3": "T41", "T-ST4": "T42",
    "T-ST5": "T43", "T-ST6": "T44",
    "T-C02": "T45", "T-C03": "T46",
}


def rewrite_check_ids(filepath, remap):
    """Pythonファイル内の _r("旧ID", ...) を _r("新ID", ...) に置換"""
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()

    changes = 0
    for old_id, new_id in remap.items():
        # _r("OLD_ID" パターン
        pattern = f'_r("{old_id}"'
        replacement = f'_r("{new_id}"'
        if pattern in content:
            content = content.replace(pattern, replacement)
            changes += 1

        # "id": "OLD_ID" パターン (common.py等)
        pattern2 = f'"id": "{old_id}"'
        replacement2 = f'"id": "{new_id}"'
        if pattern2 in content:
            content = content.replace(pattern2, replacement2)
            changes += 1

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(content)

    return changes


def add_yaml_rules(filepath, new_rules):
    """既存YAMLファイルに新規ルールを追加"""
    with open(filepath, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    existing_ids = {r["id"] for r in data["rules"]}
    added = 0
    for rule in new_rules:
        if rule["id"] not in existing_ids:
            data["rules"].append(rule)
            added += 1

    with open(filepath, "w", encoding="utf-8") as f:
        yaml.dump(data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

    return added


def make_rule(rid, name, category, severity, platform, placement="共通",
              primary_axis=None, polarity="neutral", prerequisite=None):
    """v2.0 YAML ルールを生成"""
    return {
        "id": rid,
        "name": name,
        "category": category,
        "severity": severity,
        "weight": 1.0,
        "platform": platform,
        "placement": placement,
        "detection_layer": "Layer2",
        "quick_win": False,
        "primary_axis": primary_axis,
        "secondary_axis": None,
        "axis_position": "neutral",
        "polarity": polarity,
        "prerequisite": prerequisite or [],
        "conflicts": [],
        "dependencies": [],
        "conflict_group": None,
        "yonemitsu_alignment": "整合",
        "redesign_note": "",
        "enabled": True,
    }


# ========================================
# 新規YAMLルール定義
# ========================================

GOOGLE_NEW_RULES = [
    make_rule("G26b", "QS≤3比率", "キーワード", "high", "google", "Search", "TO-04", "monitor_only"),
    make_rule("G26c", "Expected CTR Below Average", "キーワード", "medium", "google", "Search", "TO-04", "monitor_only"),
    make_rule("G26d", "Ad Relevance Below Average", "キーワード", "medium", "google", "Search", "TO-04", "monitor_only"),
    make_rule("G26e", "LP Experience Below Average", "キーワード", "medium", "google", "Search", "TO-04", "monitor_only"),
    make_rule("G37b", "RSAヘッドライン数", "クリエイティブ", "medium", "google", "Search", "TO-08"),
    make_rule("G37c", "RSA説明文数", "クリエイティブ", "medium", "google", "Search", "TO-08"),
    make_rule("G11b", "目標CPA/ROAS乖離", "予算_入札", "high", "google", "共通", "TO-03"),
    make_rule("G11c", "Manual CPC→Smart Bidding移行", "予算_入札", "high", "google", "共通", "TO-11"),
    make_rule("G13b", "予算制約(Limited by Budget)", "予算_入札", "high", "google", "共通", "TO-09", "budget_first"),
    make_rule("G22b", "コールアウト拡張", "構造_設定", "medium", "google", "Search", "TO-08"),
    make_rule("G22c", "構造化スニペット", "構造_設定", "medium", "google", "Search", "TO-08"),
    make_rule("G22d", "画像拡張", "構造_設定", "medium", "google", "Search", "TO-08"),
    make_rule("G09b", "Customer Match活用", "構造_設定", "medium", "google", "共通", "TO-02"),
    make_rule("G53b", "PMax+Search重複", "構造_設定", "high", "google", "共通", "TO-01"),
    make_rule("G15b", "予算配分バランス", "予算_入札", "medium", "google", "共通", "TO-09"),
    make_rule("G27b", "共有ネガティブKWリスト", "キーワード", "medium", "google", "Search", "TO-02"),
    make_rule("G65b", "PMaxアセット密度", "クリエイティブ", "medium", "google", "P-MAX", "TO-08"),
    make_rule("G66b", "ネイティブ動画有無", "クリエイティブ", "medium", "google", "共通", "TO-08"),
    make_rule("G68b", "PMax Ad Strength", "クリエイティブ", "medium", "google", "P-MAX", "TO-08"),
    make_rule("G68c", "PMax検索テーマ", "構造_設定", "medium", "google", "P-MAX", "TO-05"),
    make_rule("G68d", "PMaxアカウントレベルネガKW", "構造_設定", "medium", "google", "P-MAX", "TO-02"),
    make_rule("G70b", "PMaxブランドKW除外", "構造_設定", "high", "google", "P-MAX", "TO-01", "neutral", ["G70"]),
    make_rule("G75", "AI Max評価", "構造_設定", "medium", "google", "共通", "TO-11"),
    make_rule("G76", "VAC→DemandGen移行", "構造_設定", "medium", "google", "Demand Gen", "TO-11"),
    make_rule("G77", "DGフリークエンシーキャップ", "構造_設定", "medium", "google", "Demand Gen", "TO-08"),
    make_rule("G78", "CTV Floodlight制限", "構造_設定", "medium", "google", "共通", "TO-11"),
    make_rule("G79", "ゼロCVキーワード群", "キーワード", "high", "google", "Search", "TO-02"),
    make_rule("G80", "無駄クリック率", "キーワード", "medium", "google", "Search", "TO-02"),
    make_rule("G81", "Consent Mode v2", "計測_トラッキング", "high", "google", "共通", "TO-11"),
]

META_NEW_RULES = [
    make_rule("M56", "Aggregated Event Measurement", "計測_トラッキング", "medium", "meta", "共通", "TO-02"),
    make_rule("M57", "フリークエンシー疲弊", "クリエイティブ", "high", "meta", "共通", "TO-08"),
    make_rule("M58", "クリエイティブ入替日数", "クリエイティブ", "medium", "meta", "共通", "TO-07"),
    make_rule("M59", "Dynamic Creative Optimization", "クリエイティブ", "medium", "meta", "共通", "TO-08"),
    make_rule("M60", "Advantage+クリエイティブ", "構造_設定", "medium", "meta", "共通", "TO-11"),
    make_rule("M61", "ファーストパーティデータ活用", "ターゲティング", "medium", "meta", "共通", "TO-02"),
    make_rule("M62", "アトリビューション設定", "計測_トラッキング", "medium", "meta", "共通", "TO-11"),
    make_rule("M63", "配信最適化目標の妥当性", "構造_設定", "medium", "meta", "共通", "TO-06"),
    make_rule("M64", "地域ターゲティング精度", "構造_設定", "medium", "meta", "共通", "TO-11"),
    make_rule("M65", "支払い方法ステータス", "構造_設定", "low", "meta", "共通", "TO-11"),
]

TIKTOK_NEW_RULES = [
    make_rule("T36", "動画クリエイティブ必須", "クリエイティブ", "high", "tiktok", "In-Feed", "TO-08"),
    make_rule("T37", "動画完視聴率", "クリエイティブ", "medium", "tiktok", "In-Feed", "TO-08"),
    make_rule("T38", "テキストオーバーレイ有無", "クリエイティブ", "low", "tiktok", "In-Feed", "TO-08"),
    make_rule("T39", "予算充足率", "予算_入札", "medium", "tiktok", "共通", "TO-09"),
    make_rule("T40", "命名規則整合", "構造_設定", "low", "tiktok", "共通", "TO-11"),
    make_rule("T41", "リターゲティング分離", "構造_設定", "medium", "tiktok", "共通", "TO-01"),
    make_rule("T42", "iOS/Android分離", "構造_設定", "medium", "tiktok", "共通", "TO-01"),
    make_rule("T43", "広告グループ数バランス", "構造_設定", "medium", "tiktok", "共通", "TO-01"),
    make_rule("T44", "重複ターゲティング検出", "ターゲティング", "medium", "tiktok", "共通", "TO-01"),
    make_rule("T45", "配信タイプ妥当性", "構造_設定", "medium", "tiktok", "共通", "TO-11"),
    make_rule("T46", "自動入札+ターゲティング", "構造_設定", "medium", "tiktok", "共通", "TO-11"),
]


def main():
    print("Phase 2: check_id 書き換え + YAML追加")

    # Step 1: YAML新規ルール追加
    print("\n=== Step 1: YAML追加 ===")
    g_added = add_yaml_rules(os.path.join(RULES_DIR, "google_rules.yaml"), GOOGLE_NEW_RULES)
    print(f"  google_rules.yaml: +{g_added}件")

    m_added = add_yaml_rules(os.path.join(RULES_DIR, "meta_rules.yaml"), META_NEW_RULES)
    print(f"  meta_rules.yaml: +{m_added}件")

    t_added = add_yaml_rules(os.path.join(RULES_DIR, "tiktok_rules.yaml"), TIKTOK_NEW_RULES)
    print(f"  tiktok_rules.yaml: +{t_added}件")

    # Step 2: Python check_id 書き換え
    print("\n=== Step 2: Python check_id 書き換え ===")
    g_changes = rewrite_check_ids(os.path.join(CHECKS_DIR, "google.py"), GOOGLE_REMAP)
    print(f"  google.py: {g_changes}件 置換")

    m_changes = rewrite_check_ids(os.path.join(CHECKS_DIR, "meta.py"), META_REMAP)
    print(f"  meta.py: {m_changes}件 置換")

    t_changes = rewrite_check_ids(os.path.join(CHECKS_DIR, "tiktok.py"), TIKTOK_REMAP)
    print(f"  tiktok.py: {t_changes}件 置換")

    # Step 3: 件数確認
    print("\n=== Step 3: 件数確認 ===")
    for fname in ["google_rules.yaml", "meta_rules.yaml", "tiktok_rules.yaml"]:
        path = os.path.join(RULES_DIR, fname)
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        print(f"  {fname}: {len(data['rules'])}件")

    print("\n完了!")


if __name__ == "__main__":
    main()
