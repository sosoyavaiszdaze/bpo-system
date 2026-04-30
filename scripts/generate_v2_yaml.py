#!/usr/bin/env python3
"""v2.0 YAMLルール生成スクリプト — §9 CSVデータからYAMLを生成"""
import csv
import yaml
import io
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RULES_DIR = os.path.join(BASE_DIR, "config", "rules")


def parse_csv(csv_text):
    """CSV文字列をパースしてリストのリストを返す"""
    reader = csv.DictReader(io.StringIO(csv_text.strip()))
    return list(reader)


def to_list(val):
    """スペース区切り文字列をリストに変換"""
    if not val or val.strip() == "":
        return []
    return [v.strip() for v in val.replace(",", " ").split() if v.strip()]


def to_snake_case(s):
    """カテゴリ名をsnake_caseに変換: '計測・トラッキング' → '計測_トラッキング'"""
    return s.replace("・", "_").replace("　", "_").replace(" ", "_").replace("/", "_")


def axis_position(polarity):
    """§4-4: polarity→axis_position"""
    if polarity == "separate":
        return "left"
    if polarity == "aggregate":
        return "right"
    return "neutral"


def convert_row(row):
    """CSV行をv2.0 YAMLルール形式に変換"""
    rule = {
        "id": row["ID"],
        "name": row["チェック項目名"],
        "category": to_snake_case(row["カテゴリ"]),
        "severity": row["重要度"].lower(),
        "weight": 1.0,
        "platform": row["プラットフォーム"].lower(),
        "placement": row.get("配信面", "").strip() or "共通",
        "detection_layer": row.get("検出レイヤー", "Layer2"),
        "quick_win": row.get("Quick Win", "×") == "○",
        "primary_axis": row.get("primary軸", "").strip() or None,
        "secondary_axis": row.get("secondary軸", "").strip() or None,
        "axis_position": axis_position(row.get("polarity", "neutral")),
        "polarity": row.get("polarity", "neutral").strip() or "neutral",
        "prerequisite": to_list(row.get("prerequisite", "")),
        "conflicts": to_list(row.get("conflicts", "")),
        "dependencies": to_list(row.get("dependencies", "")),
        "conflict_group": None,
        "yonemitsu_alignment": row.get("米満整合度", "整合").replace("完全整合", "整合"),
        "redesign_note": row.get("再設計メモ", "").strip(),
        "enabled": True,
    }
    return rule


def calc_category_weights(rules):
    """カテゴリ出現頻度からweightsを生成"""
    counts = {}
    for r in rules:
        cat = r["category"]
        counts[cat] = counts.get(cat, 0) + 1
    total = sum(counts.values())
    weights = {}
    for cat, count in sorted(counts.items(), key=lambda x: -x[1]):
        weights[cat] = round(count / total, 2)
    # 合計が1.0に近くなるよう微調整
    return weights


