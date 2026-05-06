"""ChatWork オンボーディング自動質問 (ADR-015 §2.5 — 枠組みのみ、Phase A 5/7)

責務: tech_stack で confidence:low / source:pending_hearing のカテゴリを抽出し、
      A/B/C/D/E/F 形式の質問を ChatWork に送信。回答パースと clients.yaml 反映は
      Phase A 5/8 以降に運用本格化する。

実装スコープ (5/7 時点):
    - 質問送信フェーズ: build_questions(), post_pending_questions() 完備
    - 回答取得フェーズ: skeleton (ChatWork API messages 取得 + 簡易パーサ)
    - clients.yaml 更新: skeleton (atomic write の枠組みのみ)

Phase A 5/8 以降で本格運用する箇所:
    - 回答パターン正規化 (A/B/C/D/E/F → ツール名)
    - clients.yaml への atomic 反映 (concurrent write 対策)
    - 24-72h 未返信時の催促ロジック
"""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

import yaml

log = logging.getLogger("bpo")

ROOT = Path(__file__).resolve().parent.parent
CLIENTS_PATH = ROOT / "config" / "clients.yaml"

# 各カテゴリの A/B/C/D/E/F 選択肢 (Phase A 5/7 暫定)
QUESTION_CATALOG = {
    "tag_manager": {
        "title": "お使いのタグマネージャーを教えてください",
        "choices": {
            "A": "Google Tag Manager (GTM)",
            "B": "Yahoo!タグマネージャー",
            "C": "Tealium",
            "D": "Adobe Launch / DTM",
            "E": "なし (タグ直書き)",
            "F": "その他 (テキストで返信)",
        },
        "value_map": {
            "A": "gtm", "B": "yahoo_tag_manager", "C": "tealium",
            "D": "adobe_launch", "E": "none",
        },
    },
    "analytics": {
        "title": "お使いのアクセス解析ツールを教えてください (複数可)",
        "choices": {
            "A": "GA4 (Google Analytics 4)",
            "B": "Adobe Analytics",
            "C": "Matomo",
            "D": "Adobe Analytics + GA4 併用",
            "E": "なし",
            "F": "その他 (テキストで返信)",
        },
        "value_map": {
            "A": "ga4", "B": "adobe_analytics", "C": "matomo",
            "D": ["adobe_analytics", "ga4"], "E": "none",
        },
    },
    "ma": {
        "title": "お使いの MA (マーケティングオートメーション) ツールを教えてください",
        "choices": {
            "A": "HubSpot", "B": "Marketo", "C": "Pardot (Salesforce)",
            "D": "KARTE", "E": "b→dash", "F": "なし / その他 (テキストで返信)",
        },
        "value_map": {
            "A": "hubspot", "B": "marketo", "C": "pardot",
            "D": "karte", "E": "bdash",
        },
    },
    "crm": {
        "title": "お使いの CRM を教えてください",
        "choices": {
            "A": "Salesforce", "B": "HubSpot", "C": "kintone",
            "D": "自社開発", "E": "なし", "F": "その他 (テキストで返信)",
        },
        "value_map": {
            "A": "salesforce", "B": "hubspot", "C": "kintone",
            "D": "custom", "E": "none",
        },
    },
    "cdp": {
        "title": "お使いの CDP / DMP を教えてください",
        "choices": {
            "A": "Treasure Data", "B": "Segment", "C": "Tealium AudienceStream",
            "D": "自社 DB", "E": "なし", "F": "その他 (テキストで返信)",
        },
        "value_map": {
            "A": "treasure_data", "B": "segment", "C": "tealium_audiencestream",
            "D": "custom", "E": "none",
        },
    },
    "ab_testing": {
        "title": "お使いの A/B テストツールを教えてください",
        "choices": {
            "A": "VWO", "B": "Optimizely", "C": "KARTE Blocks",
            "D": "なし", "E": "その他 (テキストで返信)",
        },
        "value_map": {
            "A": "vwo", "B": "optimizely", "C": "karte_blocks", "D": "none",
        },
    },
    "chatbot": {
        "title": "お使いのチャットボット / カスタマーサポートツールを教えてください",
        "choices": {
            "A": "Intercom", "B": "Zendesk Chat", "C": "Channel Talk",
            "D": "なし", "E": "その他 (テキストで返信)",
        },
        "value_map": {
            "A": "intercom", "B": "zendesk", "C": "channel_talk", "D": "none",
        },
    },
}


# ========== Public API ==========

def build_questions(client_id: str) -> list[dict]:
    """tech_stack で確認が必要なカテゴリを抽出して質問リストを生成"""
    client_cfg = _load_client_cfg(client_id)
    if not client_cfg:
        return []
    tech_stack = client_cfg.get("tech_stack") or {}

    questions = []
    for category, qspec in QUESTION_CATALOG.items():
        entry = tech_stack.get(category)
        if not _needs_question(entry):
            continue
        questions.append({
            "category":   category,
            "title":      qspec["title"],
            "choices":    qspec["choices"],
            "client_id":  client_id,
            "value_map":  qspec["value_map"],
        })
    return questions


