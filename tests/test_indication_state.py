"""Indication State / Filter / Detector のテスト (ADR-005 / Day 2 C5)

6 ケース（要件）:
1. 状態遷移: open → resolved_pending → resolved_confirmed の一連フロー
2. 一時的データ欠損で clean カウントが進まない（C4 ガード）
3. 同日多重 mark_clean は 1 日 1 カウント
4. cooldown 7 日: resolved_confirmed から 7 日未満は再通知されない
5. severity フィルタ + 日次 3 件抑制
6. 解消 → 再発で別 indication_id（履歴分離）

追加:
7. アーカイブ機能（resolved_confirmed → archived）
8. detector: ads_audit / anomaly / fraud_audit / cv_quality 統合
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture
def state(tmp_path):
    from engine.indication_state import IndicationState
    return IndicationState(client_id="pilotton", state_dir=str(tmp_path))


# ============================================================
# C1: 状態遷移
# ============================================================

class TestStatusTransition:
    """ケース1: open → resolved_pending → resolved_confirmed"""

    def test_full_flow(self, state):
        from engine import indication_state as st

        # Day 1: 検知
        rec = state.upsert_detected(
            rule_id="M02", platform="meta", target_id="account_566",
            severity="high", payload={"title": "CAPI 未実装"},
            today="2026-05-04",
        )
        assert rec["status"] == st.STATUS_OPEN
        assert rec["consecutive_clean_days"] == 0
        assert rec["first_detected_date"] == "2026-05-04"
        ind_id = rec["indication_id"]
        assert ind_id == "pilotton:M02:meta:account_566:2026-05-04"

        # Day 2: clean=1 → resolved_pending
        r2 = state.mark_clean(ind_id, today="2026-05-05")
        assert r2["status"] == st.STATUS_RESOLVED_PENDING
        assert r2["consecutive_clean_days"] == 1

        # Day 3: clean=2 → まだ resolved_pending
        r3 = state.mark_clean(ind_id, today="2026-05-06")
        assert r3["status"] == st.STATUS_RESOLVED_PENDING
        assert r3["consecutive_clean_days"] == 2

        # Day 4: clean=3 → resolved_confirmed
        r4 = state.mark_clean(ind_id, today="2026-05-07")
        assert r4["status"] == st.STATUS_RESOLVED_CONFIRMED
        assert r4["consecutive_clean_days"] == 3
        assert r4["resolved_at"] is not None

        # 履歴に detected / clean*3 / resolved_confirmed が含まれる
        events = [h["event"] for h in r4["history"]]
        assert "detected" in events
        assert events.count("clean") == 3
        assert "resolved_confirmed" in events


# ============================================================
# C2: 一時的データ欠損ガード
# ============================================================

class TestDataUnavailableGuard:
    """ケース2: data_available=False では clean カウントが進まない"""

    def test_data_unavailable_does_not_advance(self, state):
        from engine import indication_state as st

        rec = state.upsert_detected(
            rule_id="M02", platform="meta", target_id="account_566",
            severity="high", payload={}, today="2026-05-04",
        )
        ind_id = rec["indication_id"]

        # Day 2: clean=1
        state.mark_clean(ind_id, today="2026-05-05")
        # Day 3: データ欠損 → カウント据え置き
        r3 = state.mark_clean(ind_id, today="2026-05-06", data_available=False)
        assert r3["consecutive_clean_days"] == 1
        assert r3["status"] == st.STATUS_RESOLVED_PENDING
        # Day 4: データ復活 → clean=2
        r4 = state.mark_clean(ind_id, today="2026-05-07")
        assert r4["consecutive_clean_days"] == 2
        # Day 5: clean=3 → confirmed
        r5 = state.mark_clean(ind_id, today="2026-05-08")
        assert r5["status"] == st.STATUS_RESOLVED_CONFIRMED

        # 履歴に data_unavailable イベントが残る
        events = [h["event"] for h in r5["history"]]
        assert "data_unavailable" in events


# ============================================================
# C3: 同日多重 mark_clean は1日1カウント
# ============================================================

class TestSameDayCleanIdempotent:
    """ケース3: 同日に複数回 mark_clean しても 1 日 1 カウント"""

    def test_same_day_multiple_calls(self, state):
        rec = state.upsert_detected(
            rule_id="M02", platform="meta", target_id="account_566",
            severity="high", payload={}, today="2026-05-04",
        )
        ind_id = rec["indication_id"]

        # 同じ日に3回呼んでも consecutive=1
        state.mark_clean(ind_id, today="2026-05-05")
        state.mark_clean(ind_id, today="2026-05-05")
        r3 = state.mark_clean(ind_id, today="2026-05-05")
        assert r3["consecutive_clean_days"] == 1


# ============================================================
# C4: cooldown 7日
# ============================================================

class TestCooldown:
    """ケース4: 同 (rule, platform, target) で resolved_confirmed から7日未満は再通知されない"""

    def test_cooldown_blocks_re_notify(self, state):
        from engine.indication_filter import filter_indications
        from engine import indication_state as st

        # 1. 検知 → 3日 clean → resolved_confirmed
        r1 = state.upsert_detected(
            rule_id="M02", platform="meta", target_id="account_566",
            severity="high", payload={}, today="2026-05-04",
        )
        state.mark_indication_notified(r1["indication_id"], today="2026-05-04")
        state.mark_clean(r1["indication_id"], today="2026-05-05")
        state.mark_clean(r1["indication_id"], today="2026-05-06")
        r1_done = state.mark_clean(r1["indication_id"], today="2026-05-07")
        assert r1_done["status"] == st.STATUS_RESOLVED_CONFIRMED

        # 2. 5日後に同事象が再発 → 別 indication_id だが cooldown 中
        r2 = state.upsert_detected(
            rule_id="M02", platform="meta", target_id="account_566",
            severity="high", payload={}, today="2026-05-12",  # 5日後
        )
        assert r2["indication_id"] != r1["indication_id"]

        notify = filter_indications([r2], state, today="2026-05-12", cooldown_days=7)
        assert notify == []  # cooldown でブロック

        # 3. 8日後（cooldown 明け）なら通知される
        r3 = state.upsert_detected(
            rule_id="M02", platform="meta", target_id="account_566",
            severity="high", payload={}, today="2026-05-15",  # 8日後
        )
        notify3 = filter_indications([r3], state, today="2026-05-15", cooldown_days=7)
        assert len(notify3) == 1
        assert notify3[0]["indication_id"] == r3["indication_id"]


# ============================================================
# C5: severity フィルタ + 日次3件抑制
# ============================================================

class TestSeverityAndDailyCap:
    """ケース5: severity 上限 + 日次 cap"""

    def test_severity_filter_excludes_medium(self, state):
        from engine.indication_filter import filter_indications

        recs = [
            state.upsert_detected(
                rule_id="M01", platform="meta", target_id="cp1",
                severity="critical", payload={}, today="2026-05-04",
            ),
            state.upsert_detected(
                rule_id="M02", platform="meta", target_id="cp2",
                severity="medium", payload={}, today="2026-05-04",
            ),
            state.upsert_detected(
                rule_id="M03", platform="meta", target_id="cp3",
                severity="low", payload={}, today="2026-05-04",
            ),
        ]
        out = filter_indications(recs, state, today="2026-05-04")
        assert len(out) == 1
        assert out[0]["rule_id"] == "M01"

    def test_daily_cap_3(self, state):
        from engine.indication_filter import filter_indications

        # 5 件 high を投入 → cap=3 で 3 件のみ
        recs = [
            state.upsert_detected(
                rule_id=f"M0{i}", platform="meta", target_id=f"cp{i}",
                severity="high", payload={}, today="2026-05-04",
            )
            for i in range(1, 6)
        ]
        out = filter_indications(recs, state, today="2026-05-04", daily_cap=3)
        assert len(out) == 3
        # critical 優先 → 全部 high なので first_detected_at 順（先勝ち）
        assert {r["rule_id"] for r in out} == {"M01", "M02", "M03"}

    def test_critical_priority_over_high(self, state):
        from engine.indication_filter import filter_indications

        # high を3件先に検知、その後 critical を1件 → critical が優先される
        for i in range(1, 4):
            state.upsert_detected(
                rule_id=f"H{i}", platform="meta", target_id=f"cp{i}",
                severity="high", payload={}, today="2026-05-04",
            )
        crit = state.upsert_detected(
            rule_id="C1", platform="meta", target_id="cpC",
            severity="critical", payload={}, today="2026-05-04",
        )
        all_recs = state.list_open_or_pending()
        out = filter_indications(all_recs, state, today="2026-05-04", daily_cap=3)
        assert len(out) == 3
        assert out[0]["severity"] == "critical"
        assert out[0]["rule_id"] == "C1"

    def test_already_notified_excluded_from_cap(self, state):
        from engine.indication_filter import filter_indications

        # 1 件すでに通知済み → cap=3 のうち残り2件分のみ枠が残る
        already = state.upsert_detected(
            rule_id="M01", platform="meta", target_id="cp1",
            severity="high", payload={}, today="2026-05-04",
        )
        state.mark_indication_notified(already["indication_id"], today="2026-05-04")

        new_recs = [
            state.upsert_detected(
                rule_id=f"M0{i}", platform="meta", target_id=f"cp{i}",
                severity="high", payload={}, today="2026-05-04",
            )
            for i in range(2, 6)
        ]
        out = filter_indications(new_recs, state, today="2026-05-04", daily_cap=3)
        # 既通知1件 + 今回2件 = cap 3
        assert len(out) == 2


# ============================================================
# C6: 解消 → 再発で別 indication_id
# ============================================================

class TestResolveAndRecur:
    """ケース6: resolved_confirmed 後に再検知すると別 indication_id"""

    def test_recurrence_gets_new_id(self, state):
        from engine import indication_state as st

        r1 = state.upsert_detected(
            rule_id="M02", platform="meta", target_id="account_566",
            severity="high", payload={}, today="2026-05-04",
        )
        state.mark_clean(r1["indication_id"], today="2026-05-05")
        state.mark_clean(r1["indication_id"], today="2026-05-06")
        r1_done = state.mark_clean(r1["indication_id"], today="2026-05-07")
        assert r1_done["status"] == st.STATUS_RESOLVED_CONFIRMED

        # 再発（同 rule/platform/target、別日）→ 別 id
        r2 = state.upsert_detected(
            rule_id="M02", platform="meta", target_id="account_566",
            severity="high", payload={}, today="2026-05-20",
        )
        assert r2["indication_id"] != r1["indication_id"]
        assert r2["first_detected_date"] == "2026-05-20"
        assert r2["status"] == st.STATUS_OPEN
        # 元レコードは無事 resolved_confirmed のまま
        assert state.get(r1["indication_id"])["status"] == st.STATUS_RESOLVED_CONFIRMED

    def test_pending_regression_back_to_open(self, state):
        """resolved_pending 中に再検知 → open に巻き戻る (clean カウントリセット)"""
        from engine import indication_state as st

        r = state.upsert_detected(
            rule_id="M02", platform="meta", target_id="cp1",
            severity="high", payload={}, today="2026-05-04",
        )
        ind_id = r["indication_id"]
        state.mark_clean(ind_id, today="2026-05-05")  # clean=1
        state.mark_clean(ind_id, today="2026-05-06")  # clean=2 (pending)

        # 再検知 → open に戻り clean カウントリセット
        r2 = state.upsert_detected(
            rule_id="M02", platform="meta", target_id="cp1",
            severity="high", payload={}, today="2026-05-07",
        )
        assert r2["indication_id"] == ind_id
        assert r2["status"] == st.STATUS_OPEN
        assert r2["consecutive_clean_days"] == 0
        events = [h["event"] for h in r2["history"]]
        assert "regressed" in events


# ============================================================
# 追加: アーカイブ機能
# ============================================================

class TestArchive:
    def test_archive_resolved(self, state, tmp_path):
        from engine import indication_state as st

        r = state.upsert_detected(
            rule_id="M02", platform="meta", target_id="cp1",
            severity="high", payload={}, today="2026-05-04",
        )
        for d in ("2026-05-05", "2026-05-06", "2026-05-07"):
            state.mark_clean(r["indication_id"], today=d)

        count = state.archive_resolved(archive_month="2026-05")
        assert count == 1

        # アクティブ DB から消えている
        assert state.get(r["indication_id"]) is None

        # アーカイブファイルに保存されている
        archive_path = os.path.join(
            str(tmp_path), "pilotton_indications.archive", "2026-05.json"
        )
        assert os.path.exists(archive_path)
        with open(archive_path) as f:
            archived = json.load(f)
        assert len(archived) == 1
        assert archived[0]["rule_id"] == "M02"
        assert archived[0]["status"] == st.STATUS_ARCHIVED


# ============================================================
# 追加: detector 統合
# ============================================================

class TestDetectorIntegration:
    def test_collect_from_all_analyzers(self):
        from engine.indication_detector import collect_candidates

        audit_results = {
            "ads_audit": {
                "issues": [
                    {"id": "M02", "severity": "high", "platform": "meta",
                     "message": "CAPI 未実装", "campaign": "MAIN_CP"},
                    {"id": "M99", "severity": "low", "platform": "meta",
                     "message": "suppress test", "suppressed": True},
                ]
            },
            "anomalies": {
                "alerts": [
                    {"severity": "warning", "metric": "cpa", "platform": "meta",
                     "message": "CPA 急騰", "cause": "予算超過", "action": "予算確認"},
                ]
            },
            "fraud_audit": {
                "issues": [
                    {"check_id": "F01", "severity": "critical", "platform": "google",
                     "message": "不正流入検出"},
                ]
            },
            "cv_quality": {
                "total_cvs": 100, "fake_cvs": 60, "real_cvs": 30, "uncertain_cvs": 10,
            },
        }
        cands = collect_candidates(audit_results)
        rule_ids = {c["rule_id"] for c in cands}
        assert "M02" in rule_ids
        assert "ANO_CPA" in rule_ids
        assert "F01" in rule_ids
        assert "CV_QUALITY_FAKE_RATE" in rule_ids
        # suppressed は除外
        assert "M99" not in rule_ids
        # warning は high に正規化
        ano = next(c for c in cands if c["rule_id"] == "ANO_CPA")
        assert ano["severity"] == "high"
        # fake_rate 60% → critical
        cvq = next(c for c in cands if c["rule_id"] == "CV_QUALITY_FAKE_RATE")
        assert cvq["severity"] == "critical"

    def test_detect_and_upsert_returns_clean_candidates(self, state):
        """前日存在した指摘が今回検知されない → clean_candidates に含まれる"""
        from engine.indication_detector import detect_and_upsert, reconcile_clean
        from engine import indication_state as st

        # 1日目: M02 検知
        day1 = {
            "ads_audit": {
                "issues": [
                    {"id": "M02", "severity": "high", "platform": "meta",
                     "message": "CAPI 未実装", "campaign": "MAIN_CP"},
                ]
            }
        }
        upserted_d1, clean_d1 = detect_and_upsert(day1, state, today="2026-05-04")
        assert len(upserted_d1) == 1
        assert len(clean_d1) == 0  # 前日無し

        # 2日目: M02 検知されない → clean 候補に含まれる
        day2 = {"ads_audit": {"issues": []}}
        upserted_d2, clean_d2 = detect_and_upsert(day2, state, today="2026-05-05")
        assert len(upserted_d2) == 0
        assert len(clean_d2) == 1
        assert clean_d2[0]["rule_id"] == "M02"

        # reconcile_clean で実際に mark_clean
        applied = reconcile_clean(clean_d2, state, today="2026-05-05")
        assert applied[0]["status"] == st.STATUS_RESOLVED_PENDING


# ============================================================
# persist 確認: save → 別インスタンスから load
# ============================================================

class TestPersistence:
    def test_save_and_reload(self, tmp_path):
        from engine.indication_state import IndicationState

        s1 = IndicationState("pilotton", state_dir=str(tmp_path))
        rec = s1.upsert_detected(
            rule_id="M02", platform="meta", target_id="cp1",
            severity="high", payload={"k": "v"}, today="2026-05-04",
        )
        s1.save()

        s2 = IndicationState("pilotton", state_dir=str(tmp_path))
        loaded = s2.get(rec["indication_id"])
        assert loaded is not None
        assert loaded["rule_id"] == "M02"
        assert loaded["payload"]["k"] == "v"
