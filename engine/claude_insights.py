"""v3 Claude API 統合 — 層3: 定性推論。

設計: docs/report_design/v3_content_strategy.md

責務:
    1. 顧客語への翻訳（専門用語 → 平易な日本語）
    2. アクションのナラティブ生成（なぜ重要か / 実行手順）
    3. Zynect Insights（10原則ベース独自視点、3-5件）
    4. エグゼクティブサマリ3行要約

設計原則:
    - ANTHROPIC_API_KEY 未設定時は全テンプレート代替で動作（人間レビュー前提）
    - temperature=0.2（再現性優先）、Zynect Insights のみ 0.5
    - プロンプトキャッシング（システムプロンプト + ルール定義）で約 70% コスト削減
    - 全リクエストを logs/llm_audit/{client_id}/{date}.json に保存（再現性確保）
    - 月額予算 ¥30,000 上限、超過時は自動でテンプレート代替に切替
    - 全 API リクエストを logs/llm_cost/{date}.json に記録
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any

log = logging.getLogger("bpo")

BASE_DIR = Path(__file__).resolve().parent.parent
LOGS_DIR = BASE_DIR / "logs"
LLM_AUDIT_DIR = LOGS_DIR / "llm_audit"
LLM_COST_DIR = LOGS_DIR / "llm_cost"

DEFAULT_MODEL = "claude-sonnet-4-6"
PREMIUM_MODEL = "claude-opus-4-7"

# Sonnet 4.6 / Opus 4.7 料金（USD per 1M tokens、2026-05時点の参考値）
PRICE_USD_PER_MTOK = {
    "claude-sonnet-4-6": {"input": 3.0, "output": 15.0, "cache_read": 0.3, "cache_write": 3.75},
    "claude-opus-4-7": {"input": 15.0, "output": 75.0, "cache_read": 1.5, "cache_write": 18.75},
}
USD_TO_JPY = 150  # 為替仮定値

MONTHLY_BUDGET_JPY = 30000  # 月額上限
SYSTEM_PROMPT = """あなたは Zynect Media の広告運用コンサルタントです。
顧客向けレポートに記載する文章を生成します。

以下の制約を厳守してください:
1. 専門用語は初出時に必ず括弧書きで平易な説明を併記
   例: フリークエンシー（同一ユーザーへの広告表示回数）
