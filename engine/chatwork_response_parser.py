"""ChatWork 回答パーサ (5/8 v3 ingestion)

責務: ChatWork 返信メッセージから (rule_id, answer_code, answer_label, status) を抽出。

対応フォーマット (柔軟):
    - "F-AH-04 A"
    - "F-AH-04: A"
    - "F-AH-04：A"           (全角コロン)
    - "A F-AH-04"
    - "F-AH-04 認証済み"     (label 直接)
    - "F-AH-04 → A"
    - 複数回答 (1 メッセージ): "F-AH-04 A / F-DG-01 B"
    - 改行区切り複数回答

answer_code → status マッピング (各 rule の rule_messaging.action_options から取得):
    - "対応済"       → confirmed_done
    - "認証済み"      → confirmed_done
    - "ハッシュ化済"  → confirmed_done
    - "活用中"       → confirmed_done
    - "全項目表示済" → confirmed_done
    - "未対応"       → not_done
    - "未設定"       → not_done
    - "平文送信中"   → not_done
    - "未活用、検討したい" → wants_help
    - "確認したい"   → wants_help
    - "状況不明..."  → wants_help
    - "活用予定なし" → not_applicable
    - "対応不要"     → not_applicable

主要 API:
    - parse_message(text, rule_messaging) -> list[ParsedAnswer]
    - parse_messages_bulk(messages, rule_messaging) -> list[ParsedAnswer]
"""
from __future__ import annotations

import logging
import re
from typing import Optional

log = logging.getLogger("bpo")

# rule_id 正規表現: F-XX-XX, P-EF-XX, V-EC-XX, X-PI1, ANO_*, M01-M99, G01-G108, T01-T46
RULE_ID_PATTERN = re.compile(
    r"\b("
    r"F-[A-Z]{2}-\d{1,3}"           # F-AH-04, F-DG-02, F-LC-10
    r"|P-[A-Z]{2}-\d{1,3}"          # P-EF-01
    r"|V-[A-Z]{2}-\d{1,3}"          # V-EC-01
    r"|PC-[A-Z]{2}-\d{1,3}"         # PC-AT-01
    r"|X-[A-Z]+\d*"                 # X-PI1
    r"|ANO_[A-Z_]+"                 # ANO_CPA_SPIKE, ANO_IMPRESSION_DROP
    r"|[GMT]\d{1,3}"                # G01, M62, T46
    r")\b"
)

# answer_code 正規表現 (半角・全角 A-F、最大 6 択想定)
ANSWER_CODE_PATTERN = re.compile(r"(?<![A-Za-z])([A-FＡ-Ｆ])(?![A-Za-z])")

# rule_id と code の境界記号 (空白 / コロン / 矢印)
SEPARATOR_PATTERN = re.compile(r"[\s:：→\-→]+")

# answer_label → status マッピング (rule_messaging.action_options の label を判定)
LABEL_TO_STATUS_KEYWORDS = {
    "confirmed_done": [
        "済", "認証済み", "活用中", "ハッシュ化済", "全項目表示済", "整合済み",
        "範囲内のみ", "問題なし", "全て確認", "全て根拠あり", "明示済", "明記済",
        "取得済", "明示済み",
    ],
    "not_done": [
        "未対応", "未設定", "未活用", "平文送信中", "一部不足", "未確認",
        "一部根拠不足", "一部のみ", "一部該当", "一部未確認",
        "届かない", "見直したい",
    ],
    "wants_help": [
        "確認したい", "状況不明", "現状未確認", "確認したいです",
        "別途相談", "詳細を確認したい", "次回確認したい",
    ],
    "not_applicable": [
        "活用予定なし", "対応不要", "該当なし", "適用外",
    ],
}


# ========== Public API ==========

class ParsedAnswer:
    """1 つの回答を表す軽量データクラス"""
    def __init__(self, rule_id: str, answer_code: str, answer_label: str,
                 status: str, raw_message: str, chatwork_message_id: Optional[str] = None,
                 answered_at: Optional[str] = None):
        self.rule_id = rule_id
        self.answer_code = answer_code
        self.answer_label = answer_label
        self.status = status
        self.raw_message = raw_message
        self.chatwork_message_id = chatwork_message_id
        self.answered_at = answered_at

    def to_dict(self) -> dict:
        return {
            "rule_id":              self.rule_id,
            "answer_code":          self.answer_code,
            "answer_label":         self.answer_label,
            "status":               self.status,
            "raw_message":          self.raw_message,
            "chatwork_message_id":  self.chatwork_message_id,
            "answered_at":          self.answered_at,
            "source":               "chatwork_reply",
        }


