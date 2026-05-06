"""auto_proposal_engine の結果カウント分離テスト (5/8 改修)

run_auto_proposal() の戻り値が sent / skipped / dry_run / failed を
正しく区別すること、および history 更新が「本番モードかつ実送信成功時のみ」
発生することを検証する。

カバー対象:
- dry_run=True: dry_run_count == attempted_count、sent_count=0、history 不変
- skipped (idempotency hit): skipped_count++、sent_count=0、history 不変
- 実送信成功 (message_id): sent_count++、posted_count == sent_count、history 更新
- 例外: failed_count++、attempted_count に含まれる
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest import mock

import pytest
import yaml

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture
def isolated_history_dir(tmp_path, monkeypatch):
    """outputs/auto_proposal_history を tmp に差し替えて履歴汚染を回避"""
    monkeypatch.setattr("engine.auto_proposal_engine.HISTORY_DIR", tmp_path / "history")
    return tmp_path / "history"


@pytest.fixture
def isolated_client_state_dir(tmp_path, monkeypatch):
    """outputs/client_state を tmp に差し替え"""
    monkeypatch.setattr("engine.auto_proposal_engine.CLIENT_STATE_DIR", tmp_path / "client_state")
    return tmp_path / "client_state"


@pytest.fixture
def stub_eligible_rules(monkeypatch):
    """_load_all_layers / _filter_by_environment / _check_prerequisite_chain /
    _check_cooldown / _enforce_caps / _apply_severity_priority を stub に差し替え、
    特定の eligible rules を出力する形で run_auto_proposal の挙動を制御
    """
    sample_rules = [
        {
            "id": "TEST-RULE-1",
            "applies_to": {},
            "trigger": {"condition": "True"},
            "data_source": [],
            "template": "_client_request_generic.md.j2",
            "rationale": "test",
            "severity": "medium",
            "daily_cap_group": "default",
        },
    ]

    monkeypatch.setattr("engine.auto_proposal_engine._load_all_layers", lambda layer_filter=None: sample_rules)
    monkeypatch.setattr("engine.auto_proposal_engine._filter_by_environment", lambda rules, cfg: rules)
    monkeypatch.setattr("engine.auto_proposal_engine._resolve_data_sources", lambda r, c, s: {"client_state": {}, "ad_platform_data": {}, "rule_evaluation": {}})
    monkeypatch.setattr("engine.auto_proposal_engine._evaluate_trigger", lambda r, d, t: True)
    monkeypatch.setattr("engine.auto_proposal_engine._evaluate_skip_if", lambda r, d, t: False)
    monkeypatch.setattr("engine.auto_proposal_engine._check_prerequisite_chain", lambda r, h, s: True)
    monkeypatch.setattr("engine.auto_proposal_engine._check_cooldown", lambda r, h, t: True)
    monkeypatch.setattr("engine.auto_proposal_engine._apply_severity_priority", lambda rs: rs)
    monkeypatch.setattr("engine.auto_proposal_engine._enforce_caps", lambda rs, h, t: rs)
    monkeypatch.setattr("engine.auto_proposal_engine._load_client_cfg", lambda cid: {
        "company": {"name": cid, "honorific": "御中"},
        "chatwork_rooms": {"main": "111"},
        "operating_charter": {"charter_version": "0.1-test"},
    })
    monkeypatch.setattr("engine.auto_proposal_engine.load_client_state", lambda cid: {})
    monkeypatch.setattr("engine.auto_proposal_engine._load_history", lambda cid: {})
    return sample_rules


# ============================================================
# Case 1: dry_run=True
# ============================================================

class TestAutoProposalDryRun:
    def test_dry_run_counts(self, stub_eligible_rules, isolated_history_dir, isolated_client_state_dir):
        """dry_run=True: dry_run_count==attempted_count、sent==0、posted_count==0、history 不変"""
        from engine.auto_proposal_engine import run_auto_proposal

        # _render_and_post を mock: dry_run の戻り値を返す
        def fake_render_and_post(rule, state, client_cfg, dry_run=False):
            return {
                "rule_id": rule["id"],
                "result": {"dry_run": True, "idempotency_key": "fake-key"},
                "body_length": 100,
                "template": rule["template"],
            }

        with mock.patch("engine.auto_proposal_engine._render_and_post", side_effect=fake_render_and_post):
            r = run_auto_proposal("test_client", dry_run=True)

        assert r["attempted_count"] == 1
        assert r["dry_run_count"] == 1
        assert r["sent_count"] == 0
        assert r["skipped_count"] == 0
        assert r["failed_count"] == 0
        assert r["posted_count"] == 0  # 後方互換: sent_count と同値

        # history は dry_run では更新されない
        history_files = list(isolated_history_dir.glob("*.yaml")) if isolated_history_dir.exists() else []
        assert history_files == []


# ============================================================
# Case 2: skipped (idempotency hit)
# ============================================================

class TestAutoProposalSkipped:
    def test_skipped_counts(self, stub_eligible_rules, isolated_history_dir, isolated_client_state_dir):
        """ChatWork が skipped=True を返す: skipped_count++、sent_count=0、history 不変"""
        from engine.auto_proposal_engine import run_auto_proposal

        def fake_render_and_post(rule, state, client_cfg, dry_run=False):
            return {
                "rule_id": rule["id"],
                "result": {"skipped": True, "idempotency_key": "existing-key"},
                "body_length": 100,
                "template": rule["template"],
            }

        with mock.patch("engine.auto_proposal_engine._render_and_post", side_effect=fake_render_and_post):
            r = run_auto_proposal("test_client", dry_run=False)

        assert r["attempted_count"] == 1
        assert r["skipped_count"] == 1
        assert r["sent_count"] == 0
        assert r["dry_run_count"] == 0
        assert r["failed_count"] == 0
        assert r["posted_count"] == 0  # 後方互換

        # skipped でも history は更新されない (5/8 dry-run 副作用ゼロ修正)
        history_files = list(isolated_history_dir.glob("*.yaml")) if isolated_history_dir.exists() else []
        assert history_files == []


# ============================================================
# Case 3: 実送信成功
# ============================================================

class TestAutoProposalSent:
    def test_sent_counts_and_history_updated(self, stub_eligible_rules, isolated_history_dir, isolated_client_state_dir):
        """ChatWork が message_id を返す: sent_count++、posted_count==sent_count、history 更新"""
        from engine.auto_proposal_engine import run_auto_proposal

        def fake_render_and_post(rule, state, client_cfg, dry_run=False):
            return {
                "rule_id": rule["id"],
                "result": {"message_id": "12345"},
                "body_length": 100,
                "template": rule["template"],
            }

        with mock.patch("engine.auto_proposal_engine._render_and_post", side_effect=fake_render_and_post):
            r = run_auto_proposal("test_client", dry_run=False)

        assert r["attempted_count"] == 1
        assert r["sent_count"] == 1
        assert r["skipped_count"] == 0
        assert r["dry_run_count"] == 0
        assert r["failed_count"] == 0
        assert r["posted_count"] == r["sent_count"]  # 後方互換: 同値

        # 実送信成功 → history 更新される
        history_path = isolated_history_dir / "test_client.yaml"
        assert history_path.exists(), "history が更新されていない"
        history = yaml.safe_load(history_path.read_text(encoding="utf-8")) or {}
        assert "TEST-RULE-1" in history
        assert history["TEST-RULE-1"]["last_sent_at"]


# ============================================================
# Case 4: 例外 (failed)
# ============================================================

class TestAutoProposalFailed:
    def test_failed_counts(self, stub_eligible_rules, isolated_history_dir, isolated_client_state_dir):
        """_render_and_post が例外を投げる: failed_count++、history 不変"""
        from engine.auto_proposal_engine import run_auto_proposal

        def fake_render_and_post(rule, state, client_cfg, dry_run=False):
            raise RuntimeError("ChatWork API down")

        with mock.patch("engine.auto_proposal_engine._render_and_post", side_effect=fake_render_and_post):
            r = run_auto_proposal("test_client", dry_run=False)

        assert r["attempted_count"] == 1
        assert r["failed_count"] == 1
        assert r["sent_count"] == 0
        assert r["skipped_count"] == 0
        assert r["dry_run_count"] == 0
        assert r["posted_count"] == 0

        history_files = list(isolated_history_dir.glob("*.yaml")) if isolated_history_dir.exists() else []
        assert history_files == []