2. 「重要です」「ご注意ください」など曖昧な強調は使わない
3. 数値は必ず根拠データから引用し、推測値は「見込み」と明記
4. 米満氏理論の原則に紐付ける場合は原則ID（P1〜P9 / M-α〜M-λ）を併記
5. 文体: です・ます調 / 簡潔 / 1文40字以下
6. 出力は必ず JSON 形式
"""


class CostTracker:
    """API コストを追跡し、月額予算超過を検知する。"""

    def __init__(self, monthly_budget_jpy: float = MONTHLY_BUDGET_JPY):
        self.monthly_budget_jpy = monthly_budget_jpy
        self.session_jpy = 0.0
        self.session_calls = 0
        self.session_input_tok = 0
        self.session_output_tok = 0

    def estimate_cost_jpy(self, model: str, input_tok: int, output_tok: int, cached_tok: int = 0) -> float:
        prices = PRICE_USD_PER_MTOK.get(model, PRICE_USD_PER_MTOK[DEFAULT_MODEL])
        # キャッシュヒット部分は cache_read 単価
        non_cached_input = max(0, input_tok - cached_tok)
        usd = (
            non_cached_input * prices["input"] / 1_000_000
            + cached_tok * prices["cache_read"] / 1_000_000
            + output_tok * prices["output"] / 1_000_000
        )
        return usd * USD_TO_JPY

    def record(self, client_id: str, model: str, input_tok: int, output_tok: int, cached_tok: int = 0):
        cost = self.estimate_cost_jpy(model, input_tok, output_tok, cached_tok)
        self.session_jpy += cost
        self.session_calls += 1
        self.session_input_tok += input_tok
        self.session_output_tok += output_tok

        # logs/llm_cost/{date}.json への追記
        LLM_COST_DIR.mkdir(parents=True, exist_ok=True)
        log_path = LLM_COST_DIR / f"{datetime.now():%Y-%m-%d}.json"
        existing: list = []
        if log_path.exists():
            try:
                with log_path.open("r", encoding="utf-8") as f:
                    existing = json.load(f)
            except (json.JSONDecodeError, OSError):
                existing = []
        existing.append(
            {
                "ts": datetime.now().isoformat(),
                "client_id": client_id,
                "model": model,
                "input_tok": input_tok,
                "output_tok": output_tok,
                "cached_tok": cached_tok,
                "estimated_cost_jpy": round(cost, 2),
            }
        )
        with log_path.open("w", encoding="utf-8") as f:
            json.dump(existing, f, ensure_ascii=False, indent=2)

    def monthly_total_jpy(self) -> float:
        """当月のコスト合計を集計"""
        if not LLM_COST_DIR.exists():
            return 0.0
        total = 0.0
        prefix = datetime.now().strftime("%Y-%m")
        for path in LLM_COST_DIR.glob(f"{prefix}-*.json"):
            try:
                with path.open("r", encoding="utf-8") as f:
                    rows = json.load(f)
                for row in rows:
                    total += float(row.get("estimated_cost_jpy", 0))
            except (json.JSONDecodeError, OSError, ValueError):
                continue
        return total

    def is_budget_exceeded(self) -> bool:
        return self.monthly_total_jpy() >= self.monthly_budget_jpy


def save_audit_log(client_id: str, kind: str, request: dict, response: Any) -> None:
    """全 API リクエストを logs/llm_audit/{client_id}/{date}.json に保存（追記）。"""
    target_dir = LLM_AUDIT_DIR / client_id
    target_dir.mkdir(parents=True, exist_ok=True)
    log_path = target_dir / f"{datetime.now():%Y-%m-%d}.json"
    existing: list = []
    if log_path.exists():
        try:
            with log_path.open("r", encoding="utf-8") as f:
                existing = json.load(f)
        except (json.JSONDecodeError, OSError):
            existing = []
    existing.append(
        {
            "ts": datetime.now().isoformat(),
            "kind": kind,
            "request": request,
            "response": response,
        }
    )
    with log_path.open("w", encoding="utf-8") as f:
        json.dump(existing, f, ensure_ascii=False, indent=2, default=str)


class ClaudeInsights:
    """Claude API 呼び出しのファサード。

    インスタンス1つで1レポート生成サイクル分の状態（コスト集計）を保持。
    API キー未設定または予算超過時は全てフォールバック文を返す。
    """

    def __init__(self, client_id: str):
        self.client_id = client_id
        self.api_key = os.environ.get("ANTHROPIC_API_KEY")
        self.cost_tracker = CostTracker()
        self.api_available = bool(self.api_key)
        self.fallback_used = False
        self._client = None

        if not self.api_available:
            log.info(f"[{client_id}] ANTHROPIC_API_KEY 未設定 → 全てテンプレート代替で生成")
        elif self.cost_tracker.is_budget_exceeded():
            log.warning(
                f"[{client_id}] 月額予算 ¥{MONTHLY_BUDGET_JPY:,} 超過のためテンプレート代替に切替"
            )
            self.api_available = False

    def _ensure_client(self):
        if self._client is None and self.api_available:
            try:
                from anthropic import Anthropic
                self._client = Anthropic(api_key=self.api_key)
            except ImportError:
                log.warning("anthropic SDK 未インストール → テンプレート代替")
                self.api_available = False
                self._client = None
        return self._client

    def _invoke(
        self,
        kind: str,
        prompt: str,
        model: str = DEFAULT_MODEL,
        temperature: float = 0.2,
        max_tokens: int = 1500,
    ) -> dict | None:
        """Claude API を呼び出し、JSON パースした結果を返す。失敗時は None。"""
        client = self._ensure_client()
        if client is None:
            return None

        try:
            resp = client.messages.create(
                model=model,
                max_tokens=max_tokens,
                temperature=temperature,
                system=[
                    {
                        "type": "text",
                        "text": SYSTEM_PROMPT,
                        "cache_control": {"type": "ephemeral"},
                    },
                ],
                messages=[{"role": "user", "content": prompt}],
            )
        except Exception as e:
            log.error(f"[{self.client_id}] Claude API エラー({kind}): {e}")
            self.fallback_used = True
            return None

        # 使用量記録
        usage = getattr(resp, "usage", None)
        in_tok = getattr(usage, "input_tokens", 0) if usage else 0
        out_tok = getattr(usage, "output_tokens", 0) if usage else 0
        cached_tok = getattr(usage, "cache_read_input_tokens", 0) if usage else 0
        self.cost_tracker.record(self.client_id, model, in_tok, out_tok, cached_tok)

        text = resp.content[0].text if resp.content else ""

        # 監査ログ
        save_audit_log(
            self.client_id,
            kind,
            {"model": model, "temperature": temperature, "prompt": prompt[:1000]},
            {"text": text, "input_tok": in_tok, "output_tok": out_tok},
        )

        # JSON パース
        try:
            # ``` ブロックを除去
            cleaned = text.strip()
            if cleaned.startswith("```"):
                cleaned = cleaned.split("```", 2)[1]
                if cleaned.startswith("json"):
                    cleaned = cleaned[4:].strip()
                if cleaned.endswith("```"):
                    cleaned = cleaned[:-3].strip()
            return json.loads(cleaned)
        except json.JSONDecodeError as e:
            log.warning(f"[{self.client_id}] Claude JSON パース失敗: {e}")
            self.fallback_used = True
            return None

    # ---------- 公開メソッド ----------

    def generate_summary_3lines(self, audit: dict, aggregate: dict, industry_label: str) -> dict:
        """エグゼクティブサマリ3行要約を生成する。"""
        if not self.api_available:
            return self._fallback_summary_3lines(audit, aggregate, industry_label)

        score = audit.get("score", 0)
        grade = audit.get("grade", "F")
        issues = audit.get("issues", []) or []
        critical = [i for i in issues if i.get("severity") == "critical"]
        savings = aggregate.get("total_savings_yen", 0)

        prompt = f"""以下の広告アカウント監査結果から、顧客向けレポートのエグゼクティブサマリを3行で生成してください。