def parse_message(
    text: str, rule_messaging: dict,
    chatwork_message_id: Optional[str] = None,
    answered_at: Optional[str] = None,
) -> list[ParsedAnswer]:
    """1 件のメッセージから複数の (rule_id, answer) ペアを抽出

    Args:
        text: ChatWork 返信本文
        rule_messaging: load_messaging() の戻り値
        chatwork_message_id / answered_at: メタ情報 (parsed answer に付与)

    Returns:
        ParsedAnswer のリスト (空ありえる)
    """
    if not text:
        return []

    rules_meta = rule_messaging.get("rules") or {}

    # 1. メッセージ内の全 rule_id を見つける
    rule_matches = list(RULE_ID_PATTERN.finditer(text))
    if not rule_matches:
        return []

    # 2. 改行 / "/" で複数回答に分割した上で、各セグメントから rule_id+code を抽出
    # 「、」は action_options の label に含まれる句読点 (例: "状況不明、確認したい") の
    # 可能性があるので segment 区切りには使わない
    segments = re.split(r"[\n／/]+", text)

    out: list[ParsedAnswer] = []
    seen: set = set()  # 同 message 内の重複 rule_id 排除

    for seg in segments:
        rids = RULE_ID_PATTERN.findall(seg)
        if not rids:
            continue

        # 各 rule_id について、この segment 内の最も近い answer_code または label を探す
        for rid in rids:
            if rid in seen:
                continue
            answer = _extract_answer_for_rule(seg, rid, rules_meta.get(rid) or {})
            if answer is None:
                continue
            code, label, status = answer
            seen.add(rid)
            out.append(ParsedAnswer(
                rule_id=rid, answer_code=code, answer_label=label, status=status,
                raw_message=seg.strip(), chatwork_message_id=chatwork_message_id,
                answered_at=answered_at,
            ))

    return out


def parse_messages_bulk(
    messages: list[dict], rule_messaging: dict,
) -> list[ParsedAnswer]:
    """複数の ChatWork メッセージをまとめてパース

    Args:
        messages: ChatWork API の messages 配列
                  各要素: {"message_id", "body", "send_time", "account": {...}}
        rule_messaging: load_messaging() の戻り値

    Returns:
        全 ParsedAnswer のリスト
    """
    out: list[ParsedAnswer] = []
    for msg in messages or []:
        body = msg.get("body", "") or ""
        msg_id = str(msg.get("message_id", ""))
        send_time = msg.get("send_time")
        answered_at = _send_time_to_iso(send_time)
        out.extend(parse_message(body, rule_messaging, chatwork_message_id=msg_id, answered_at=answered_at))
    return out


# ========== Private ==========

def _extract_answer_for_rule(
    segment: str, rule_id: str, rule_msg: dict,
) -> Optional[tuple]:
    """segment から rule_id に紐づく (answer_code, answer_label, status) を抽出

    優先順位:
        1. action_options の label が segment に含まれる場合 (例: "認証済み")
        2. rule_id を segment から除去した残りで ANSWER_CODE_PATTERN を探す
           (rule_id 内の "F" を answer_code として誤検出するのを防ぐ)
        3. 見つからなければ None

    Returns:
        (answer_code, answer_label, status) tuple または None
    """
    action_options = rule_msg.get("action_options") or {}

    # 1. label による直接マッチ (より具体的なので優先)
    for code, label in action_options.items():
        if label and label in segment:
            status = _map_label_to_status(label)
            return (code, label, status)

    # 2. answer_code 単独 (A/B/C/D/E/F)
    # rule_id を空白に置換して、rule_id 内の文字 (F-AH-04 の F 等) を answer_code として
    # 誤検出するのを防ぐ。同 segment 内で複数の rule_id がある場合は全て除去。
    cleaned = segment
    for rid_match in RULE_ID_PATTERN.findall(segment):
        cleaned = cleaned.replace(rid_match, " ")

    code_match = ANSWER_CODE_PATTERN.search(cleaned)
    if code_match:
        code_raw = code_match.group(1)
        # 全角 → 半角
        code = _normalize_code(code_raw)
        label = action_options.get(code, "")
        status = _map_label_to_status(label) if label else "wants_help"
        return (code, label, status)

    return None


def _normalize_code(code: str) -> str:
    """全角 A-F → 半角"""
    return code.translate(str.maketrans("ＡＢＣＤＥＦ", "ABCDEF"))


def _map_label_to_status(label: str) -> str:
    """answer_label → status マッピング (LABEL_TO_STATUS_KEYWORDS の最初にマッチしたものを採用)"""
    for status, keywords in LABEL_TO_STATUS_KEYWORDS.items():
        for kw in keywords:
            if kw in label:
                return status
    # フォールバック: 不明な label は "wants_help" として担当に振る
    return "wants_help"


def _send_time_to_iso(send_time) -> Optional[str]:
    """ChatWork API の send_time (UNIX epoch) を ISO 文字列に"""
    if not send_time:
        return None
    try:
        from datetime import datetime, timezone, timedelta
        jst = timezone(timedelta(hours=9))
        return datetime.fromtimestamp(int(send_time), tz=jst).isoformat(timespec="seconds")
    except (ValueError, TypeError):
        return None
