"""ChatWork 回答パーサ (5/8 v3 ingestion + 5/7 P3 Bot-filter)

責務: ChatWork 返信メッセージから (rule_id, answer_code, answer_label, status) を抽出。
      Bot 自身の自動通知本文は回答として取り込まない (誤回答保存防止)。

対応フォーマット (柔軟):
    - "F-AH-04 A"
    - "F-AH-04: A"
    - "F-AH-04：A"           (全角コロン)
    - "A F-AH-04"
    - "F-AH-04 認証済み"     (label 直接)
    - "F-AH-04 → A"
    - "F-LC-10 詳細"          (intent fallback → wants_help)
    - "F-AH-04 相談したい"   (intent fallback → wants_help)
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
    - parse_messages_bulk(messages, rule_messaging, bot_account_ids=None) -> list[ParsedAnswer]
    - is_bot_message(msg, bot_account_ids=None) -> bool

責務境界 (Phase 1 / Phase 2):
    Phase 1 (本実装): 構造的回答 (regex) + 軽量 intent fallback
        - "F-AH-04 A" / "F-AH-04 認証済み" → regex で確定
        - "F-LC-10 詳細" / "相談したい" → INTENT_WANTS_HELP_KEYWORDS で wants_help
    Phase 2 (将来): 自由記述の自然文を Claude API で分類
        - "Pixel が見つからないんですが何これ？" 系の自然文
        - Claude が rule_id / intent / status / reply_draft を返す
        - 本 module は parse_message で None を返したケースを Claude に投げる
          設計境界として残してある (parse_message の戻り値 [] が将来 Claude 経路へ)
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
#
# 判定順序 (上から優先) — 5/8 P2 修正:
#   1. wants_help    : 「検討したい / 確認したい / 相談」を含む系は支援要求
#                       (例: 「未活用、検討したい」は not_done ではなく wants_help)
#   2. not_applicable: 「対応不要 / 適用外 / 活用予定なし」は除外希望
#   3. confirmed_done: 「済」「全て」「明示」「取得済」等の完了表現
#   4. not_done      : 上記いずれも当たらない「未対応」系
#
# Python 3.7+ では dict は挿入順を保持。判定は順次 LABEL_TO_STATUS_KEYWORDS の
# キーを上から走査するため、wants_help を not_done より先に置くことで
# 「未活用、検討したい」が wants_help に正しく分類される。
# 5/7 P3: Bot 自身の自動通知本文を「回答」として誤取り込みしないための判定ワード。
# どれか 1 つでも本文に含まれていれば、Zynect Auto-Reporter 自身の投稿とみなして
# parse_messages_bulk から除外する。
# 顧客返信に偶然これらを含めるケースを避けるため、自動通知のみが必ず使う特徴的な
# 文字列だけに絞っている (顧客が引用するリスクの高い rule_id 単独は含めない)。
BOT_BODY_MARKERS = (
    "[info][title]",
    "[/info]",
    "本日の広告成果改善TODO",
    "本通知は Zynect Auto-Reporter",
    "▼ ご回答",
    "─────────────────────",
    "━━━━━━━━━━━━━━━━━━━━━━━━━━━",
    "🔴 今日確認してほしいこと",
    "🟡 今週中に確認したいこと",
)

# 5/7 P3: 構造的回答が無い場合の intent fallback。
# "F-LC-10 詳細" / "F-AH-04 相談したい" 等は wants_help として取り込む (Phase 1)。
# Phase 2 では Claude API で自然文分類に置換予定。
INTENT_WANTS_HELP_KEYWORDS = (
    "詳細",       # "詳細"単独 / "詳細を" / "詳細案内"
    "もっと知りたい",
    "もっと詳しく",
    "詳しく",
    "相談",       # "相談したい" / "別途相談"
    "教えて",     # "教えてください"
    "わからない",
    "わかりません",
    "案内ほしい",
    "案内してください",
)


LABEL_TO_STATUS_KEYWORDS = {
    "wants_help": [
        "確認したい", "検討したい", "状況不明", "現状未確認",
        "別途相談", "支援してほしい", "詳細を", "次回確認",
    ],
    "not_applicable": [
        "活用予定なし", "対応不要", "該当なし", "適用外", "対象外",
    ],
    "confirmed_done": [
        "認証済み", "ハッシュ化済", "全項目表示済", "全て確認", "全て根拠あり",
        "明示済", "明記済", "取得済", "範囲内のみ", "問題なし",
        "整合済み", "活用中",
        # 末尾に置く幅広い "済" — 上記キーワードで絞った後の残漁
        "済",
    ],
    "not_done": [
        "未対応", "未設定", "未活用", "平文送信中", "一部不足", "未確認",
        "一部根拠不足", "一部のみ", "一部該当", "一部未確認",
        "届かない", "見直したい",
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


def is_bot_message(msg: dict, bot_account_ids: Optional[set] = None) -> bool:
    """この ChatWork メッセージは Bot 自身の自動通知か?

    Bot 判定は 2 軸の OR:
      1. msg.account.account_id が bot_account_ids に含まれる (確定的)
      2. body に BOT_BODY_MARKERS のいずれかを含む (本文ベース、保険)

    どちらかでも該当すれば True (= 回答取り込み対象から除外)。

    bot_account_ids が None の場合は 2 のみで判定 (config 未設定でも誤取り込み防止)。
    """
    if not msg:
        return False

    # 1. account_id 一致 (config 経路)
    if bot_account_ids:
        try:
            acc = msg.get("account") or {}
            acc_id = acc.get("account_id")
            if acc_id is not None and int(acc_id) in {int(x) for x in bot_account_ids}:
                return True
        except (TypeError, ValueError):
            pass

    # 2. body に Bot 自動通知 marker を含む (保険、config 不在でも効く)
    body = msg.get("body", "") or ""
    for marker in BOT_BODY_MARKERS:
        if marker in body:
            return True

    return False


def parse_messages_bulk(
    messages: list[dict], rule_messaging: dict,
    bot_account_ids: Optional[set] = None,
) -> list[ParsedAnswer]:
    """複数の ChatWork メッセージをまとめてパース (Bot 投稿は除外)

    Args:
        messages: ChatWork API の messages 配列
                  各要素: {"message_id", "body", "send_time", "account": {...}}
        rule_messaging: load_messaging() の戻り値
        bot_account_ids: Bot 自身の account_id 集合 (None なら本文 marker のみで判定)

    Returns:
        全 ParsedAnswer のリスト (Bot 自動通知を含むメッセージは skip)
    """
    out: list[ParsedAnswer] = []
    skipped_bot = 0
    for msg in messages or []:
        if is_bot_message(msg, bot_account_ids=bot_account_ids):
            skipped_bot += 1
            continue
        body = msg.get("body", "") or ""
        msg_id = str(msg.get("message_id", ""))
        send_time = msg.get("send_time")
        answered_at = _send_time_to_iso(send_time)
        out.extend(parse_message(body, rule_messaging, chatwork_message_id=msg_id, answered_at=answered_at))
    if skipped_bot:
        log.info(f"chatwork_response_parser: Bot 自動通知 {skipped_bot} 件をスキップ")
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

    # 1a. label の完全マッチ (例: "F-AH-04 認証済み")
    for code, label in action_options.items():
        if label and label in segment:
            status = _map_label_to_status(label)
            return (code, label, status)

    # 1b. (5/8 P2 follow-up) 句読点で分割した部分キーワードでマッチ
    # 例: action_options.B = "未活用、検討したい" のとき、segment に「検討したい」だけ
    # 書かれていても hit させる。
    for code, label in action_options.items():
        if not label:
            continue
        # label を「、」「,」で分割し、各サブキーワードが segment に含まれるか確認
        for kw in re.split(r"[、,]", label):
            kw = kw.strip()
            if not kw or len(kw) < 3:   # 短すぎるキーワードは誤マッチの元 (3 文字未満は除外)
                continue
            if kw in segment:
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

    # 3. (5/7 P3) intent fallback — 明示 code/label が無くても自由記述の wants_help
    # 系キーワードを含めば「詳細案内希望」として取り込む。Phase 2 で Claude API
    # 自然文分類に置換予定 (boundary: 構造的回答=regex, 自由記述=Claude)。
    for kw in INTENT_WANTS_HELP_KEYWORDS:
        if kw in segment:
            return ("?", "詳細案内希望", "wants_help")

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
