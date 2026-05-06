"""ADR-006/009 AdTruth Blocker (媒体別ブロック実行) のテスト (6 ケース)"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.adtruth_blocker import (
    BaseBlocker, MetaBlocker, GoogleBlocker, TikTokBlocker, LPBlocker,
    get_blocker,
)


class TestBlockerFactory:
    def test_get_blocker_meta(self):
        """get_blocker('meta') で MetaBlocker を返す"""
        b = get_blocker("meta")
        assert isinstance(b, MetaBlocker)
        assert b.media == "meta"

    def test_get_blocker_unknown_media_raises(self):
        """未知の media で ValueError"""
        with pytest.raises(ValueError, match="Unknown media"):
            get_blocker("unknown_platform")


class TestMetaBlocker:
    def test_block_user_segment_hashes_pii(self):
        """email を SHA256 ハッシュ化して exclusion 登録"""
        blocker = MetaBlocker()
        user_signals = [{"email": "user@example.com"}, {"phone": "08012345678"}]
        result = blocker.block_user_segment("test_client", user_signals)
        assert result["media"] == "meta"
        assert result["method"] == "custom_audience_exclusion"
        assert result["blocked_count"] == 2
        assert result["executed"] is False  # Phase A はモック

    def test_block_ip_range_raises_not_implemented(self):
        """Meta は IP 除外非対応 → NotImplementedError"""
        blocker = MetaBlocker()
        with pytest.raises(NotImplementedError, match="Meta does not support IP"):
            blocker.block_ip_range("test", ["1.2.3.4"])


class TestGoogleBlocker:
    def test_block_ip_range_truncates_to_500(self):
        """Google IP 除外は最大 500 件に切り詰め"""
        blocker = GoogleBlocker()
        ip_list = [f"192.168.{i//256}.{i%256}" for i in range(700)]
        result = blocker.block_ip_range("test_client", ip_list)
        assert result["media"] == "google"
        assert result["method"] == "ip_exclusion"
        assert result["blocked_count"] == 500  # truncated


class TestTikTokBlocker:
    def test_tiktok_block_ip_raises(self):
        """TikTok は IP 除外非対応"""
        blocker = TikTokBlocker()
        with pytest.raises(NotImplementedError, match="TikTok does not support IP"):
            blocker.block_ip_range("test", ["1.2.3.4"])