def render_question_message(question: dict, client_id: str) -> str:
    """ChatWork 用 1 メッセージにレンダリング (idempotency_key は category 名 + 日付)"""
    company_label = client_id  # 簡略
    today = datetime.now().strftime("%Y-%m-%d")
    lines = [
        f"[info][title]【Zynect オンボーディング 自動質問】{question['title']}[/title]",
        f"[Zynect Auto-Onboarding / {today}]",
        f"{company_label} 御中",
        "",
        "本質問への返信内容を弊社システムに記録し、運用指摘を最適化します。",
        "下記から該当する選択肢の英字 (A〜F) でご返信ください。",
        "",
    ]
    for code, label in question["choices"].items():
        lines.append(f"  [{code}] {label}")
    lines += [
        "",
        f"※ 自動収集対象カテゴリ: {question['category']}",
        f"※ 質問 ID: ONB-{today}-{question['category']}",
        "[/info]",
    ]
    return "\n".join(lines)


def post_pending_questions(client_id: str, dry_run: bool = False) -> dict:
    """confidence:low なカテゴリ全てに 1 質問ずつ ChatWork 送信 (枠組み実装)"""
    questions = build_questions(client_id)
    if not questions:
        return {"client_id": client_id, "questions_built": 0, "posted": 0}

    client_cfg = _load_client_cfg(client_id)
    chatwork_rooms = client_cfg.get("chatwork_rooms") or {}
    room_id = chatwork_rooms.get("main")
    posted = 0

    try:
        from notifiers.chatwork_notifier import ChatWorkClient
    except ImportError:
        log.error("ChatWorkClient import failed; aborting onboarding push")
        return {"client_id": client_id, "questions_built": len(questions), "posted": 0,
                "error": "import_failed"}

    chat = ChatWorkClient(room_id=room_id, dry_run=dry_run)
    today = datetime.now().strftime("%Y-%m-%d")
    for q in questions:
        body = render_question_message(q, client_id)
        idempotency_key = f"onboarding:{client_id}:{q['category']}:{today}"
        result = chat.post_message(body, idempotency_key=idempotency_key)
        if not result.get("skipped"):
            posted += 1
    return {
        "client_id":       client_id,
        "questions_built": len(questions),
        "posted":          posted,
    }


def parse_answer(category: str, message_text: str) -> Optional[str]:
    """ChatWork 返信メッセージから A/B/C/D/E/F を抽出 → ツール名にマッピング (skeleton)

    Phase A 5/7 時点の簡易実装:
      - 先頭 1 文字が A-F なら採用
      - 大文字小文字を吸収
    Phase A 5/8 以降:
      - 全角英字 (Ａ〜Ｆ) 対応
      - 「Aです」等の自然文対応
      - キーワード検出 (「Salesforce」と書かれていれば B 相当)
    """
    if not message_text:
        return None
    spec = QUESTION_CATALOG.get(category)
    if not spec:
        return None
    code = message_text.strip()[:1].upper()
    return spec["value_map"].get(code)


def apply_answer_to_clients_yaml(client_id: str, category: str, value) -> bool:
    """clients.yaml の tech_stack[category] を atomic 更新 (skeleton)

    Phase A 5/7 時点:
      - 単純な書き戻し (concurrent write は単一 launchd ジョブ前提で問題なし)
    Phase A 5/8 以降:
      - file lock (fcntl / lockfile) で並行書き込み対策
      - history バックアップ
    """
    if not client_id or not category:
        return False
    cfg = yaml.safe_load(CLIENTS_PATH.read_text(encoding="utf-8")) or {}
    client = cfg.get("clients", {}).get(client_id)
    if not client:
        return False
    ts = client.setdefault("tech_stack", {})
    if isinstance(value, list):
        ts[category] = value
    else:
        ts[category] = {
            "value": value,
            "confidence": "medium",
            "source": "hearing",
            "last_verified": datetime.now().strftime("%Y-%m-%d"),
        }
    tmp = CLIENTS_PATH.with_suffix(".yaml.tmp")
    tmp.write_text(yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False), encoding="utf-8")
    tmp.replace(CLIENTS_PATH)
    return True


# ========== Helpers ==========

def _load_client_cfg(client_id: str) -> dict:
    cfg = yaml.safe_load(CLIENTS_PATH.read_text(encoding="utf-8")) or {}
    return cfg.get("clients", {}).get(client_id, {}) or {}


def _needs_question(entry) -> bool:
    """tech_stack の単一カテゴリエントリが質問対象かどうか判定"""
    if entry is None:
        return True
    if isinstance(entry, dict):
        if entry.get("source") == "pending_hearing":
            return True
        if entry.get("confidence") in (None, "low"):
            return True
        if entry.get("value") in (None, "unknown"):
            return True
        return False
    if isinstance(entry, list):
        return len(entry) == 0
    if isinstance(entry, str):
        return entry in ("", "unknown")
    return True
