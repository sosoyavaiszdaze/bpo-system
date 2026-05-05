"""AdTruth Blocker — 媒体別ブロック実行 (ADR-006 + ADR-009 TO-12)

責務: 不正トラフィックのブロック実行を媒体別に統一インターフェースで提供。

実装スコープ (5/5 中):
    - インターフェース + ロジック骨格
    - 媒体ごとのブロック手段の差を吸収する抽象化
    - モック動作 (実 API 呼出は Phase B Week 2-3)

Phase B Week 2-3 で接続:
    - Meta Marketing API: Custom Audience exclusion (CAPI hash)
    - Google Ads API: IP 除外 (最大 500) / キーワード除外 / プレースメント
    - TikTok Business API: Custom Audience exclusion
    - LP cloaking page (媒体非依存)
"""
from __future__ import annotations

import hashlib
import logging
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Optional

log = logging.getLogger("bpo")


# ========== Base Interface ==========

class BaseBlocker(ABC):
    """媒体別ブロッカーの抽象基底クラス"""

    media: str = "base"

    @abstractmethod
    def block_user_segment(self, client_id: str, user_signals: list[dict]) -> dict:
        """ユーザシグナル (email/phone hash 等) のセグメントをブロック"""
        ...

    @abstractmethod
    def block_placement(self, client_id: str, placement_id: str) -> dict:
        """特定プレースメント (Audience Network 等) を除外"""
        ...

    @abstractmethod
    def block_ip_range(self, client_id: str, ip_list: list[str]) -> dict:
        """IP アドレスリストをブロック"""
        ...

    @abstractmethod
    def pause_ad_set(self, client_id: str, ad_set_id: str) -> dict:
        """広告セットを一時停止"""
        ...

    def _hash_pii(self, value: str) -> str:
        """email / phone を SHA256 ハッシュ化 (CAPI / Customer Audience 形式)"""
        normalized = (value or "").strip().lower()
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


# ========== Meta Blocker ==========

class MetaBlocker(BaseBlocker):
    """Meta 広告のブロック実行

    主要手段:
        - Custom Audience exclusion (CAPI hash)
        - Audience Network 除外
        - プレースメント除外
    非対応:
        - IP 除外 (Meta はサポートなし)
    """

    media = "meta"

    def block_user_segment(self, client_id: str, user_signals: list[dict]) -> dict:
        """Custom Audience に exclusion として追加 (PII は SHA256 ハッシュ化)"""
        hashed = []
        for sig in user_signals:
            if sig.get("email"):
                hashed.append({"em": self._hash_pii(sig["email"])})
            if sig.get("phone"):
                hashed.append({"ph": self._hash_pii(sig["phone"])})

        # Phase B Week 2-3 で実 API 呼出 (Meta Marketing API custom_audiences/{id}/users)
        log.info(f"[meta_blocker] {client_id}: would block {len(hashed)} users via Custom Audience exclusion")
        return {
            "media": "meta",
            "method": "custom_audience_exclusion",
            "blocked_count": len(hashed),
            "executed": False,  # Phase B で True 化
            "executed_at": datetime.now().isoformat(timespec="seconds"),
            "client_id": client_id,
        }

    def block_placement(self, client_id: str, placement_id: str) -> dict:
        """プレースメント除外 (例: audience_network)"""
        log.info(f"[meta_blocker] {client_id}: would exclude placement '{placement_id}'")
        return {
            "media": "meta",
            "method": "placement_exclusion",
            "placement_id": placement_id,
            "executed": False,
            "executed_at": datetime.now().isoformat(timespec="seconds"),
            "client_id": client_id,
        }

    def block_ip_range(self, client_id: str, ip_list: list[str]) -> dict:
        raise NotImplementedError("Meta does not support IP range exclusion")

    def pause_ad_set(self, client_id: str, ad_set_id: str) -> dict:
        log.info(f"[meta_blocker] {client_id}: would pause ad_set {ad_set_id}")
        return {
            "media": "meta",
            "method": "pause_ad_set",
            "ad_set_id": ad_set_id,
            "executed": False,
            "client_id": client_id,
        }


# ========== Google Blocker ==========