業界: {industry_label}
総合スコア: {score}/100 (Grade {grade})
検出問題: {len(issues)}件 (うち重大 {len(critical)}件)
最大の重大問題: {critical[0].get('issue') if critical else 'なし'}
試算済み月額改善見込み: ¥{savings:,}

出力フォーマット (JSON):
{{
  "line1": "現状診断（業界平均との比較を含む）",
  "line2": "最大の重大問題（具体的な数値を含む）",
  "line3": "改善見込み（金額または改善率）"
}}"""

        result = self._invoke("summary_3lines", prompt, max_tokens=600)
        if result and all(k in result for k in ("line1", "line2", "line3")):
            return result
        return self._fallback_summary_3lines(audit, aggregate, industry_label)

    def _fallback_summary_3lines(self, audit: dict, aggregate: dict, industry_label: str) -> dict:
        score = audit.get("score", 0)
        issues = audit.get("issues", []) or []
        critical = [i for i in issues if i.get("severity") == "critical"]
        savings = aggregate.get("total_savings_yen", 0)
        line1 = f"広告アカウント全体の健康スコアは {score}/100 点。{industry_label} 業界の状況と比較すると改善余地があります。"
        if critical:
            c = critical[0]
            line2 = f"最大の重大問題は {c.get('platform', '')} の {c.get('campaign', '')} における問題（{c.get('issue', '')}）です。"
        else:
            line2 = f"検出された問題は合計 {len(issues)} 件で、重大な問題は確認されていません。"
        if savings > 0:
            line3 = f"優先アクション Top5 を実行することで、月額 ¥{savings:,} の改善見込みです。"
        else:
            line3 = "優先アクション Top5 の実行で運用品質の底上げが期待できます。"
        return {"line1": line1, "line2": line2, "line3": line3, "_fallback": True}

    def generate_action_narrative(self, rule: dict, impact: dict, principle_tag: str = "") -> dict:
        """1アクション分のナラティブ（なぜ重要か / 実行手順）を生成する。"""
        if not self.api_available:
            return self._fallback_action_narrative(rule, impact, principle_tag)

        prompt = f"""以下のルール検出結果から、優先アクション 1 件分の文章を生成してください。