def write_rules_yaml(platform_name, rules, output_path):
    """ルールYAMLを出力"""
    cw = calc_category_weights(rules)
    data = {
        "name": f"{platform_name} Audit Rules v2.0",
        "version": "2.0",
        "category_weights": cw,
        "rules": rules,
    }
    with open(output_path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
    print(f"  {output_path}: {len(rules)} rules, {len(cw)} categories")


# ========== CSV DATA ==========

GOOGLE_CSV = """ID,プラットフォーム,配信面,カテゴリ,チェック項目名,重要度,検出レイヤー,Quick Win,primary軸,secondary軸,polarity,prerequisite,conflicts,dependencies,米満整合度,再設計メモ
G01,Google,共通,計測・トラッキング,コンバージョン重複計測,Critical,Layer2,○,TO-11,TO-02,neutral,,G02 G06,G11,整合,計測の正しさは全運用判断の前提。最優先項目
G02,Google,共通,計測・トラッキング,コンバージョンカテゴリ設定,High,Layer2,×,TO-02,TO-04,preserve,G01,,G06 G11,整合,ポジ/ネガ両方のシグナルを学習に与える土台
G03,Google,共通,計測・トラッキング,拡張コンバージョン実装状況,High,Layer2,○,TO-02,TO-11,neutral,G01,,G11,整合,計測精度向上 = 学習シグナル精度向上
G04,Google,共通,計測・トラッキング,GA4連携状態,High,Layer2,×,TO-11,TO-02,neutral,G01,,G09,整合,外部シグナルの取り込み土台
G05,Google,共通,計測・トラッキング,タグ発火エラー,Critical,Layer3,○,TO-11,TO-02,neutral,,,G01-G10全て,整合,G01-10の前提となる物理的な発火確認
G06,Google,共通,計測・トラッキング,コンバージョン価値設定,High,Layer2,×,TO-02,TO-03,neutral,G01 G02,,G11(tROAS),整合,tROAS運用の前提条件
G07,Google,共通,計測・トラッキング,クロスデバイスコンバージョン,Medium,Layer2,×,TO-02,,preserve,G01,,,整合,ポジティブシグナルの取りこぼし防止
G08,Google,共通,計測・トラッキング,データドリブンアトリビューション,High,Layer2,×,TO-02,TO-11,neutral,G01 G04,,G11,整合,シグナル評価の基準軸そのもの
G09,Google,共通,計測・トラッキング,オーディエンスリスト同期,Medium,Layer2,×,TO-02,TO-11,neutral,G04,,,整合,ファーストパーティデータをシグナルとして注入
G10,Google,共通,計測・トラッキング,オフラインコンバージョンインポート,High,Layer2,×,TO-02,TO-10,preserve,G01,,G11,整合,B2B長期CVを学習に与える
G11,Google,共通,予算・入札,入札戦略と目標値の整合,Critical,Layer2,○,TO-03,TO-11,neutral,G01 G02,G14,G12 G44,整合,TO-03の中核。tCPA/tMaxConv選択の妥当性
G12,Google,共通,予算・入札,学習フェーズ滞留,High,Layer2,○,TO-10,TO-03,preserve,G11 G13 G14,,,整合,学習未完了 = 短期判断を急いで長期最適化を毀損
G13,Google,共通,予算・入札,予算制限による機会損失,High,Layer2,○,TO-09,TO-03,budget_first,G11,,G44,整合,TO-09の中核。Budget Lost先行解消
G14,Google,共通,予算・入札,入札戦略の頻繁な変更,High,Layer2,○,TO-10,TO-11,preserve,G11,G11,G12,整合,短期判断の積み重ねが長期学習を破壊
G15,Google,共通,予算・入札,共有予算の適切性,Medium,Layer2,×,TO-09,TO-11,neutral,G13,,,整合,Budget Lost解消の手段の一つ
G16,Google,共通,予算・入札,季節性調整の設定,Medium,Layer2,×,TO-11,TO-10,neutral,G11,G14,,整合,計画的な人的介入として正当
G17,Google,共通,構造・設定,キャンペーンタイプの目的一致,High,Layer4,×,TO-06,TO-11,neutral,,,G39 G40,整合,1コンテンツ1AG原則の上位概念
G18,Google,共通,構造・設定,広告スケジュール最適化,Medium,Layer2,○,TO-11,TO-03,neutral,G11,,,整合,頻度の幅(時間)制御。自動入札時は不要な場合多し
G19,Google,共通,構造・設定,デバイス別入札調整,Medium,Layer2,○,TO-11,TO-03,neutral,G11,,,要再定義,自動入札時は調整比は無効。tCPA調整比として動作
G20,Google,共通,構造・設定,地域入札調整,High,Layer2,○,TO-11,TO-09,neutral,G11,,G21,整合,ネガティブシグナル(低パフォ地域)の保持
G21,Google,共通,構造・設定,除外地域の適切性,Medium,Layer2,○,TO-11,,neutral,,,G20,整合,明確に商圏外なら除外
G22,Google,共通,構造・設定,広告表示オプションの網羅,Medium,Layer2,○,TO-08,TO-04,neutral,,,G34,整合,目立ち度向上 = pCTR向上 = Ad Rank向上
G23,Google,共通,構造・設定,ポリシー違反・不承認,High,Layer2,○,TO-11,,neutral,,,,整合,配信前提条件のチェック
G24,Google,共通,構造・設定,キャンペーン/広告グループ停止忘れ,Medium,Layer2,○,TO-11,TO-02,neutral,,,,整合,意図のない予算消費を防ぐ
G25,Google,共通,構造・設定,ネーミングルール整合,Low,Layer4,×,TO-11,,neutral,,,,整合,分析時の障害除去
G26,Google,Search,キーワード,品質スコア低下キーワード,High,Layer2,○,TO-04,TO-05,monitor_only,,G28 G35,G26-NEW1 G26-NEW2,概念的に問題,重要: 米満氏noteによれば品質スコアはAd Rank算出に使われない結果指標。停止判断の根拠にしない。モニタリングのみ
G27,Google,Search,キーワード,ネガティブキーワード不足,Critical,Layer2,○,TO-02,TO-05,preserve,,,G28 G29,整合,ネガティブシグナルの保持は学習有意差を高める
G28,Google,Search,キーワード,検索語句レポート未反映,Critical,Layer2,○,TO-05,TO-02,open,G27,,G27,整合,クエリ→KW追加→可視化のサイクル
G29,Google,Search,キーワード,マッチタイプの偏り,High,Layer2,×,TO-05,TO-02,open,G11 G27,,,整合,自動入札+除外整備済みなら部分一致(インテントマッチ)推奨
G30,Google,Search,キーワード,重複キーワード,Medium,Layer2,○,TO-01,TO-05,aggregate,,,G39,整合,細分化由来の自社競合
G31,Google,Search,キーワード,ブランドKWと一般KWの混在,High,Layer2,×,TO-01,TO-04,separate,,,,整合,評価軸が異なるので分離が正しい
G32,Google,Search,キーワード,低インプレッションKW,Low,Layer2,×,TO-05,TO-02,preserve,,,,要再定義,削除でなく分析ノイズ低減策へ。低IMP=ネガティブシグナル候補
G33,Google,Search,キーワード,競合KWの露出シェア,Medium,Layer2,×,TO-09,TO-04,neutral,,,G44 G45,整合,オークションインサイト活用
G34,Google,Search,クリエイティブ,RSA広告強度,High,Layer2,○,TO-08,TO-04,neutral,G37 G38,,,整合,短い見出し x 多バリエーションを評価
G35,Google,Search,クリエイティブ,3x Kill Rule違反,Critical,Layer2,○,TO-07,TO-02,context_dependent,G11,G34,G26-NEW1,要再定義,重要: 自動入札時は止めずに編集。手動入札時のみ停止検討。文脈依存
G36,Google,Search,クリエイティブ,広告文の重複,Medium,Layer4,×,TO-06,TO-08,neutral,,,,整合,ABテスト機会と統計的有意差の確保
G37,Google,Search,クリエイティブ,見出し・説明文数,Medium,Layer2,○,TO-08,TO-04,neutral,,,G34,整合,バリエーション最大化の前提
G38,Google,Search,クリエイティブ,ピン留め過剰,Medium,Layer2,×,TO-08,TO-11,neutral,,,G34,整合,システム最適化の阻害要因
G39,Google,Search,構造・設定,広告グループあたりKW数,Medium,Layer2,×,TO-01,TO-05,neutral,,,G40,整合,20KW超は過密。集約 or 細分化判断
G40,Google,Search,構造・設定,SKAG/STAG構造の妥当性,Medium,Layer4,×,TO-01,TO-10,aggregate,,G39,G39,整合,SKAGは粒度過剰でデータ希薄。集約推奨が妥当
G41,Google,Search,構造・設定,入札単価上限の妥当性,High,Layer2,×,TO-03,TO-09,neutral,G11,,G44,整合,深度の天井で機会損失
G42,Google,Search,構造・設定,検索パートナー配信,Medium,Layer2,○,TO-02,,neutral,,,,整合,ノイズ流入の除外判断
G43,Google,Search,構造・設定,動的検索広告のターゲット,High,Layer2,×,TO-05,TO-02,neutral,G27,,,整合,DSAは集約型。除外整備が前提
G44,Google,Search,構造・設定,インプレッションシェア上位損失率,High,Layer2,×,TO-09,TO-04,neutral,G13,,G45,整合,TO-09の中核。Budget/Ad Rank分解の入口
G45,Google,Search,構造・設定,絶対トップIS損失率,Medium,Layer2,×,TO-09,TO-04,neutral,G44,,,整合,ページ最上部IS。米満氏が広告クリエイティブ評価軸として強調
G46,Google,Shopping,フィード,フィードエラー・警告,Critical,Layer2,○,TO-11,,neutral,,,,整合,Shopping配信の前提
G47,Google,Shopping,フィード,タイトル最適化,High,Layer4,×,TO-08,TO-06,neutral,,,,整合,商品KW × 訴求要素のバランス
G48,Google,Shopping,フィード,画像品質,High,Layer3,×,TO-06,,neutral,,,,整合,ユーザー認知の前提
G49,Google,Shopping,フィード,在庫切れ商品の配信,High,Layer2,○,TO-06,TO-02,neutral,,,,整合,LP整合性の保証
G50,Google,Shopping,フィード,価格競争力,Medium,Layer2,×,TO-04,TO-09,neutral,,,,整合,Ad Rank的要因(競争力)
G51,Google,Shopping,構造・設定,商品グループ細分化,High,Layer2,×,TO-01,TO-10,neutral,,,,整合,Shoppingでも構造粒度のトレードオフ
G52,Google,Shopping,構造・設定,プロモーション拡張の設定,Medium,Layer2,○,TO-08,,neutral,,,,整合,目立ち度向上
G53,Google,Shopping,構造・設定,ショッピング優先度設定,High,Layer2,×,TO-01,TO-09,neutral,,G70,,整合,Standard vs PMAX重複の構造調停
G54,Google,GDN,配信面,低品質プレースメント除外,Critical,Layer2,○,TO-02,TO-05,preserve,,,,整合,ネガティブシグナル保持
G55,Google,GDN,配信面,アプリ面除外設定,High,Layer2,○,TO-02,,preserve,,,,整合,誤クリック由来のノイズ除外
G56,Google,GDN,クリエイティブ,ビューアビリティ,Medium,Layer2,×,TO-08,,neutral,,,,整合,視認性の物理的測定
G57,Google,GDN,ターゲティング,オーディエンス拡張の挙動,High,Layer2,×,TO-05,TO-02,neutral,,,,整合,拡張は学習を促進 過拡張は無駄配信
G58,Google,GDN,クリエイティブ,レスポンシブディスプレイ広告の素材数,Medium,Layer2,○,TO-08,,neutral,,,,整合,RSA同様 バリエーション幅
G59,Google,YouTube,クリエイティブ,動画完了率,High,Layer2,×,TO-08,TO-04,neutral,,,G63,整合,視聴維持 = pCTR/pVTR向上
G60,Google,YouTube,予算・入札,CPV効率,Medium,Layer2,×,TO-03,,neutral,G11,,,整合,深度のCPV版
G61,Google,YouTube,ターゲティング,オーディエンス適合性,Medium,Layer4,×,TO-06,,neutral,,,,整合,ブランドセーフティ
G62,Google,YouTube,構造・設定,動画広告フォーマット混在,Medium,Layer2,×,TO-06,TO-08,neutral,,,,整合,目的とフォーマットの整合
G63,Google,YouTube,クリエイティブ,冒頭5秒のフック,Medium,Layer4,×,TO-08,TO-04,neutral,,,,整合,動画版の見出し相当
G64,Google,P-MAX,構造・設定,アセットグループ複数化,High,Layer2,○,TO-01,TO-10,neutral,,G65 G67,G73,整合,PMAX内構造の粒度
G65,Google,P-MAX,クリエイティブ,画像アセット数,Medium,Layer2,○,TO-08,,neutral,,,G64,整合,バリエーション幅
G66,Google,P-MAX,クリエイティブ,動画アセット自動生成,High,Layer2,○,TO-11,TO-06,neutral,,,,整合,システム自動化に任せすぎるリスク
G67,Google,P-MAX,クリエイティブ,見出し・説明文のパターン数,Medium,Layer2,×,TO-08,,neutral,,,G64,整合,テキスト版バリエーション幅
G68,Google,P-MAX,ターゲティング,オーディエンスシグナル設定,High,Layer2,○,TO-02,TO-11,neutral,G09,,,整合,学習初期値の人的設計
G69,Google,P-MAX,構造・設定,URL拡張の設定,High,Layer2,×,TO-06,TO-11,neutral,,,,整合,LP個別最適 vs 統一
G70,Google,P-MAX,構造・設定,ブランドKW除外リスト,High,Layer2,○,TO-01,TO-09,separate,,G31,G53,整合,Search/PMAX重複防止
G71,Google,P-MAX,構造・設定,商品フィード連携,High,Layer2,×,TO-11,,neutral,G46,,,整合,PMAXのEC前提条件
G72,Google,P-MAX,ターゲティング,除外オーディエンス,Medium,Layer2,×,TO-02,,preserve,G09,,,整合,ネガティブシグナル
G73,Google,P-MAX,構造・設定,アセットグループ別パフォーマンス分析,Medium,Layer4,×,TO-04,TO-11,neutral,G64,,,整合,PMAX内ブラックボックスの可視化
G74,Google,Demand Gen,クリエイティブ,縦型動画素材の有無,Medium,Layer2,×,TO-08,TO-06,neutral,,,,整合,プレースメント別フォーマット
G75,Google,Demand Gen,予算・入札,入札戦略の適合性,Medium,Layer2,×,TO-03,,neutral,G11,,,整合,深度x頻度バランス
G76,Google,Demand Gen,ターゲティング,類似オーディエンス活用,Medium,Layer2,×,TO-02,,neutral,G09,,,整合,学習シグナルの拡張
G77,Google,Demand Gen,クリエイティブ,カルーセル素材多様性,Low,Layer4,×,TO-08,,neutral,,,,整合,バリエーション幅
G78,Google,App,計測・トラッキング,インストールCV計測,Critical,Layer2,○,TO-11,TO-02,neutral,,,G79,整合,App前提条件
G79,Google,App,計測・トラッキング,アプリ内イベント最適化,High,Layer2,×,TO-02,TO-04,preserve,G78,,,整合,深層イベントを学習に与える
G80,Google,App,構造・設定,ディープリンク設定,Medium,Layer2,×,TO-06,,neutral,,,,整合,LP-広告整合性のApp版
G26-NEW1,Google,Search,クリエイティブ,Ad Rank Lost原因分解 (新設),Critical,Layer2,○,TO-04,TO-09,neutral,G44,,G34 G37,新設,新設: G26を結果指標から原因変数に置き換え。pCTR低/CPC低/品質スコア低の3分解
G26-NEW2,Google,Search,クリエイティブ,推定CTR低文字列特定 (新設),High,Layer4,×,TO-04,TO-08,neutral,G26-NEW1,,G34,新設,新設: 品質スコア構成要素の推定CTR低KW群から視認性課題の文字列を特定
G45-NEW,Google,Search,計測,ページ最上部/上部IS推移 (新設),High,Layer2,×,TO-09,TO-04,neutral,G44,,G45,新設,新設: 米満氏note06が強調する評価軸。競合動向と並べてモニタリング
G81-NEW,Google,共通,判断ログ,トレードオフ判断ログ生成 (新設),Critical,Layer4,×,TO-10,TO-11,preserve,,,,新設,新設: Zynect独自の説明責任インフラ。月次レポートに搭載
G82-NEW,Google,共通,計測,オークションインサイト分析 (新設),High,Layer2,×,TO-09,TO-04,neutral,G44,,G45-NEW,新設,新設: 競合の入札強化/参入を月次で切り分け"""

META_CSV = """ID,プラットフォーム,配信面,カテゴリ,チェック項目名,重要度,検出レイヤー,Quick Win,primary軸,secondary軸,polarity,prerequisite,conflicts,dependencies,米満整合度,再設計メモ
M01,Meta,共通,計測・トラッキング,Pixel発火状態,Critical,Layer3,○,TO-11,TO-02,neutral,,,M02-M08,整合,計測の前提
M02,Meta,共通,計測・トラッキング,CAPI実装状況,Critical,Layer2,○,TO-11,TO-02,neutral,M01,,M03 M06,整合,iOS時代の必須
M03,Meta,共通,計測・トラッキング,イベントマッチ品質,High,Layer2,○,TO-02,,neutral,M02,,,整合,シグナル精度の指標
M04,Meta,共通,計測・トラッキング,ドメイン検証,Critical,Layer2,○,TO-11,,neutral,,,M05,整合,8イベント枠の前提
M05,Meta,共通,計測・トラッキング,優先度イベントの設定,High,Layer2,×,TO-02,TO-11,neutral,M04,,,整合,学習優先順位の人的設計
M06,Meta,共通,計測・トラッキング,重複イベント排除,High,Layer2,○,TO-11,,neutral,M01 M02,,,整合,Pixel/CAPI二重計測防止
M07,Meta,共通,計測・トラッキング,カスタムコンバージョン設定,Medium,Layer2,×,TO-02,,neutral,M01,,,整合,シグナル定義の柔軟化
M08,Meta,共通,計測・トラッキング,iOS14+の影響計測,High,Layer4,×,TO-02,TO-04,neutral,M02,,,整合,計測欠損の認識
M09,Meta,共通,予算・入札,学習フェーズ脱出率,Critical,Layer2,○,TO-10,TO-02,preserve,M10 M13,,,整合,Meta特有の50CV/週基準
M10,Meta,共通,予算・入札,1広告セットあたりCV数,High,Layer2,×,TO-01,TO-10,aggregate,,M15,M09,整合,Meta特有の集約推奨基準
M11,Meta,共通,予算・入札,CBO vs ABO選択,Medium,Layer4,×,TO-11,TO-03,neutral,,,,整合,予算配分の制御方式選択
M12,Meta,共通,予算・入札,目標費用設定,High,Layer2,×,TO-03,TO-09,neutral,M09,,,整合,Meta版の深度制御
M13,Meta,共通,予算・入札,予算変更の頻度,High,Layer2,○,TO-10,TO-11,preserve,,,M09,整合,学習リセット防止
M14,Meta,共通,構造・設定,キャンペーン目的の適正,High,Layer4,×,TO-11,TO-06,neutral,,,,整合,目的とKPIの整合
M15,Meta,共通,構造・設定,広告セット過多,Medium,Layer2,×,TO-01,TO-10,aggregate,M10,,,整合,Meta版TO-01
M16,Meta,共通,構造・設定,ポリシー違反・不承認,High,Layer2,○,TO-11,,neutral,,,,整合,配信前提
M17,Meta,共通,構造・設定,アカウント品質スコア,High,Layer2,○,TO-04,,monitor_only,,,,整合,結果指標として参照のみ
M18,Meta,共通,構造・設定,広告アカウント制限リスク,Critical,Layer2,○,TO-11,,neutral,,,,整合,事業継続性の前提
M19,Meta,共通,構造・設定,ビジネスマネージャー権限,Medium,Layer2,×,TO-11,,neutral,,,,整合,セキュリティ
M20,Meta,共通,構造・設定,カタログ連携状態,High,Layer2,○,TO-11,TO-06,neutral,,,M44-M48,整合,Advantage+前提
M21,Meta,FB フィード,クリエイティブ,FBフィードCTR,Medium,Layer2,×,TO-08,TO-04,neutral,,,,整合,
M22,Meta,FB フィード,クリエイティブ,テキスト量過多,Medium,Layer4,○,TO-08,,neutral,,,,整合,Meta特有制約
M23,Meta,FB フィード,構造・設定,プレースメント手動最適化,Medium,Layer2,×,TO-11,TO-02,neutral,,,,整合,自動最適化阻害の例
M24,Meta,FB フィード,クリエイティブ,動画/画像の使い分け,Low,Layer4,×,TO-08,,neutral,,,,整合,フォーマット多様性
M25,Meta,FB フィード,クリエイティブ,1:1と4:5のアスペクト比,Low,Layer2,○,TO-08,,neutral,,,,整合,表示面積最大化
M26,Meta,IG フィード,クリエイティブ,IGフィードのビジュアル品質,Medium,Layer4,×,TO-06,,neutral,,,,整合,ブランド整合性
M27,Meta,IG フィード,クリエイティブ,エンゲージメント率,Medium,Layer2,×,TO-04,TO-08,neutral,,,,整合,結果指標
M28,Meta,IG フィード,クリエイティブ,UGC風素材の活用,Medium,Layer4,×,TO-08,TO-06,neutral,,,,整合,訴求軸の多様化
M29,Meta,IG フィード,クリエイティブ,キャプション文字数,Low,Layer4,×,TO-08,,neutral,,,,整合,訴求網羅
M30,Meta,IG フィード,クリエイティブ,プロフィール誘導の整合,Low,Layer3,×,TO-06,,neutral,,,,整合,導線整合
M31,Meta,IG リール,クリエイティブ,リール動画尺,Medium,Layer2,○,TO-08,,neutral,,,M34,整合,プラットフォーム最適尺
M32,Meta,IG リール,クリエイティブ,縦型9:16フル画面対応,Medium,Layer4,○,TO-08,TO-06,neutral,,,,整合,セーフゾーン
M33,Meta,IG リール,クリエイティブ,サウンド有無,Medium,Layer4,○,TO-08,,neutral,,,,整合,リール特性
M34,Meta,IG リール,クリエイティブ,動画完了率,Medium,Layer2,×,TO-04,TO-08,neutral,M31,,,整合,結果指標
M35,Meta,IG リール,クリエイティブ,UGC/Spark系素材,Medium,Layer4,×,TO-08,,neutral,,,,整合,オーガニック融合
M36,Meta,IG ストーリーズ,クリエイティブ,ストーリーズフルスクリーン活用,Medium,Layer2,○,TO-08,,neutral,,,,整合,
M37,Meta,IG ストーリーズ,クリエイティブ,スワイプアップ/CTA訴求,Low,Layer4,×,TO-06,,neutral,,,,整合,
M38,Meta,FB ストーリーズ,クリエイティブ,FBストーリーズのCTR,Low,Layer2,×,TO-08,TO-04,neutral,,,,整合,
M39,Meta,Audience Network,構造・設定,AN品質管理,High,Layer2,○,TO-02,,preserve,,,,整合,ネガティブ保持
M40,Meta,Audience Network,構造・設定,ブランドセーフティ除外,Medium,Layer2,×,TO-02,TO-06,preserve,,,,整合,
M41,Meta,Audience Network,クリエイティブ,AN専用クリエイティブ,Low,Layer4,×,TO-08,,neutral,,,,整合,
M42,Meta,Messenger,計測・トラッキング,Messenger配信のCV計測,Medium,Layer2,×,TO-11,TO-02,neutral,M01,,,整合,
M43,Meta,Messenger,構造・設定,自動応答フロー,Medium,Layer3,×,TO-06,,neutral,,,,整合,LP-広告連続性
M44,Meta,Advantage+ Shopping,構造・設定,Advantage+カタログ整合,High,Layer2,×,TO-11,TO-06,neutral,M20,,,整合,ASC前提
M45,Meta,Advantage+ Shopping,予算・入札,入札上限の適正,High,Layer2,×,TO-03,,neutral,,,,整合,深度制御
M46,Meta,Advantage+ Shopping,クリエイティブ,既存顧客予算キャップ,Medium,Layer2,×,TO-11,TO-02,neutral,,,,整合,新規/既存配分の人的制御
M47,Meta,Advantage+ Shopping,クリエイティブ,クリエイティブバリエーション,Medium,Layer4,○,TO-08,,neutral,,,,整合,
M48,Meta,Advantage+ Shopping,構造・設定,ASC+との共存,Medium,Layer2,×,TO-01,TO-09,separate,,,,整合,予算競合の構造解消
M49,Meta,共通,ターゲティング,オーディエンスオーバーラップ,High,Layer2,○,TO-01,TO-05,aggregate,,,,整合,Meta版自社競合解消
M50,Meta,共通,ターゲティング,LLA(類似オーディエンス)の鮮度,Medium,Layer2,×,TO-02,,neutral,,,,整合,シグナル鮮度
M51,Meta,共通,ターゲティング,カスタムオーディエンス更新頻度,Medium,Layer2,×,TO-02,,neutral,,,M50,整合,ファーストパーティ鮮度
M52,Meta,共通,ターゲティング,広すぎる/狭すぎる設定,High,Layer2,×,TO-01,TO-05,neutral,M10,,,整合,Meta版粒度判定
M53,Meta,共通,ターゲティング,既存顧客除外設定,Medium,Layer2,○,TO-02,TO-11,preserve,,,,整合,新規獲得時のネガ保持
M54,Meta,共通,ターゲティング,Advantage詳細ターゲット+設定,Medium,Layer2,×,TO-05,TO-11,neutral,,,,整合,自動拡張の制御
M55,Meta,共通,ターゲティング,年齢/性別の過剰絞り込み,Medium,Layer2,×,TO-01,TO-05,open,,,M52,整合,Meta版マッチタイプ開放議論"""

TIKTOK_CSV = """ID,プラットフォーム,配信面,カテゴリ,チェック項目名,重要度,検出レイヤー,Quick Win,primary軸,secondary軸,polarity,prerequisite,conflicts,dependencies,米満整合度,再設計メモ
T01,TikTok,共通,計測・トラッキング,TikTok Pixel発火,Critical,Layer3,○,TO-11,TO-02,neutral,,,T02-T05,整合,
T02,TikTok,共通,計測・トラッキング,Events API実装,Critical,Layer2,○,TO-11,TO-02,neutral,T01,,,整合,
T03,TikTok,共通,計測・トラッキング,イベント最適化設定,High,Layer2,×,TO-02,TO-04,preserve,T01,,,整合,深層イベント
T04,TikTok,共通,計測・トラッキング,CVイベントの優先度,Medium,Layer2,×,TO-02,TO-11,neutral,T03,,,整合,
T05,TikTok,共通,計測・トラッキング,マッチ品質,High,Layer2,○,TO-02,,neutral,T01 T02,,,整合,
T06,TikTok,共通,予算・入札,学習フェーズ停滞,High,Layer2,○,TO-10,TO-02,preserve,T07 T09,,,整合,
T07,TikTok,共通,予算・入札,1広告グループあたりCV数,Medium,Layer2,×,TO-01,TO-10,aggregate,,,T06,整合,TikTok版集約基準
T08,TikTok,共通,予算・入札,入札戦略の適合性,Medium,Layer2,×,TO-03,,neutral,,,,整合,
T09,TikTok,共通,予算・入札,予算変更の頻度,Medium,Layer2,○,TO-10,TO-11,preserve,,,T06,整合,
T10,TikTok,共通,構造・設定,キャンペーン目的の整合,Medium,Layer4,×,TO-11,TO-06,neutral,,,,整合,
T11,TikTok,共通,構造・設定,広告不承認の放置,High,Layer2,○,TO-11,,neutral,,,,整合,
T12,TikTok,共通,構造・設定,アカウント警告,Critical,Layer2,○,TO-11,,neutral,,,,整合,
T13,TikTok,共通,クリエイティブ,クリエイティブ疲労,High,Layer2,○,TO-08,TO-07,neutral,,T14,,整合,TikTok特有の疲労速度
T14,TikTok,共通,クリエイティブ,クリエイティブ本数,Medium,Layer2,×,TO-08,TO-02,neutral,,,T13,整合,週5本投入
T15,TikTok,共通,クリエイティブ,Creative Center活用,Low,Layer4,×,TO-08,TO-11,neutral,,,,整合,
T16,TikTok,In-Feed,クリエイティブ,動画尺9-15秒,Medium,Layer2,○,TO-08,,neutral,,,,整合,
T17,TikTok,In-Feed,クリエイティブ,サウンドオン前提の設計,Medium,Layer4,○,TO-08,TO-06,neutral,,,,整合,
T18,TikTok,In-Feed,クリエイティブ,CTRのベンチマーク比,Medium,Layer2,×,TO-04,TO-08,neutral,,,,整合,結果指標
T19,TikTok,In-Feed,クリエイティブ,フックの強さ,Medium,Layer4,×,TO-08,TO-04,neutral,,,,整合,冒頭3秒
T20,TikTok,In-Feed,クリエイティブ,縦型9:16対応,Low,Layer4,×,TO-08,,neutral,,,,整合,
T21,TikTok,TopView,クリエイティブ,ブランドリフト指標,Medium,Layer2,×,TO-04,,monitor_only,,,,整合,
T22,TikTok,TopView,構造・設定,到達率と頻度,Medium,Layer2,×,TO-03,,neutral,,,,整合,頻度x深度のリーチ版
T23,TikTok,Spark Ads,構造・設定,Spark Ads権限取得,Medium,Layer2,×,TO-11,,neutral,,,T24-T26,整合,オーガニック融合の前提
T24,TikTok,Spark Ads,クリエイティブ,オーガニックエンゲージ率,Medium,Layer4,×,TO-08,TO-04,neutral,T23,,,整合,
T25,TikTok,Spark Ads,クリエイティブ,クリエイター多様性,Low,Layer4,×,TO-08,,neutral,,,,整合,リスク分散
T26,TikTok,Spark Ads,クリエイティブ,オーガニック連携整合,Low,Layer4,×,TO-06,,neutral,,,,整合,
T27,TikTok,Search Ads,ターゲティング,Search Ads KW設計,Medium,Layer2,×,TO-05,TO-01,neutral,,,,整合,TikTok版検索広告
T28,TikTok,Search Ads,予算・入札,Search Ads入札,Medium,Layer2,×,TO-03,,neutral,,,,整合,
T29,TikTok,Search Ads,構造・設定,除外KW設定,Medium,Layer2,○,TO-02,TO-05,preserve,,,,整合,ネガ保持
T30,TikTok,Pangle,構造・設定,Pangle品質管理,High,Layer2,○,TO-02,,preserve,,,,整合,GDN AN相当
T31,TikTok,Pangle,構造・設定,Pangleブランドセーフティ,Medium,Layer2,×,TO-02,TO-06,preserve,,,,整合,
T32,TikTok,Smart+,構造・設定,Smart+自動最適化設定,High,Layer2,×,TO-11,TO-10,neutral,,,T33-T35,整合,PMAX相当
T33,TikTok,Smart+,予算・入札,Smart+ROAS目標,High,Layer2,×,TO-03,TO-09,neutral,T32,,,整合,
T34,TikTok,Smart+,クリエイティブ,Smart+クリエイティブプール,Medium,Layer2,○,TO-08,TO-02,neutral,T32,,,整合,学習素材プール
T35,TikTok,Smart+,構造・設定,Smart+とマニュアル共存,Medium,Layer2,×,TO-01,TO-09,separate,,G53 M48,,整合,PMAX/ASC同構造"""

SEO_CSV = """ID,プラットフォーム,配信面,カテゴリ,チェック項目名,重要度,検出レイヤー,Quick Win,primary軸,secondary軸,polarity,prerequisite,conflicts,dependencies,米満整合度,再設計メモ
S01,SEO,サイト全体,構造・設定,robots.txt設定,Critical,Layer3,○,TO-11,,neutral,,,S17,整合,クロール制御の前提
S02,SEO,サイト全体,構造・設定,sitemap.xmlの存在と整合,High,Layer3,○,TO-11,,neutral,S01,,S20,整合,
S03,SEO,サイト全体,構造・設定,HTTPS/SSL,High,Layer3,○,TO-11,,neutral,,,,整合,技術的前提
S04,SEO,サイト全体,Core Web Vitals,LCP,Critical,Layer3,○,TO-06,TO-04,neutral,S09 S10 S29,,,整合,UX結果指標
S05,SEO,サイト全体,Core Web Vitals,CLS,High,Layer3,×,TO-06,,neutral,,,,整合,
S06,SEO,サイト全体,Core Web Vitals,INP,High,Layer3,×,TO-06,,neutral,,,,整合,
S07,SEO,サイト全体,モバイル,モバイルフレンドリー,Critical,Layer3,○,TO-06,,neutral,,,S08 S43,整合,
S08,SEO,サイト全体,モバイル,ビューポート設定,High,Layer3,○,TO-06,,neutral,,,S07,整合,
S09,SEO,サイト全体,サイト速度,サーバー応答時間,High,Layer3,×,TO-11,,neutral,,,S04,整合,
S10,SEO,サイト全体,サイト速度,画像最適化,Medium,Layer3,○,TO-11,,neutral,,,S04 S28,整合,
S11,SEO,サイト全体,構造化データ,Schema.org実装,High,Layer3,○,TO-08,TO-06,neutral,,,S12 S32,整合,リッチリザルト
S12,SEO,サイト全体,構造化データ,構造化データエラー,High,Layer2,○,TO-11,,neutral,S11,,,整合,
S13,SEO,サイト全体,国際SEO,hreflang整合,High,Layer3,×,TO-06,,neutral,,,,整合,
S14,SEO,サイト全体,構造・設定,canonical整合,High,Layer3,○,TO-06,TO-01,neutral,,S15,,整合,重複の正規化
S15,SEO,サイト全体,構造・設定,重複コンテンツ,High,Layer4,×,TO-06,TO-01,aggregate,,,S14 S44,整合,1コンテンツ1URL原則
S16,SEO,サイト全体,構造・設定,クロールエラー,High,Layer2,○,TO-11,,neutral,,,,整合,
S17,SEO,サイト全体,構造・設定,インデックスカバレッジ,Critical,Layer2,○,TO-11,,neutral,S01 S02 S20,,,整合,
S18,SEO,サイト全体,内部リンク,内部リンク構造,High,Layer3,×,TO-11,TO-06,neutral,,,S37,整合,
S19,SEO,サイト全体,構造・設定,404/リダイレクト設計,Medium,Layer3,○,TO-11,,neutral,,,,整合,
S20,SEO,サイト全体,構造・設定,XMLサイトマップ内エラー,Medium,Layer2,×,TO-11,,neutral,S02,,,整合,
S21,SEO,ページ別,メタ情報,titleタグ長さ,High,Layer3,○,TO-08,TO-06,neutral,,,S22,整合,見出し相当
S22,SEO,ページ別,メタ情報,title重複,High,Layer3,○,TO-06,TO-01,neutral,,S15,,整合,1ページ1title
S23,SEO,ページ別,メタ情報,meta description,High,Layer3,○,TO-08,,neutral,,,S24,整合,
S24,SEO,ページ別,メタ情報,meta description重複,Medium,Layer3,○,TO-06,,neutral,,,,整合,
S25,SEO,ページ別,見出し構造,H1の存在と単一性,High,Layer3,○,TO-08,TO-06,neutral,,,S26,整合,
S26,SEO,ページ別,見出し構造,H2/H3階層の論理性,Medium,Layer3,×,TO-06,,neutral,S25,,,整合,
S27,SEO,ページ別,画像最適化,画像alt属性,High,Layer3,○,TO-08,,neutral,,,,整合,
S28,SEO,ページ別,画像最適化,画像ファイルサイズ,Medium,Layer3,○,TO-11,,neutral,,,S04,整合,
S29,SEO,ページ別,画像最適化,画像lazy-loading,Medium,Layer3,×,TO-11,,neutral,,,S04,整合,
S30,SEO,ページ別,OGP,OGP設定,High,Layer3,○,TO-08,TO-06,neutral,,,S31,整合,
S31,SEO,ページ別,OGP,Twitter Card,Medium,Layer3,○,TO-08,,neutral,S30,,,整合,
S32,SEO,ページ別,構造化データ,JSON-LD実装,High,Layer3,○,TO-08,TO-04,neutral,S11,,,整合,
S33,SEO,ページ別,コンテンツ,コンテンツ文字数,Medium,Layer3,×,TO-08,TO-06,neutral,,,,整合,
S34,SEO,ページ別,コンテンツ,キーワード出現密度,Medium,Layer4,×,TO-08,TO-06,neutral,,,,整合,
S35,SEO,ページ別,コンテンツ,E-E-A-T要素,High,Layer4,×,TO-06,TO-04,neutral,,,,整合,YMYL対応
S36,SEO,ページ別,コンテンツ,コンテンツの鮮度,Medium,Layer2,○,TO-10,TO-02,preserve,,,,整合,ポジティブシグナルの維持
S37,SEO,ページ別,内部リンク,内部リンク数,Medium,Layer3,×,TO-11,,neutral,,,,整合,
S38,SEO,ページ別,内部リンク,アンカーテキスト最適化,Medium,Layer4,×,TO-08,,neutral,,,,整合,
S39,SEO,ページ別,外部リンク,外部リンクの品質,Medium,Layer4,×,TO-02,,preserve,,,,整合,ネガリンク保持判断
S40,SEO,ページ別,外部リンク,外部リンクのnofollow/UGC,Medium,Layer3,×,TO-11,,neutral,,,,整合,
S41,SEO,ページ別,UX,CTA配置,High,Layer4,○,TO-06,TO-08,neutral,,,S42,整合,LP-広告連続性
S42,SEO,ページ別,UX,ファーストビュー情報設計,High,Layer4,○,TO-08,TO-06,neutral,,,S41,整合,
S43,SEO,ページ別,UX,モバイルレンダリング,High,Layer3,○,TO-06,,neutral,S07,,,整合,
S44,SEO,ページ別,コンテンツ,共食いコンテンツ,High,Layer4,×,TO-01,TO-06,aggregate,,,S15,整合,Hagakure原典そのもの。1コンテンツ1ページ原則
S45,SEO,ページ別,メタ情報,noindex誤設定,Critical,Layer3,○,TO-11,,neutral,,,S17,整合,"""

ADTRUTH_CSV = """ID,プラットフォーム,配信面,カテゴリ,チェック項目名,重要度,検出レイヤー,Quick Win,primary軸,secondary軸,polarity,prerequisite,conflicts,dependencies,米満整合度,再設計メモ
F01,AdTruth,全媒体,不正検知,クリック不正検出,Critical,Layer1,○,TO-02,TO-09,preserve,,,F02-F12,整合,ボット流入はネガシグナル汚染の最大要因。学習資源を守る
F02,AdTruth,全媒体,不正検知,ボットスコア判定,Critical,Layer1,○,TO-02,,preserve,,,,整合,
F03,AdTruth,全媒体,不正検知,コンバージョン不正検出,High,Layer1,○,TO-02,TO-04,preserve,,,,整合,不正CV = 偽ポジティブシグナル。学習を破壊する最悪パターン
F04,AdTruth,全媒体,不正検知,インプレッション不正,High,Layer1,○,TO-09,TO-02,preserve,,,,整合,
F05,AdTruth,全媒体,不正検知,地域不整合,High,Layer1,○,TO-02,,preserve,,,,整合,
F06,AdTruth,全媒体,不正検知,デバイスフィンガープリント異常,High,Layer1,○,TO-02,,preserve,,,,整合,
F07,AdTruth,全媒体,不正検知,異常クリックパターン,Medium,Layer1,○,TO-02,,preserve,,,,整合,
F08,AdTruth,全媒体,不正検知,リファラー偽装,Medium,Layer1,○,TO-02,,preserve,,,,整合,
F09,AdTruth,全媒体,不正検知,セッション異常,Medium,Layer1,○,TO-02,TO-04,preserve,,,,整合,
F10,AdTruth,全媒体,不正検知,IPアドレス集中,Medium,Layer1,○,TO-02,,preserve,,,,整合,
F11,AdTruth,全媒体,不正検知,ユーザーエージェント異常,Medium,Layer1,×,TO-02,,preserve,,,,整合,
F12,AdTruth,全媒体,不正検知,タイムスタンプ異常,Medium,Layer1,○,TO-02,TO-04,preserve,,,,整合,
F13,AdTruth,全媒体,不正検知,アフィリエイト不正,High,Layer1,○,TO-02,,preserve,,,,整合,
F14,AdTruth,全媒体,不正検知,ビューアビリティ不正,Medium,Layer1,×,TO-09,,preserve,,,,整合,
F15,AdTruth,全媒体,不正検知,予算保護アラート,Critical,Layer1,○,TO-09,TO-11,neutral,,,,整合,リアルタイムでBudget Lostを防ぐ実行レイヤー"""


def main():
    os.makedirs(RULES_DIR, exist_ok=True)
    print("v2.0 YAML ルール生成開始...")

    # Google
    google_rows = parse_csv(GOOGLE_CSV)
    google_rules = [convert_row(r) for r in google_rows]
    write_rules_yaml("Google Ads", google_rules, os.path.join(RULES_DIR, "google_rules.yaml"))

    # Meta
    meta_rows = parse_csv(META_CSV)
    meta_rules = [convert_row(r) for r in meta_rows]
    write_rules_yaml("Meta Ads", meta_rules, os.path.join(RULES_DIR, "meta_rules.yaml"))

    # TikTok
    tiktok_rows = parse_csv(TIKTOK_CSV)
    tiktok_rules = [convert_row(r) for r in tiktok_rows]
    write_rules_yaml("TikTok Ads", tiktok_rules, os.path.join(RULES_DIR, "tiktok_rules.yaml"))

    # SEO
    seo_rows = parse_csv(SEO_CSV)
    seo_rules = [convert_row(r) for r in seo_rows]
    write_rules_yaml("SEO", seo_rules, os.path.join(RULES_DIR, "seo_rules.yaml"))

    # AdTruth
    adtruth_rows = parse_csv(ADTRUTH_CSV)
    adtruth_rules = [convert_row(r) for r in adtruth_rows]
    write_rules_yaml("AdTruth", adtruth_rules, os.path.join(RULES_DIR, "adtruth_rules.yaml"))

    # Tradeoff Axes (§3 そのまま)
    axes_path = os.path.join(RULES_DIR, "tradeoff_axes.yaml")
    axes_data = {
        "version": "2.0",
        "axes": [
            {"id": "TO-01", "name": "構造の粒度", "pole_left": "細分化", "pole_right": "集約",
             "criteria": "週1,000IMP未満なら集約・以上なら細分化維持。1コンテンツ1AG原則",
             "source": "Hagakure / GORIN / デッキp7 運用パラダイムシフト"},
            {"id": "TO-02", "name": "学習シグナル", "pole_left": "ポジティブ強化", "pole_right": "ネガティブ保持",
             "criteria": "ネガティブも学習資源。安易な停止は学習を毀損する",
             "source": "Unlocking 03 機械学習 / siranui ep0"},
            {"id": "TO-03", "name": "入札次元", "pole_left": "頻度の幅", "pole_right": "深度の強さ",
             "criteria": "tCPA=深度+頻度両方制御 / tMaxConv=頻度集中で深度自由",
             "source": "siranui ep4 入札概念"},
            {"id": "TO-04", "name": "評価対象", "pole_left": "品質スコア(結果)", "pole_right": "Ad Rank(原因)",
             "criteria": "品質スコアはモニタリング指標。原因はpCTR・関連性・LP利便性",
             "source": "Unlocking 05 品質スコアの誤解"},
            {"id": "TO-05", "name": "KW運用", "pole_left": "KW追加で可視化", "pole_right": "学習データ集約",
             "criteria": "自動入札時代はKW追加=文字列レベル可視化目的のみ",
             "source": "Unlocking 03, 05"},
            {"id": "TO-06", "name": "クリエイティブ-LP", "pole_left": "広告統一", "pole_right": "LP個別最適",
             "criteria": "1AG=1LP原則。複数LPは広告カスタマイザで吸収",
             "source": "Unlocking 08 / Hagakure原典"},
            {"id": "TO-07", "name": "クリエイティブ管理", "pole_left": "負け止め", "pole_right": "学習継続",
             "criteria": "自動入札時は止めずに編集。手動入札時のみ停止検討",
             "source": "Unlocking 07"},
            {"id": "TO-08", "name": "広告フォーマット", "pole_left": "訴求網羅", "pole_right": "バリエーション幅",
             "criteria": "RSA/RDAは短い見出しでバリエーション重視",
             "source": "Unlocking 08"},
            {"id": "TO-09", "name": "IS Lost構造", "pole_left": "Budget最適化", "pole_right": "Ad Rank最適化",
             "criteria": "Budget Lost解消後にAd Rank Lostが顕在化する順序がある",
             "source": "デッキp36 / Unlocking 02"},
            {"id": "TO-10", "name": "時間軸", "pole_left": "短期効率", "pole_right": "長期学習",
             "criteria": "細分化は短期CPA改善、集約は長期学習蓄積",
             "source": "デッキp7 運用パラダイムシフト"},
            {"id": "TO-11", "name": "コントロール権", "pole_left": "人的最適化", "pole_right": "システム自動化",
             "criteria": "施策は最終的に自動化に乗せる前提で初期値を人間が設計",
             "source": "デッキ Automation Process"},
        ]
    }
    with open(axes_path, "w", encoding="utf-8") as f:
        yaml.dump(axes_data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
    print(f"  {axes_path}: 11 axes")

    print("\n完了! 全ファイル生成成功")


if __name__ == "__main__":
    main()