class GoogleBlocker(BaseBlocker):
    """Google 広告のブロック実行

    主要手段:
        - IP 除外 (最大 500 件、campaign-level)
        - キーワード除外
        - プレースメント除外
        - オーディエンス除外
    """

    media = "google"
    IP_EXCLUSION_MAX = 500

    def block_user_segment(self, client_id: str, user_signals: list[dict]) -> dict:
        """Customer Match の exclusion list に追加"""
        hashed = []
        for sig in user_signals:
            if sig.get("email"):
                hashed.append({"em": self._hash_pii(sig["email"])})

        log.info(f"[google_blocker] {client_id}: would block {len(hashed)} users via Customer Match exclusion")
        return {
            "media": "google",
            "method": "customer_match_exclusion",
            "blocked_count": len(hashed),
            "executed": False,
            "client_id": client_id,
        }

    def block_placement(self, client_id: str, placement_id: str) -> dict:
        log.info(f"[google_blocker] {client_id}: would exclude placement '{placement_id}'")
        return {
            "media": "google",
            "method": "placement_exclusion",
            "placement_id": placement_id,
            "executed": False,
            "client_id": client_id,
        }

    def block_ip_range(self, client_id: str, ip_list: list[str]) -> dict:
        """IP 除外 (campaign-level、最大 500 件)"""
        if len(ip_list) > self.IP_EXCLUSION_MAX:
            log.warning(
                f"[google_blocker] {client_id}: IP list exceeds max {self.IP_EXCLUSION_MAX}, truncating"
            )
            ip_list = ip_list[: self.IP_EXCLUSION_MAX]

        log.info(f"[google_blocker] {client_id}: would block {len(ip_list)} IPs")
        return {
            "media": "google",
            "method": "ip_exclusion",
            "blocked_count": len(ip_list),
            "executed": False,
            "client_id": client_id,
        }

    def pause_ad_set(self, client_id: str, ad_set_id: str) -> dict:
        # Google では ad_group に該当
        log.info(f"[google_blocker] {client_id}: would pause ad_group {ad_set_id}")
        return {
            "media": "google",
            "method": "pause_ad_group",
            "ad_set_id": ad_set_id,
            "executed": False,
            "client_id": client_id,
        }


# ========== TikTok Blocker ==========

class TikTokBlocker(BaseBlocker):
    """TikTok 広告のブロック実行

    主要手段:
        - Custom Audience exclusion
        - プレースメント除外
    非対応:
        - IP 除外 (TikTok はサポートなし)
    """

    media = "tiktok"

    def block_user_segment(self, client_id: str, user_signals: list[dict]) -> dict:
        hashed = []
        for sig in user_signals:
            if sig.get("email"):
                hashed.append({"em": self._hash_pii(sig["email"])})

        log.info(f"[tiktok_blocker] {client_id}: would block {len(hashed)} users via Custom Audience exclusion")
        return {
            "media": "tiktok",
            "method": "custom_audience_exclusion",
            "blocked_count": len(hashed),
            "executed": False,
            "client_id": client_id,
        }

    def block_placement(self, client_id: str, placement_id: str) -> dict:
        log.info(f"[tiktok_blocker] {client_id}: would exclude placement '{placement_id}'")
        return {
            "media": "tiktok",
            "method": "placement_exclusion",
            "placement_id": placement_id,
            "executed": False,
            "client_id": client_id,
        }

    def block_ip_range(self, client_id: str, ip_list: list[str]) -> dict:
        raise NotImplementedError("TikTok does not support IP range exclusion")

    def pause_ad_set(self, client_id: str, ad_set_id: str) -> dict:
        log.info(f"[tiktok_blocker] {client_id}: would pause ad_group {ad_set_id}")
        return {
            "media": "tiktok",
            "method": "pause_ad_group",
            "ad_set_id": ad_set_id,
            "executed": False,
            "client_id": client_id,
        }


# ========== LP Blocker (媒体非依存・最終防衛) ==========

class LPBlocker:
    """LP 側での cloaking page 表示 (AdTruth タグ連携、媒体非依存)

    Phase B Week 2-3 で AdTruth (papa-torb/adtruth fork) タグ + 独自カスタムで実装。
    本ファイルでは骨格のみ。
    """

    def cloak_user(self, client_id: str, user_signature: dict) -> dict:
        """LP 訪問時、不正シグナルが高ければ cloaking page を表示"""
        log.info(f"[lp_blocker] {client_id}: would cloak user {user_signature.get('id', '?')}")
        return {
            "method": "lp_cloaking",
            "user_id": user_signature.get("id"),
            "executed": False,  # Phase B Week 2-3 で True 化
            "client_id": client_id,
        }


# ========== Factory ==========

def get_blocker(media: str) -> BaseBlocker:
    """媒体名から適切な Blocker インスタンスを返す"""
    blockers = {
        "meta": MetaBlocker,
        "google": GoogleBlocker,
        "tiktok": TikTokBlocker,
    }
    cls = blockers.get(media.lower())
    if not cls:
        raise ValueError(f"Unknown media: {media}. Supported: {list(blockers.keys())}")
    return cls()
