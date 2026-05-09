"""ChatWork Jinja2 テンプレート レンダリング単体テスト (ADR-005 / Day 1)

pilotton 真値（CV 156 / CPA ¥9,251、Day 5.2 計測）を投入してレンダリング検証。

4 ケース:
1. daily_indication: M02 (CAPI未実装) 1 件指摘でレンダリング、想定効果と手順が出力される
2. daily_indication: 複数件 (3件) のセパレータ表示が正しい
3. completion_notice: M02 解消通知、before/after 状態 + 達成効果が出力される
4. monthly_report: 月次サマリ + 添付PDF表記が出力される
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# pilotton 真値（Day 5.2 / 直近30日）
PILOTTON_REAL_CV = 156
PILOTTON_REAL_CPA = "¥9,251"
PILOTTON_REAL_SPEND = "¥1,443,150"


@pytest.fixture
def chatwork_render():
    from templates.chatwork import render
    return render


class TestDailyIndication:
    """daily_indication.md.j2 レンダリング"""

    def test_single_indication_m02(self, chatwork_render):
        """ケース1: M02 (CAPI未実装) 1件指摘 — 改善手順がマクロから本文展開される"""
        ctx = {
            "client_display_name": "株式会社パイロットン",
            "date": "2026-05-04",
            "greeting": "お世話になっております。本日の運用監査結果をお送りします。",
            "indications": [
                {
                    "title": "CAPI（Conversion API）未実装",
                    "rule_id": "M02",
                    "severity_label": "重要度高",
                    "fact": (
                        f"直近30日の Pixel 計測 CV は {PILOTTON_REAL_CV} 件、"
                        f"CPA {PILOTTON_REAL_CPA}（消化額 {PILOTTON_REAL_SPEND}）。"
                        "iOS14.5+ の SKAN 影響で計測欠損が推定 30-40% 発生中。"
                    ),
                    "impact": "CV 計測欠損により学習シグナルが減衰、CPA 改善余地が顕在化していない。",
                    "scenario_label": "現実シナリオ",
                    "expected_effect": "月次 CPA -8% (¥9,251 → ¥8,510 程度)",
                    "payload": {},
                }
            ],
            "footer_note": None,
        }
        out = chatwork_render("daily_indication.md.j2", ctx)
        assert "[info]" in out and "[/info]" in out
        assert "株式会社パイロットン" in out
        assert "M02" in out
        assert "CAPI" in out
        assert "重要度高" in out
        assert PILOTTON_REAL_CPA in out
        # 抽象度の高い「目的+ゴール状態」フォーマットで展開
        assert "【目的】" in out
        assert "【ゴール状態】" in out
        # 5 つの実装方法が選択肢として併記される
        assert "One-Click" in out  # 方法A
        assert "ECフォース" in out  # 方法B (pilotton 想定、2026-05-04 Shopify→ECフォース 切替)
        assert "Server-side GTM" in out  # 方法D
        # 外部リンク非依存
        assert "参考資料" not in out
        assert "https://" not in out
        # 免責文 (層2) が末尾に挿入される
        assert "本手順は" in out and "時点の情報です" in out
        assert "ChatGPT" in out and "Claude" in out and "Gemini" in out
        # 1件のみなのでセパレータの「1/1」表記
        assert "1/1" in out

    def test_multiple_indications(self, chatwork_render):
        """ケース2: 3件の連続指摘でセパレータ表示 + 各指摘末尾に免責文"""
        ctx = {
            "client_display_name": "株式会社パイロットン",
            "date": "2026-05-04",
            "greeting": None,
            "indications": [
                {
                    "title": f"指摘{i}",
                    "rule_id": f"UNKNOWN_{i}",  # フォールバック発火
                    "severity_label": "重要度高",
                    "fact": f"事実{i}",
                    "impact": f"影響{i}",
                    "expected_effect": None,
                    "payload": {},
                }
                for i in range(1, 4)
            ],
            "footer_note": "本指摘は内部レビュー済みです。",
        }
        out = chatwork_render("daily_indication.md.j2", ctx)
        for i in range(1, 4):
            assert f"UNKNOWN_{i}" in out
            assert f"事実{i}" in out
        assert "1/3" in out and "3/3" in out
        assert "本指摘は内部レビュー済みです。" in out
        # 未知 rule_id はフォールバック表示
        assert out.count("弊社までご返信ください") >= 3
        # 免責文も各指摘ごとに 1 回出る (3 件 = 3 回)
        assert out.count("時点の情報です") == 3


class TestCompletionNotice:
    """completion_notice.md.j2 レンダリング"""

    def test_m02_resolved(self, chatwork_render):
        """ケース3: M02 解消通知 + before/after 効果"""
        ctx = {
            "client_display_name": "株式会社パイロットン",
            "date": "2026-05-20",
            "completions": [
                {
                    "title": "CAPI 実装完了による計測精度回復",
                    "rule_id": "M02",
                    "first_reported_at": "2026-05-04",
                    "resolved_at": "2026-05-20",
                    "before_state": (
                        f"Pixel のみで計測、直近30日 CV {PILOTTON_REAL_CV} 件 / CPA {PILOTTON_REAL_CPA}。"
                        " EMQ スコア 4.2 で SKAN 計測欠損が顕在化。"
                    ),
                    "after_state": (
                        "CAPI Gateway 有効化、purchase / lead 両イベントを GTM ssgtm 経由で転送。"
                        " EMQ 7.8 達成、Pixel + CAPI dedup 動作確認済み。"
                    ),
                    "consecutive_clean_days": 3,
                    "achieved_effect": {
                        "minimum": "¥-58,000 / 月（保守）",
                        "realistic": "¥-115,500 / 月（現実）",
                        "optimistic": "¥-173,000 / 月（上限）",
                    },
                    "note": "CV 重複排除は conversion_mapping.yaml により自動化。",
                }
            ],
        }
        out = chatwork_render("completion_notice.md.j2", ctx)
        assert "[info]" in out
        assert "M02" in out
        assert "2026-05-04" in out and "2026-05-20" in out
        assert "Pixel のみで計測" in out
        assert "CAPI Gateway 有効化" in out
        assert "3 日連続でクリーン" in out
        assert "¥-115,500" in out
        assert "conversion_mapping.yaml" in out

    def test_anomaly_continued_issue_followup(self, chatwork_render):
        """急変アラート終了後も水準が戻らない場合は YAML 仮説を表示"""
        ctx = {
            "client_display_name": "株式会社パイロットン",
            "date": "2026-05-09",
            "completions": [
                {
                    "title": "[Meta] CPA +76.3% 上昇 (¥5,494 → ¥9,686)",
                    "rule_id": "ANO_CPA_SPIKE",
                    "first_reported_at": "2026-05-07",
                    "resolved_at": "2026-05-09",
                    "before_state": "[Meta] CPA +76.3% 上昇 (¥5,494 → ¥9,686)",
                    "after_state": "急変条件は3日連続で再発していません。ただし水準が戻ったとは限らないため、下記の継続課題を確認します。",
                    "consecutive_clean_days": 3,
                    "is_continued_issue": True,
                    "followup": {
                        "type": "continued_issue",
                        "summary": "急変アラートは終了しましたが、CPA水準は悪化後の状態が残っています。",
                        "account_metric": {"metric": "cpa", "baseline": 5494, "latest": 9458, "change_pct": 72.2},
                        "campaign_metrics": [
                            {
                                "campaign": "MYNAILPLEX_配信_新",
                                "baseline_cpa": 4120,
                                "latest_cpa": 9387,
                                "cpa_change_pct": 127.9,
                                "baseline_impressions": 866756,
                                "latest_impressions": 368544,
                                "impression_change_pct": -57.5,
                            }
                        ],
                        "hypotheses": [
                            {
                                "rule_id": "M68",
                                "rule_name": "学習リセット要因イベント検出",
                                "hypothesis": "予算・ターゲット変更で学習が再起動した可能性。",
                                "evidence": "CPA悪化と配信量低下が同時に出ています。",
                                "next_action": "変更履歴を確認してください。",
                            }
                        ],
                        "customer_question": "5/5前後に変更はありましたか?",
                        "answer_options": {
                            "A": "予算・入札・ターゲットを変更した",
                            "B": "広告素材・LP・CVイベントを変更した",
                            "C": "特に変更していない / 不明",
                        },
                    },
                }
            ],
        }
        out = chatwork_render("completion_notice.md.j2", ctx)
        assert "急変アラート終了 / 継続確認" in out
        assert "継続課題" in out
        assert "MYNAILPLEX_配信_新" in out
        assert "M68 学習リセット要因イベント検出" in out
        assert "[A] 予算・入札・ターゲットを変更した" in out
        assert "達成効果（月次換算）" not in out


class TestMonthlyReport:
    """monthly_report.md.j2 レンダリング"""

    def test_pilotton_monthly(self, chatwork_render):
        """ケース4: pilotton 月次レポート + 添付PDF表記"""
        ctx = {
            "client_display_name": "株式会社パイロットン",
            "period_label": "2026-05",
            "period_start": "2026-05-01",
            "period_end": "2026-05-31",
            "generated_at": "2026-06-01 09:00 JST",
            "summary": {
                "indications_new": 5,
                "completions": 3,
                "in_progress": 2,
                "coverage_label": "Top5 中 4 件カバー（80%）",
            },
            "effect": {
                "minimum": "¥-180,000",
                "realistic": "¥-360,000",
                "optimistic": "¥-540,000",
                "formula_note": "※確実値は pixel_health 連動係数 (×0.1〜0.2) 適用済み",
            },
            "completions_breakdown": [
                {
                    "rule_id": "M02",
                    "title": "CAPI 実装完了",
                    "severity_label": "高",
                    "effect_label": "¥-115,500 / 月",
                },
                {
                    "rule_id": "M09",
                    "title": "Domain Verification 完了",
                    "severity_label": "高",
                    "effect_label": "¥-92,000 / 月",
                },
                {
                    "rule_id": "M03",
                    "title": "EMQ スコア改善 (4.2 → 7.8)",
                    "severity_label": "中",
                    "effect_label": "¥-152,500 / 月",
                },
            ],
            "open_indications": [
                {
                    "rule_id": "M61",
                    "title": "AEM 優先度設定",
                    "days_open": 12,
                    "severity_label": "中",
                },
                {
                    "rule_id": "M04",
                    "title": "Customer File アップロード",
                    "days_open": 8,
                    "severity_label": "中",
                },
            ],
            "next_focus": [
                "AEM 優先度設定の完了（M61）— 来週中",
                "AdTruth タグ実装による不正排除（X01）",
            ],
            "attached_pdf": "pilotton_monthly_2026-05.pdf",
        }
        out = chatwork_render("monthly_report.md.j2", ctx)
        assert "[info]" in out
        assert "株式会社パイロットン" in out
        assert "2026-05" in out
        assert "新規指摘：5 件" in out
        assert "解消完了：3 件" in out
        assert "Top5 中 4 件カバー（80%）" in out
        assert "¥-360,000" in out
        assert "pixel_health 連動係数" in out
        assert "M02" in out and "M09" in out and "M03" in out
        assert "M61" in out and "12 日" in out
        assert "AEM 優先度設定の完了" in out
        assert "pilotton_monthly_2026-05.pdf" in out


class TestStrictUndefined:
    """未定義変数アクセス時に Jinja2 が早期エラーになるか"""

    def test_missing_required_field_raises(self, chatwork_render):
        from jinja2 import UndefinedError

        # client_display_name を欠落
        ctx = {
            "date": "2026-05-04",
            "indications": [],
            "greeting": None,
            "footer_note": None,
        }
        with pytest.raises(UndefinedError):
            chatwork_render("daily_indication.md.j2", ctx)


# ============================================================
# G タスク: rule_id 別の改善手順マクロ展開
# ============================================================

def _make_indication(rule_id, payload=None):
    return {
        "client_display_name": "[テスト] 株式会社パイロットン",
        "date": "2026-05-04",
        "greeting": None,
        "indications": [{
            "title": "テスト指摘",
            "rule_id": rule_id,
            "severity_label": "重要度高",
            "fact": "テスト事実",
            "impact": "テスト影響",
            "expected_effect": None,
            "payload": payload or {},
        }],
        "footer_note": None,
    }


class TestRuleIdActionStepsMacro:
    """rule_id 別の改善手順がマクロから本文展開されること (層1: 抽象度↑、層3: WebSearch 反映)"""

    # ----- 1. CAPI -----
    def test_capi_missing_canonical_id(self, chatwork_render):
        out = chatwork_render(
            "daily_indication.md.j2",
            _make_indication("DQ-CAPI-MISSING"),
        )
        assert "【目的】" in out
        assert "【ゴール状態】" in out
        # 5 つの実装方法が選択肢として併記される
        assert "One-Click" in out         # 方法A (2026/4 リリース、層3 反映)
        assert "ECフォース" in out        # 方法B (pilotton 想定)
        assert "CAPI Gateway" in out      # 方法C
        assert "Server-side GTM" in out   # 方法D
        assert "graph.facebook.com" in out  # 方法E (URL ではなくエンドポイント記述)
        # 動作確認 + 効果指標
        assert "テストイベント" in out
        assert "EMQ スコア" in out
        assert "17.8%" in out  # 層3 で取り込んだ Meta 公式数値

    def test_capi_legacy_alias_m02(self, chatwork_render):
        out = chatwork_render("daily_indication.md.j2", _make_indication("M02"))
        assert "【目的】" in out
        assert "ECフォース" in out
        assert "Server-side GTM" in out

    # ----- 2. Pixel 休眠 -----
    def test_pixel_dormant_with_days_payload(self, chatwork_render):
        out = chatwork_render(
            "daily_indication.md.j2",
            _make_indication("PIXEL-DORMANT", {"dormant_days": 312}),
        )
        assert "直近 312 日間" in out
        assert "Pixel イベント受信なし" in out
        # 削除前のバックアップ推奨 (ユーザ要件)
        assert "バックアップ" in out
        # Meta は Pixel 完全削除を提供しないという 2026 仕様を明示
        assert "完全削除" in out
        # 統合判断基準
        assert "判断基準" in out

    def test_pixel_dormant_legacy_m01(self, chatwork_render):
        out = chatwork_render("daily_indication.md.j2", _make_indication("M01"))
        assert "【目的】" in out
        assert "メイン Pixel" in out

    # ----- 3. ドメイン認証 -----
    def test_domain_verification_default_dns(self, chatwork_render):
        out = chatwork_render(
            "daily_indication.md.j2",
            _make_indication("DOMAIN-NOT-VERIFIED"),
        )
        # 3 方式の使い分け基準が明示
        assert "DNS TXT レコード" in out
        assert "HTML ファイル" in out
        assert "メタタグ" in out
        assert "使い分け基準" in out
        # AEM (M61) との連動に言及
        assert "AEM" in out
        # facebook-domain-verification の実物値を含む
        assert "facebook-domain-verification" in out

    def test_domain_verification_html_method(self, chatwork_render):
        out = chatwork_render(
            "daily_indication.md.j2",
            _make_indication("DOMAIN-NOT-VERIFIED", {"recommended_method": "html_file"}),
        )
        assert "HTML ファイル設置" in out
        assert "ドキュメントルート" in out

    def test_domain_verification_meta_tag_method(self, chatwork_render):
        out = chatwork_render(
            "daily_indication.md.j2",
            _make_indication("DOMAIN-NOT-VERIFIED", {"recommended_method": "meta_tag"}),
        )
        assert "meta タグ埋込" in out
        assert "<meta name" in out

    # ----- 4. AEM (層3 で 2025/6 仕様変更を反映) -----
    def test_aem_reflects_2025_spec_change(self, chatwork_render):
        out = chatwork_render(
            "daily_indication.md.j2",
            _make_indication("AEM-NOT-CONFIGURED"),
        )
        # 仕様変更注記が冒頭にある
        assert "仕様変更" in out
        assert "2025 年 6 月" in out
        assert "8 イベント枠" in out and "撤廃" in out
        # 新仕様の実施事項
        assert "value 最適化" in out
        assert "SKAdNetwork" in out or "SKAN" in out
        # ドメイン認証 (M09) との依存関係
        assert "M09" in out

    def test_aem_legacy_m61(self, chatwork_render):
        out = chatwork_render("daily_indication.md.j2", _make_indication("M61"))
        assert "value 最適化" in out
        assert "Purchase" in out

    # ----- 5. 1st パーティデータ -----
    def test_first_party_data_steps(self, chatwork_render):
        out = chatwork_render(
            "daily_indication.md.j2",
            _make_indication("FIRST-PARTY-DATA-MISSING"),
        )
        assert "Customer File 形式仕様" in out
        # SHA256 + no-salt の Meta 仕様明記
        assert "SHA256" in out and "no salt" in out
        # マッチ率の目安が 3 段階
        assert "30%" in out
        assert "Lookalike" in out
        # GDPR / 個情法対応
        assert "GDPR" in out

    def test_first_party_legacy_m04(self, chatwork_render):
        out = chatwork_render("daily_indication.md.j2", _make_indication("M04"))
        assert "Customer File" in out

    # ----- フォールバック -----
    def test_unknown_rule_fallback(self, chatwork_render):
        out = chatwork_render(
            "daily_indication.md.j2",
            _make_indication("X99-UNKNOWN-RULE"),
        )
        assert "X99-UNKNOWN-RULE" in out
        assert "弊社" in out
        assert "ご返信ください" in out

    # ----- 共通: 外部リンク非依存 -----
    def test_no_external_links_anywhere(self, chatwork_render):
        """方針: 外部リンク依存ゼロ。全 5 主要 rule_id で http(s):// URL が出力されない

        注: graph.facebook.com 等は API エンドポイント記述として許可
            (ホスト名のみで http:// プレフィクスなし)
        """
        for rid in (
            "DQ-CAPI-MISSING", "PIXEL-DORMANT", "DOMAIN-NOT-VERIFIED",
            "AEM-NOT-CONFIGURED", "FIRST-PARTY-DATA-MISSING",
        ):
            out = chatwork_render("daily_indication.md.j2", _make_indication(rid))
            assert "https://" not in out, f"{rid}: 本文に URL が含まれてはならない"
            assert "http://" not in out, f"{rid}: 本文に URL が含まれてはならない"

    def test_payload_optional_in_template(self, chatwork_render):
        """payload キー自体が無くても StrictUndefined にぶつからない"""
        ctx = {
            "client_display_name": "テスト",
            "date": "2026-05-04",
            "greeting": None,
            "indications": [{
                "title": "t",
                "rule_id": "M02",
                "severity_label": "高",
                "fact": "f",
                "impact": "i",
                "expected_effect": None,
                # payload キー無し
            }],
            "footer_note": None,
        }
        out = chatwork_render("daily_indication.md.j2", ctx)
        assert "ECフォース" in out


# ============================================================
# G タスク 層2: 免責文 + 生成 AI 質問導線
# ============================================================

class TestDisclaimerAiAssist:
    """_disclaimer_ai_assist.md.j2 の動作確認"""

    def test_disclaimer_present_in_every_indication(self, chatwork_render):
        """全 5 主要 rule_id で免責文が末尾に展開される"""
        for rid in (
            "DQ-CAPI-MISSING", "PIXEL-DORMANT", "DOMAIN-NOT-VERIFIED",
            "AEM-NOT-CONFIGURED", "FIRST-PARTY-DATA-MISSING",
        ):
            out = chatwork_render("daily_indication.md.j2", _make_indication(rid))
            assert "本手順は" in out
            assert "時点の情報です" in out
            assert "ChatGPT" in out
            assert "Claude" in out
            assert "Gemini" in out
            assert "スクリーンショット" in out

    def test_disclaimer_present_for_unknown_rule_id(self, chatwork_render):
        """未定義 rule_id (フォールバック) でも免責文は出る"""
        out = chatwork_render("daily_indication.md.j2", _make_indication("UNKNOWN_X"))
        assert "ChatGPT" in out

    def test_disclaimer_year_month_auto_injected(self, chatwork_render):
        """current_year / current_month が globals から自動展開される"""
        from datetime import datetime
        now = datetime.now()
        out = chatwork_render("daily_indication.md.j2", _make_indication("M02"))
        assert f"{now.year}年" in out
        assert f"{now.month}月" in out

    def test_disclaimer_year_month_can_be_overridden(self, chatwork_render):
        """context で current_year / current_month を上書き可能"""
        ctx = _make_indication("M02")
        ctx["current_year"] = 2099
        ctx["current_month"] = 12
        out = chatwork_render("daily_indication.md.j2", ctx)
        assert "2099年12月" in out

    def test_disclaimer_count_matches_indication_count(self, chatwork_render):
        """免責文は indication の件数だけ繰り返される (1 件指摘 → 1 回、3 件 → 3 回)"""
        ctx_1 = _make_indication("M02")
        out_1 = chatwork_render("daily_indication.md.j2", ctx_1)
        assert out_1.count("時点の情報です") == 1

        ctx_3 = {
            "client_display_name": "テスト",
            "date": "2026-05-04",
            "greeting": None,
            "indications": [
                {"title": f"t{i}", "rule_id": "M02", "severity_label": "高",
                 "fact": "f", "impact": "i", "expected_effect": None, "payload": {}}
                for i in range(3)
            ],
            "footer_note": None,
        }
        out_3 = chatwork_render("daily_indication.md.j2", ctx_3)
        assert out_3.count("時点の情報です") == 3