ルールID: {rule.get('id')}
ルール名: {rule.get('name')}
カテゴリ: {rule.get('category')}
重要度: {rule.get('severity')}
原則タグ: {principle_tag}
quick_win: {rule.get('quick_win')}
ルール定義の根拠 (redesign_note): {rule.get('redesign_note', '')[:300]}
expected_impact rationale: {(rule.get('expected_impact') or {{}}).get('rationale', '')[:300]}
試算インパクト: {impact.get('estimated_savings_display', '')}

出力フォーマット (JSON):
{{
  "action_name": "顧客語のアクション名（30字以内）",
  "why_important": "なぜ重要か（80字以内、原則タグを併記）",
  "steps": [
    {{"step": 1, "actor": "[運用代行]", "what": "実施内容", "duration": "5分"}}
  ]
}}"""

        result = self._invoke("action_narrative", prompt, max_tokens=800)
        if result and "action_name" in result:
            return result
        return self._fallback_action_narrative(rule, impact, principle_tag)

    def _fallback_action_narrative(self, rule: dict, impact: dict, principle_tag: str) -> dict:
        rid = rule.get("id", "")
        name = rule.get("name", "")
        category = rule.get("category", "")
        action_name = f"{name}の改善対応"
        why_parts = []
        if principle_tag:
            why_parts.append(f"【{principle_tag}】")
        why_parts.append(rule.get("redesign_note", "")[:80] or "本項目は運用品質に影響します。")
        why_important = "".join(why_parts)
        steps = [
            {"step": 1, "actor": "[運用代行]", "what": f"対象（{category}カテゴリ）の現状確認", "duration": "30分"},
            {"step": 2, "actor": "[運用代行]", "what": f"{name} の是正実施", "duration": "30分"},
            {"step": 3, "actor": "[運用代行]", "what": "改善後の指標を1週間モニタリング", "duration": "週次"},
        ]
        return {
            "action_name": action_name,
            "why_important": why_important,
            "steps": steps,
            "_fallback": True,
        }

    def generate_zynect_insights(
        self, rules: list[dict], detected_principles: dict, max_count: int = 4
    ) -> list[dict]:
        """Zynect Insights セクション用の独自視点を生成する。"""
        if not self.api_available:
            return self._fallback_insights(rules, detected_principles, max_count)

        rule_summary = "\n".join(
            f"- {r['id']} {r.get('name','')} [sev={r.get('severity')}] note={r.get('redesign_note','')[:80]}"
            for r in rules[:8]
        )
        prompt = f"""以下のルール検出結果から、米満氏理論の10原則ベースの独自視点（Zynect Insights）を最大{max_count}件生成してください。

検出ルール一覧:
{rule_summary}

各 Insight は以下の対比構造を必ず含むこと:
- 「他社監査ではこう言われがち」（業界一般）
- 「Zynect ではこう判断する」（米満氏理論ベース）

