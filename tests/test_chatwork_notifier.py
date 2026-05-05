"""ChatWork Notifier モックテスト (ADR-005 / Day 1)

5 ケース:
1. トークン未設定でも import エラーにならない / send_chatwork_message が安全に空dict
2. 投稿成功 (200) → message_id 取得 + idempotency 記録
3. 同一 body の二度目投稿は idempotency でスキップ
4. 5xx → リトライ後に成功
5. ファイル添付 (multipart) が送信される
"""
import io
import json
import os
import sys
import tempfile
from unittest import mock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture
def isolated_sent_log(tmp_path):
    """各テストで独立した sent log を使う"""
    return str(tmp_path / "chatwork_sent.json")


@pytest.fixture
def fake_response_factory():
    """urllib.request.urlopen 用のレスポンスを生成するファクトリ"""

    def _make(payload: dict, status: int = 200):
        body = json.dumps(payload).encode("utf-8")
        resp = mock.MagicMock()
        resp.read.return_value = body
        resp.status = status
        # context manager 対応
        resp.__enter__ = lambda self: self
        resp.__exit__ = lambda self, exc_type, exc_val, exc_tb: None
        return resp

    return _make


class TestChatWorkNoToken:
    """トークン未設定時の安全動作 (ケース1)"""

    def test_import_no_error_without_token(self):
        """モジュール import 時にトークン未設定でも例外にならない"""
        # 環境変数を強制的に削除
        with mock.patch.dict(os.environ, {}, clear=True):
            # 再 import を強制
            if "notifiers.chatwork_notifier" in sys.modules:
                del sys.modules["notifiers.chatwork_notifier"]
            from notifiers import chatwork_notifier  # noqa: F401

    def test_send_returns_empty_when_no_token(self, isolated_sent_log):
        """トークン未設定時は ERROR ログ + 空dict を返し pipeline を落とさない"""
        from notifiers.chatwork_notifier import send_chatwork_message

        with mock.patch.dict(os.environ, {}, clear=True):
            result = send_chatwork_message(
                "test body", room_id="123", api_token=None, dry_run=False
            )
            assert result == {}


class TestChatWorkPostMessage:
    """メッセージ投稿成功 (ケース2)"""

    def test_post_message_success(self, isolated_sent_log, fake_response_factory):
        from notifiers.chatwork_notifier import ChatWorkClient

        client = ChatWorkClient(
            api_token="DUMMY_TOKEN",
            room_id="999",
            sent_log_path=isolated_sent_log,
        )

        fake_resp = fake_response_factory({"message_id": "12345"})
        with mock.patch("urllib.request.urlopen", return_value=fake_resp) as m_open:
            result = client.post_message("こんにちは")

        assert result["message_id"] == "12345"
        # 呼ばれた Request の URL とヘッダーを検証
        called_req = m_open.call_args[0][0]
        assert called_req.full_url.endswith("/rooms/999/messages")
        assert called_req.headers.get("X-chatworktoken") == "DUMMY_TOKEN"
        assert b"body=" in called_req.data
        # 送信記録が残っている
        with open(isolated_sent_log) as f:
            sent = json.load(f)
        assert len(sent) == 1


class TestChatWorkIdempotency:
    """同一 body の再送スキップ (ケース3)"""

    def test_duplicate_skipped(self, isolated_sent_log, fake_response_factory):
        from notifiers.chatwork_notifier import ChatWorkClient

        client = ChatWorkClient(
            api_token="DUMMY",
            room_id="888",
            sent_log_path=isolated_sent_log,
        )
        fake_resp = fake_response_factory({"message_id": "1"})
        with mock.patch("urllib.request.urlopen", return_value=fake_resp) as m_open:
            r1 = client.post_message("同じ本文")
            r2 = client.post_message("同じ本文")  # 二度目はスキップ

        assert r1.get("message_id") == "1"
        assert r2.get("skipped") is True
        # urlopen は1回しか呼ばれていない
        assert m_open.call_count == 1


class TestChatWorkRetry:
    """5xx 後にリトライして成功 (ケース4)"""

    def test_retry_on_5xx_then_success(self, isolated_sent_log, fake_response_factory, monkeypatch):
        import urllib.error

        from notifiers.chatwork_notifier import ChatWorkClient

        # backoff 待機を無効化（テスト高速化）
        monkeypatch.setattr("notifiers.chatwork_notifier.time.sleep", lambda *a, **kw: None)

        client = ChatWorkClient(
            api_token="DUMMY",
            room_id="777",
            sent_log_path=isolated_sent_log,
            max_retries=3,
        )

        fail_err = urllib.error.HTTPError(
            url="http://example", code=503, msg="Service Unavailable",
            hdrs=None, fp=io.BytesIO(b"")
        )
        success_resp = fake_response_factory({"message_id": "ok-after-retry"})
        side_effects = [fail_err, fail_err, success_resp]

        with mock.patch("urllib.request.urlopen", side_effect=side_effects) as m_open:
            result = client.post_message("retryable")

        assert result["message_id"] == "ok-after-retry"
        assert m_open.call_count == 3


class TestChatWorkFileUpload:
    """ファイル添付投稿 (ケース5)"""

    def test_upload_file_multipart(self, isolated_sent_log, fake_response_factory, tmp_path):
        from notifiers.chatwork_notifier import ChatWorkClient

        # テスト用ファイル作成
        f = tmp_path / "report.pdf"
        f.write_bytes(b"%PDF-1.4 fake content")

        client = ChatWorkClient(
            api_token="DUMMY",
            room_id="555",
            sent_log_path=isolated_sent_log,
        )
        fake_resp = fake_response_factory({"file_id": 9999})
        with mock.patch("urllib.request.urlopen", return_value=fake_resp) as m_open:
            result = client.upload_file(str(f), message="月次レポート添付")

        assert result["file_id"] == 9999
        called_req = m_open.call_args[0][0]
        assert called_req.full_url.endswith("/rooms/555/files")
        # multipart boundary が Content-Type に含まれる
        ct = called_req.headers.get("Content-type", "")
        assert ct.startswith("multipart/form-data; boundary=")
        # body にファイル内容と message が含まれる
        assert b"%PDF-1.4 fake content" in called_req.data
        assert "月次レポート添付".encode("utf-8") in called_req.data


class TestChatWorkDryRun:
    """dry_run モードは HTTP を呼ばずに記録のみ"""

    def test_dry_run_no_http(self, isolated_sent_log):
        from notifiers.chatwork_notifier import ChatWorkClient

        client = ChatWorkClient(
            api_token="DUMMY",
            room_id="111",
            sent_log_path=isolated_sent_log,
            dry_run=True,
        )
        with mock.patch("urllib.request.urlopen") as m_open:
            result = client.post_message("dry_run body")

        assert result["dry_run"] is True
        m_open.assert_not_called()
