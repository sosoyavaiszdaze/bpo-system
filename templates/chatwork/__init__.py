"""ChatWork 通知用 Jinja2 テンプレート (ADR-005)

テンプレート構成:
- daily_indication.md.j2          — 日次指摘 (メインエントリ)
- completion_notice.md.j2         — 解消通知
- monthly_report.md.j2            — 月次運用レポート
- _action_steps.md.j2             — rule_id 別改善手順マクロ (層1: 抽象度↑)
- _disclaimer_ai_assist.md.j2     — 共通免責文 + 生成AI 質問導線 (層2)

知識ベース陳腐化対策 (ADR-005 G タスク 3 層):
  層1: _action_steps.md.j2 で「目的＋ゴール状態＋選択肢」記述、UI 変更に強い
  層2: 各指摘末尾に YYYY/MM 入り免責文 + 生成AIへ画面解析を委譲する誘導
  層3: 実装時に WebSearch で公式情報を反映 (本ファイル更新の都度)

Jinja2 globals:
  current_year / current_month  — 投稿日の年月。免責文の YYYY/MM 表示用。
                                   render(context={"current_year": 2027}) で上書き可
"""
import logging
import os
from datetime import datetime

from jinja2 import Environment, FileSystemLoader, StrictUndefined, TemplateNotFound

log = logging.getLogger("bpo")

TEMPLATE_DIR = os.path.dirname(os.path.abspath(__file__))
GENERIC_FALLBACK_TEMPLATE = "_client_request_generic.md.j2"


def get_chatwork_env() -> Environment:
    """ChatWork テンプレート用の Jinja2 環境

    StrictUndefined で未定義変数アクセスを早期検出する。
    current_year / current_month を globals に注入し、_disclaimer_ai_assist マクロで参照。
    """
    env = Environment(
        loader=FileSystemLoader(TEMPLATE_DIR),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=False,
    )
    now = datetime.now()
    env.globals["current_year"] = now.year
    env.globals["current_month"] = now.month
    return env


def render(template_name: str, context: dict) -> str:
    """指定テンプレートをレンダリング

    Args:
        template_name: 'daily_indication.md.j2' 等
        context: テンプレートに渡す辞書
                 - "current_year" / "current_month" を渡せば globals を上書き

    フォールバック:
        指定テンプレートが存在しない場合は _client_request_generic.md.j2 を使用。
        ADR-013 の 248 個の新規ルールが個別テンプレ未整備の段階でも
        rule_name / rationale / legal_reference を埋めた汎用通知を生成する。
    """
    env = get_chatwork_env()
    try:
        tmpl = env.get_template(template_name)
    except TemplateNotFound:
        if template_name == GENERIC_FALLBACK_TEMPLATE:
            raise
        log.warning(
            f"template '{template_name}' not found, falling back to {GENERIC_FALLBACK_TEMPLATE}"
        )
        tmpl = env.get_template(GENERIC_FALLBACK_TEMPLATE)
    return tmpl.render(**context)