出力フォーマット (JSON):
{{
  "insights": [
    {{
      "principle_id": "P3",
      "principle_name": "結果指標非依存原則",
      "title": "短いタイトル",
      "industry_view": "他社監査ではこう言われがち（80字以内）",
      "zynect_view": "Zynect ではこう判断する（120字以内）",
      "impact_note": "想定インパクト（40字以内）"
    }}
  ]
}}"""

        result = self._invoke("zynect_insights", prompt, model=PREMIUM_MODEL, temperature=0.5, max_tokens=2000)
        if result and isinstance(result.get("insights"), list):
            return result["insights"][:max_count]
        return self._fallback_insights(rules, detected_principles, max_count)

    def _fallback_insights(self, rules: list[dict], detected_principles: dict, max_count: int) -> list[dict]:
        # ルールの redesign_note ベースで簡易生成
        out = []
        seen_principles = set()
        for r in rules:
            note = r.get("redesign_note", "") or ""
            if not note:
                continue
            principle = r.get("yonemitsu_alignment", "") or ""
            # 簡易的な原則 ID 推定
            pid = "P?"
            for token in ("P1", "P2", "P3", "P4", "P5", "P6", "P7", "P8", "P9", "M-α", "M-β", "M-γ", "M-δ", "M-ε", "M-ζ", "M-η", "M-θ", "M-ι", "M-λ"):
                if token in note:
                    pid = token
                    break
            if pid in seen_principles:
                continue
            seen_principles.add(pid)

            out.append(
                {
                    "principle_id": pid,
                    "principle_name": "（原則名は付録参照）",
                    "title": f"{r.get('id')} {r.get('name','')}",
                    "industry_view": f"一般的には {r.get('name','')} に対して機械的な是正が推奨されがちです。",
                    "zynect_view": note[:200],
                    "impact_note": "学習品質・長期最適化の観点で重要",
                    "_fallback": True,
                }
            )
            if len(out) >= max_count:
                break
        if not out:
            out.append(
                {
                    "principle_id": "P9",
                    "principle_name": "説明責任・判断ログ原則",
                    "title": "判断ロジックの可視化",
                    "industry_view": "月次レポートは数値結果と次月施策を並べる程度。",
                    "zynect_view": "トレードオフ判断のロジック自体をログ化し、なぜその判断をしたかを記録します。",
                    "impact_note": "属人化を防ぎ、運用品質ばらつきを抑制",
                    "_fallback": True,
                }
            )
        return out

    def translate_to_customer_language(self, technical_text: str) -> str:
        """専門用語混じりのテキストを顧客語に翻訳する。短文用。"""
        if not self.api_available or not technical_text:
            return self._fallback_translate(technical_text)

        prompt = f"""次の文章を、広告運用に詳しくない経営者向けに翻訳してください。
専門用語は初出時に括弧書きで平易な説明を併記してください。

原文:
{technical_text}

出力フォーマット (JSON):
{{ "translated": "翻訳後の文章" }}"""

        result = self._invoke("translate", prompt, max_tokens=600)
        if result and "translated" in result:
            return result["translated"]
        return self._fallback_translate(technical_text)

    def _fallback_translate(self, text: str) -> str:
        # 主要用語のみ括弧書きを差し込む簡易代替
        replacements = {
            "フリークエンシー": "フリークエンシー（同一ユーザーへの広告表示回数）",
            "ROAS": "ROAS（広告費用対効果）",
            "CPA": "CPA（顧客獲得単価）",
            "CTR": "CTR（クリック率）",
            "学習データ": "学習データ（AIが配信を最適化するための材料）",
            "学習フェーズ": "学習フェーズ（AIが配信を最適化中の状態）",
        }
        out = text or ""
        for k, v in replacements.items():
            if k in out:
                # 初出のみ置換
                out = out.replace(k, v, 1)
        return out

    # ---------- 集計 ----------
    def session_stats(self) -> dict:
        return {
            "api_available": self.api_available,
            "fallback_used": self.fallback_used,
            "total_calls": self.cost_tracker.session_calls,
            "total_input_tok": self.cost_tracker.session_input_tok,
            "total_output_tok": self.cost_tracker.session_output_tok,
            "estimated_cost_jpy": round(self.cost_tracker.session_jpy, 2),
        }
