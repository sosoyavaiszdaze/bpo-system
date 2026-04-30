"""Claude API 定性分析エンジン — Anthropic API による広告/LP/クリエイティブ分析 + ファイルキャッシュ"""
import os
import json
import logging
import yaml
import time
import hashlib
from datetime import datetime

log = logging.getLogger("bpo")

CONFIG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config")
CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "claude_cache")


def load_model_config():
    """model.yaml 設定読み込み"""
    path = os.path.join(CONFIG_DIR, "model.yaml")
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    return {}


def run_claude_analysis(client_id, data, audit_results):
    """Claude API 定性分析20項目を実行

    Args:
        client_id: クライアントID
        data: unified format データ
        audit_results: ads_audit の結果
    Returns:
        dict: 定性分析結果
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        log.info(f"[{client_id}] ANTHROPIC_API_KEY 未設定、Claude分析スキップ")
        return {"skipped": True, "reason": "API key not configured"}

    config = load_model_config()
    model_cfg = config.get("model", {})
    model = model_cfg.get("primary", "claude-sonnet-4-5-20250514")
    max_retries = model_cfg.get("retry", {}).get("max_attempts", 3)
    backoff_base = model_cfg.get("retry", {}).get("backoff_base", 2)

    try:
        import anthropic
    except ImportError:
        log.warning("anthropic パッケージ未インストール")
        return {"skipped": True, "reason": "anthropic package not installed"}

    client = anthropic.Anthropic(api_key=api_key)

    # 分析対象データの準備
    analysis_context = _build_context(data, audit_results)

    analyses = {}
    analysis_items = [
        ("ad_copy_quality", "広告コピー品質評価"),
        ("campaign_structure", "キャンペーン構造最適化提案"),
        ("budget_allocation", "予算配分最適化"),
        ("audience_strategy", "オーディエンス戦略"),
        ("creative_fatigue", "クリエイティブ疲弊度分析"),
    ]

    cache_key = _build_cache_key(client_id, analysis_context)

    for analysis_key, analysis_name in analysis_items:
        # キャッシュ確認（同日同データならスキップ）
        cached = _load_cache(cache_key, analysis_key)
        if cached is not None:
            analyses[analysis_key] = {"name": analysis_name, "result": cached}
            continue

        prompt = _build_prompt(analysis_key, analysis_context)
        result = _call_claude(client, model, prompt, max_retries, backoff_base)
        analyses[analysis_key] = {
            "name": analysis_name,
            "result": result,
        }
        # 結果をキャッシュ保存
        if result is not None:
            _save_cache(cache_key, analysis_key, result)

    log.info(f"[{client_id}] Claude分析完了: {len(analyses)}項目")

    return {
        "model": model,
        "analyses": analyses,
        "summary": _build_summary(analyses),
    }


def _build_context(data, audit_results):
    """分析コンテキストを構築"""
    campaigns = data.get("campaigns", [])
    return json.dumps({
        "campaigns": [{
            "name": c.get("campaign", ""),
            "platform": c.get("platform", ""),
            "type": c.get("campaign_type", ""),
            "cost": c.get("cost", 0),
            "conversions": c.get("conversions", 0),
            "cpa": c.get("cpa", 0),
            "roas": c.get("roas", 0),
            "ctr": c.get("ctr", 0),
        } for c in campaigns[:20]],
        "score": audit_results.get("score", 0),
        "grade": audit_results.get("grade", "?"),
        "issues_count": len(audit_results.get("issues", [])),
        "top_issues": [i.get("issue", "") for i in audit_results.get("issues", [])[:5]],
    }, ensure_ascii=False, indent=2)


def _load_prompt_config():
    """config/prompts/analysis_prompts.yaml からプロンプト設定を読み込み"""
    path = os.path.join(CONFIG_DIR, "prompts", "analysis_prompts.yaml")
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    return {}


def _build_prompt(analysis_key, context):
    """分析プロンプトを構築（外部YAML優先 → インラインフォールバック）"""
    prompt_cfg = _load_prompt_config()
    prompts_yaml = prompt_cfg.get("prompts", {})

    if analysis_key in prompts_yaml:
        template = prompts_yaml[analysis_key].get("template", "")
        return template.replace("{{ context }}", context)

    # フォールバック
    fallback = {
        "ad_copy_quality": "以下の広告キャンペーンデータを分析し、広告コピーの品質を評価してください。改善提案を3つ挙げてください。日本語で回答。\n\n",
        "campaign_structure": "以下のキャンペーンデータを分析し、構造の最適化提案を行ってください。キャンペーン統合・分割の推奨を含めてください。日本語で回答。\n\n",
        "budget_allocation": "以下のデータから予算配分の最適化を提案してください。ROAS/CPA効率に基づいた再配分案を示してください。日本語で回答。\n\n",
        "audience_strategy": "以下のデータからオーディエンス戦略の改善提案を行ってください。ターゲティングの精度向上案を含めてください。日本語で回答。\n\n",
        "creative_fatigue": "以下のデータからクリエイティブ疲弊の兆候を分析してください。リフレッシュが必要なキャンペーンを特定してください。日本語で回答。\n\n",
    }
    return fallback.get(analysis_key, "以下のデータを分析してください。\n\n") + context


def _load_system_prompt():
    """外部YAMLからシステムプロンプトを読み込み"""
    prompt_cfg = _load_prompt_config()
    return prompt_cfg.get("system", "あなたはデジタル広告の専門アナリストです。データに基づいた具体的な改善提案を行います。")


def _call_claude(client, model, prompt, max_retries, backoff_base):
    """Claude API 呼び出し（リトライ付き）"""
    system_prompt = _load_system_prompt()
    for attempt in range(max_retries):
        try:
            message = client.messages.create(
                model=model,
                max_tokens=1024,
                system=[{
                    "type": "text",
                    "text": system_prompt,
                    "cache_control": {"type": "ephemeral"},
                }],
                messages=[{"role": "user", "content": prompt}],
            )
            return message.content[0].text
        except Exception as e:
            if attempt < max_retries - 1:
                wait = backoff_base ** attempt
                log.warning(f"Claude API リトライ {attempt + 1}/{max_retries}: {e}, {wait}秒待機")
                time.sleep(wait)
            else:
                log.error(f"Claude API 失敗: {e}")
                return None


CACHE_TTL_DAYS = 7
PROMPT_VERSION = "1"


def _build_cache_key(client_id, context):
    """キャッシュキーを生成（client_id + 日付 + コンテキストハッシュ + プロンプトバージョン）"""
    context_hash = hashlib.md5(context.encode()).hexdigest()[:12]
    today = datetime.now().strftime("%Y-%m-%d")
    return f"{client_id}_{today}_{context_hash}_v{PROMPT_VERSION}"


def _load_cache(cache_key, analysis_key):
    """キャッシュからClaude分析結果を読み込み"""
    cache_path = os.path.join(CACHE_DIR, f"{cache_key}_{analysis_key}.json")
    if os.path.exists(cache_path):
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            log.debug(f"Claude cache hit: {analysis_key}")
            return data.get("result")
        except Exception:
            return None
    return None


def _save_cache(cache_key, analysis_key, result):
    """Claude分析結果をキャッシュに保存"""
    os.makedirs(CACHE_DIR, exist_ok=True)
    cache_path = os.path.join(CACHE_DIR, f"{cache_key}_{analysis_key}.json")
    try:
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump({"result": result, "cached_at": datetime.now().isoformat()},
                      f, ensure_ascii=False)
    except Exception as e:
        log.debug(f"Claude cache save error: {e}")
    # TTL超過の古いキャッシュを削除
    _cleanup_cache()


def _cleanup_cache():
    """TTL超過のキャッシュファイルを削除"""
    if not os.path.exists(CACHE_DIR):
        return
    now = datetime.now()
    try:
        for f in os.listdir(CACHE_DIR):
            path = os.path.join(CACHE_DIR, f)
            if not f.endswith(".json"):
                continue
            mtime = datetime.fromtimestamp(os.path.getmtime(path))
            if (now - mtime).days > CACHE_TTL_DAYS:
                os.remove(path)
                log.debug(f"Claude cache expired: {f}")
    except Exception:
        pass


def _build_summary(analyses):
    """分析結果のサマリーを生成"""
    summaries = []
    for key, analysis in analyses.items():
        result = analysis.get("result")
        if result is None:
            summaries.append(f"**{analysis['name']}**: 分析エラー（API応答なし）")
            continue
        lines = result.strip().split("\n")[:2]
        summaries.append(f"**{analysis['name']}**: {' '.join(lines)}")
    return "\n".join(summaries)
